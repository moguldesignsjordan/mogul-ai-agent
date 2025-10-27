import os, io, json
from typing import Dict, Any, List
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from google.cloud import speech_v1p1beta1 as speech
from google.cloud import texttospeech
from openai import OpenAI
from twilio.twiml.voice_response import VoiceResponse, Gather

# === local tools ===
from tools import get_booking_link, lookup_customer, add_note

# ── resolve .env next to main.py
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "America/New_York")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
BASE_PUBLIC_URL = os.getenv("BASE_PUBLIC_URL", "").rstrip("/")  # ex: https://your-ngrok-url.ngrok-free.dev

def _mask(key: str | None) -> str:
    if not key:
        return "None"
    if len(key) <= 8:
        return key[0] + "***" + key[-1]
    return key[:4] + "..." + key[-4:]

print("–––––––– STARTUP DEBUG ––––––––")
print("BASE_DIR:", BASE_DIR)
print(".env path:", ENV_PATH)
print(".env exists?", ENV_PATH.exists())
print("OPENAI_API_KEY loaded?", "yes ✅" if OPENAI_API_KEY else "no ❌")
print("OPENAI_API_KEY masked:", _mask(OPENAI_API_KEY))
print("MODEL:", MODEL)
print("DEFAULT_TZ:", DEFAULT_TZ)
print("ELEVENLABS_API_KEY loaded?", "yes ✅" if ELEVENLABS_API_KEY else "no ❌")
print("ELEVENLABS_API_KEY masked:", _mask(ELEVENLABS_API_KEY))
print("BASE_PUBLIC_URL:", BASE_PUBLIC_URL or "(not set)")
print("–––––––– END STARTUP DEBUG ––––––––")

# === Firestore ===
import firebase_admin
from firebase_admin import credentials, firestore

db = None
if not firebase_admin._apps:
    try:
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if creds_path:
            cred = credentials.Certificate(creds_path)
        else:
            cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firestore initialized")
    except Exception as e:
        print(f"[warn] Firestore disabled: {e}")
        db = None
else:
    db = firestore.client()

def save_chat_to_firestore(user_messages: List[Dict[str, Any]], assistant_message: Dict[str, Any]):
    if not db:
        return
    try:
        doc = {
            "created_at": datetime.utcnow().isoformat() + "Z",
            "model": MODEL,
            "messages": user_messages,
            "assistant_reply": assistant_message,
        }
        db.collection("chat_logs").add(doc)
    except Exception as e:
        print("[warn] failed to write chat log:", e)

async def log_call_message(call_sid: str, role: str, text: str) -> None:
    """Store each phone call turn in Firestore if available."""
    if not db:
        return
    try:
        (
            db.collection("calls")
            .document(call_sid)
            .collection("messages")
            .document()
            .set(
                {
                    "role": role,
                    "text": text,
                    "ts": datetime.utcnow().isoformat() + "Z",
                }
            )
        )
    except Exception as e:
        print("firestore log failed", e)

# === OpenAI client ===
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing. Check apps/api-python/.env")
oai = OpenAI(api_key=OPENAI_API_KEY)

# === Request/response models ===
from models import ChatRequest, ChatResponse

# === Tool setup ===
TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_booking_link",
            "description": "Return the public booking link for a 30-minute call with Jordan.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": "Check Firestore for an existing customer record by email or phone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Attach a summary of this conversation to an existing Firestore customer record.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "customer_id": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["conversation_id", "customer_id", "summary"],
            },
        },
    },
]

TOOL_IMPL = {
    "get_booking_link": get_booking_link,
    "lookup_customer": lookup_customer,
    "add_note": add_note,
}

# === System prompt (shared across web chat + phone) ===
SYSTEM_PROMPT = (
    "You are Mogul Design Agency's helpful customer service assistant. "
    "Your job is to qualify leads, answer questions, and smoothly move callers toward booking.\n\n"
    "STYLE:\n"
    "- Sound confident, calm, human, and natural.\n"
    "- Keep answers conversational, not too long, like you're on a real call.\n"
    "- Ask clarifying questions instead of guessing.\n"
    "- If they're asking about working with us, gather name + email and offer to book a call.\n"
    "- If they're in a rush or money is on the line, reassure them calmly and keep them talking.\n\n"
    "BOOKING FLOW:\n"
    "1) If they ask to talk, meet, call, schedule, book time, or check availability, assume they want to schedule a call.\n"
    "2) Ask for their full name first, then ask for email.\n"
    "3) After you have name + email, you can call `get_booking_link` to give them the scheduling link.\n"
    "4) Never invent data.\n"
)

# === Tool helpers ===
async def _run_one_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    fn = TOOL_IMPL.get(name)
    if fn is None:
        return {"error": "unknown_tool"}
    if name == "lookup_customer":
        # require at least email or phone
        email, phone = args.get("email"), args.get("phone")
        if not email and not phone:
            return {"error": "provide_email_or_phone"}
        return await fn(email=email, phone=phone)
    return await fn(**args)

def _assistant_tool_call_dict(msg_obj) -> dict:
    calls = []
    for tc in (msg_obj.tool_calls or []):
        calls.append(
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
        )
    return {"role": "assistant", "tool_calls": calls}

async def _apply_tool_calls(base_msgs: List[dict], msg_obj) -> List[dict]:
    tool_msgs: List[dict] = []
    assistant_tool = _assistant_tool_call_dict(msg_obj)

    for tc in (msg_obj.tool_calls or []):
        args = json.loads(tc.function.arguments or "{}")
        try:
            result = await _run_one_tool(tc.function.name, args)
        except Exception as e:
            result = {"error": f"Tool error: {e}"}
        tool_msgs.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            }
        )

    return base_msgs + [assistant_tool] + tool_msgs

async def run_with_tools(messages: List[dict]) -> dict:
    """
    messages: [{"role": "user"/"assistant", "content": "..."}...]
    We prepend SYSTEM_PROMPT, let OpenAI decide tool calls,
    then (if tool calls) run them and ask again.
    """
    base_msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    from openai.types.chat import ChatCompletionMessage
    first_resp = oai.chat.completions.create(
        model=MODEL,
        messages=base_msgs,
        tools=TOOL_SCHEMA,
        tool_choice="auto",
        stream=False,
    )

    choice = first_resp.choices[0]
    msg: ChatCompletionMessage = choice.message

    if choice.finish_reason == "tool_calls" and msg.tool_calls:
        msgs_with_tools = await _apply_tool_calls(base_msgs, msg)
        second_resp = oai.chat.completions.create(
            model=MODEL,
            messages=msgs_with_tools,
            stream=False,
        )
        return second_resp.choices[0].message.model_dump()

    return msg.model_dump()

# === FastAPI app ===
app = FastAPI(title="AI Agent API (Python + Voice + Phone)")

origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === static UI (your web chat UI) ===
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="static")

# === call audio directory (for Twilio to stream our generated voice)
CALL_AUDIO_DIR = Path(BASE_DIR / "call_audio")
CALL_AUDIO_DIR.mkdir(exist_ok=True)

# serve mp3s so Twilio can <Play>
app.mount(
    "/call-audio",
    StaticFiles(directory=str(CALL_AUDIO_DIR), html=False),
    name="call-audio",
)

@app.get("/")
async def root():
    return RedirectResponse(url="/ui/")

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.get("/healthz")
async def healthz():
    ready = bool(OPENAI_API_KEY)
    return {
        "ok": True,
        "openai": ready,
        "firestore": db is not None,
        "model": MODEL,
        "tz": DEFAULT_TZ,
    }

@app.get("/config")
def get_config():
    return {
        "calLink": os.getenv("CALCOM_EVENT_LINK", "").strip(),
        "brandColor": os.getenv("CALCOM_BRAND_COLOR", "#111827").strip(),
    }

# === Chat endpoint (used by browser + phone brain)
@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        incoming = [m.model_dump(exclude_none=True) for m in req.messages]
        message = await run_with_tools(incoming)
        save_chat_to_firestore(incoming, message)

        # frontend expects { message: {role, content} }
        return {"message": message}
    except Exception as e:
        print("❌ /v1/chat error:", e)
        return JSONResponse(
            status_code=500,
            content={"error": f"server_error: {str(e)}"},
        )

# === Twilio SMS webhook (kept for completeness)
@app.post("/twilio/sms")
async def sms_webhook(request: Request):
    form = await request.form()
    body = form.get("Body", "")
    return HTMLResponse(
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>Thanks! The AI assistant received: {body}</Message>
</Response>""",
        media_type="application/xml",
    )

# ====================================================
# 🗣️ Speech To Text (browser mic upload)
# ====================================================
@app.post("/v1/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """
    Browser sends WebM/Opus audio -> we return transcript {text:"..."}.
    The UI uses that to display your bubble.
    """
    try:
        content = await audio.read()
        client = speech.SpeechClient()
        audio_cfg = speech.RecognitionAudio(content=content)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            language_code="en-US",
            enable_automatic_punctuation=True,
        )
        resp = client.recognize(config=config, audio=audio_cfg)

        if not resp.results:
            return {"text": ""}

        transcript = resp.results[0].alternatives[0].transcript
        print("🎙️ Transcribed:", transcript)
        return {"text": transcript}
    except Exception as e:
        print("❌ STT error:", e)
        return JSONResponse({"text": "", "error": str(e)}, status_code=500)

# ====================================================
# 🔊 Text To Speech (browser playback)
# ====================================================
from elevenlabs.client import ElevenLabs
_eleven_client: ElevenLabs | None = None
if ELEVENLABS_API_KEY:
    try:
        _eleven_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        print("✅ ElevenLabs client ready")
    except Exception as e:
        print("⚠️ ElevenLabs init failed:", e)
else:
    print("ℹ️ ELEVENLABS_API_KEY not set, will use Google TTS only")

@app.post("/v1/tts")
async def text_to_speech(payload: dict):
    """
    Converts assistant text into MP3 audio stream for the browser.
    Tries ElevenLabs first, fallback to Google.
    """
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    # 1) ElevenLabs first
    if _eleven_client:
        try:
            VOICE_ID = "yM93hbw8Qtvdma2wCnJG"  # TODO: set your ElevenLabs voice ID
            audio_stream = _eleven_client.text_to_speech.convert(
                voice_id=VOICE_ID,
                model_id="eleven_multilingual_v2",
                text=text,
                output_format="mp3_44100_128",
            )
            audio_bytes = b"".join(chunk for chunk in audio_stream)
            print("🔊 ElevenLabs TTS generated", len(audio_bytes), "bytes")
            return StreamingResponse(
                io.BytesIO(audio_bytes), media_type="audio/mpeg"
            )
        except Exception as e:
            print("⚠️ ElevenLabs TTS failed, using Google fallback:", e)

    # 2) Google fallback
    try:
        tts_client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        tts_resp = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
        audio_bytes = tts_resp.audio_content
        print("🔊 Google TTS generated", len(audio_bytes), "bytes (fallback)")
        return StreamingResponse(
            io.BytesIO(audio_bytes), media_type="audio/mpeg"
        )
    except Exception as e:
        print("❌ TOTAL TTS error:", e)
        return JSONResponse({"error": str(e)}, status_code=500)

# ====================================================
# 🎤 PHONE voice helper: synthesize + save MP3 so Twilio can <Play>
# ====================================================
async def synthesize_agent_voice_to_file(text: str) -> str:
    """
    Generate branded voice audio for phone calls.
    Saves MP3 under call_audio/, returns relative URL path like /call-audio/<id>.mp3
    Twilio will <Play> BASE_PUBLIC_URL + that path in the live call.
    """
    if not text:
        text = "..."

    audio_bytes: bytes = b""

    # Try ElevenLabs first (same voice as web)
    if _eleven_client:
        try:
            VOICE_ID = "yM93hbw8Qtvdma2wCnJG"  # TODO: same ElevenLabs voice ID here
            audio_stream = _eleven_client.text_to_speech.convert(
                voice_id=VOICE_ID,
                model_id="eleven_multilingual_v2",
                text=text,
                output_format="mp3_44100_128",
            )
            audio_bytes = b"".join(chunk for chunk in audio_stream)
        except Exception as e:
            print("⚠️ phone ElevenLabs fail, fallback to Google:", e)

    # Fallback: Google TTS
    if not audio_bytes:
        try:
            tts_client = texttospeech.TextToSpeechClient()
            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice = texttospeech.VoiceSelectionParams(
                language_code="en-US",
                ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            )
            tts_resp = tts_client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config,
            )
            audio_bytes = tts_resp.audio_content
        except Exception as e:
            print("❌ TOTAL phone TTS fail:", e)
            audio_bytes = b""

    file_id = str(uuid4())
    file_path = CALL_AUDIO_DIR / f"{file_id}.mp3"
    with open(file_path, "wb") as f:
        f.write(audio_bytes)

    return f"/call-audio/{file_id}.mp3"

# ====================================================
# 🎧 LiveKit token stub (future realtime voice)
# ====================================================
@app.get("/v1/livekit-token")
async def get_livekit_token(identity: str = "browser-user"):
    return {"token": "livekit-token-placeholder"}

# ====================================================
# 📞 Twilio Voice Webhooks (inbound phone calls)
# ====================================================

@app.post("/twilio/voice", response_class=Response)
async def twilio_voice(
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
):
    """
    First webhook when the call connects.
    We greet with our own voice (MP3), then open a Gather turn.
    """
    await log_call_message(CallSid, "system", f"Call started from {From} to {To}")

    vr = VoiceResponse()

    # greeting in branded voice
    greet_url_path = await synthesize_agent_voice_to_file(
        "Hey, this is Mogul Support. Tell me what you need and I'll take care of it. Go ahead, I'm listening."
    )
    vr.play(f"{BASE_PUBLIC_URL}{greet_url_path}")

    # Gather: let caller talk
    gather = Gather(
        input="speech",
        action=f"{BASE_PUBLIC_URL}/twilio/voice/continue",
        method="POST",
        speechTimeout="auto",
        language="en-US",
    )
    # We do NOT add gather.say() or gather.play() here.
    # The greeting already told them we're listening.
    vr.append(gather)

    # Fallback if silence
    fallback_url_path = await synthesize_agent_voice_to_file(
        "Sorry, I didn't catch that. Can you say that again?"
    )
    vr.play(f"{BASE_PUBLIC_URL}{fallback_url_path}")
    vr.redirect(f"{BASE_PUBLIC_URL}/twilio/voice")

    return Response(content=str(vr), media_type="text/xml")

@app.post("/twilio/voice/continue", response_class=Response)
async def twilio_voice_continue(
    CallSid: str = Form(...),
    SpeechResult: str = Form(None),
):
    """
    Each conversational turn:
    - Take caller speech (SpeechResult)
    - Append to Firestore
    - Send convo so far to /v1/chat
    - Play ONLY the model's answer in branded voice
    - Open new Gather without injecting our own canned line
    """
    user_text = (SpeechResult or "").strip()

    if not user_text:
        vr = VoiceResponse()
        sorry_url_path = await synthesize_agent_voice_to_file(
            "I didn't hear anything. Can you say that one more time?"
        )
        vr.play(f"{BASE_PUBLIC_URL}{sorry_url_path}")
        vr.redirect(f"{BASE_PUBLIC_URL}/twilio/voice")
        return Response(content=str(vr), media_type="text/xml")

    # caller message -> Firestore
    await log_call_message(CallSid, "caller", user_text)

    # fetch conversation history for memory
    history_docs = []
    if db is not None:
        try:
            q = (
                db.collection("calls")
                .document(CallSid)
                .collection("messages")
                .order_by("ts")
                .stream()
            )
            for d in q:
                history_docs.append(d.to_dict())
        except Exception as e:
            print("history fetch fail", e)

    # convert call history -> /v1/chat style messages
    msgs_payload: List[Dict[str, str]] = []
    for msg in history_docs:
        r = msg.get("role")
        text = msg.get("text", "")
        if r == "caller":
            msgs_payload.append({"role": "user", "content": text})
        elif r == "agent":
            msgs_payload.append({"role": "assistant", "content": text})

    # latest caller turn
    msgs_payload.append({"role": "user", "content": user_text})

    # talk to our same web brain
    assistant_reply = "Absolutely. I can help with that. Can you tell me what you need done?"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "http://localhost:8787/v1/chat",
                headers={"content-type": "application/json"},
                json={"messages": msgs_payload},
            )
        data = r.json()
        assistant_reply = data.get("message", {}).get("content", assistant_reply)
    except Exception as e:
        print("agent call fail", e)

    # log agent reply back to Firestore for this CallSid
    await log_call_message(CallSid, "agent", assistant_reply)

    vr = VoiceResponse()

    # 1. play assistant reply (model output only, no canned voice lines)
    reply_url_path = await synthesize_agent_voice_to_file(assistant_reply)
    vr.play(f"{BASE_PUBLIC_URL}{reply_url_path}")

    # 2. open new gather (silent prompt so the LLM's reply stands alone)
    gather = Gather(
        input="speech",
        action=f"{BASE_PUBLIC_URL}/twilio/voice/continue",
        method="POST",
        speechTimeout="auto",
        language="en-US",
    )
    # No gather.say() or gather.play() -> we don't inject "What else can I do"
    vr.append(gather)

    # 3. polite closing in case they don't answer
    end_url_path = await synthesize_agent_voice_to_file(
        "Okay, I'll wrap us up here. You can call back anytime."
    )
    vr.play(f"{BASE_PUBLIC_URL}{end_url_path}")

    return Response(content=str(vr), media_type="text/xml")

# ====================================================
# 📞 Twilio call status callback (after call ends)
# ====================================================
@app.post("/twilio/voice/status", response_class=Response)
async def twilio_voice_status(
    CallSid: str = Form(...),
    CallStatus: str = Form(...),
    From: str = Form(None),
    To: str = Form(None),
    Duration: str = Form(None),
):
    """
    Twilio hits this when the call ends (completed/busy/no-answer/etc.).
    We:
    - fetch turn history from Firestore
    - build a transcript
    - get an OpenAI summary
    - save summary+transcript to calls/{CallSid}
    """
    print(f"📞 status callback for {CallSid}: {CallStatus} duration={Duration}s")

    # 1. Pull all messages from this call
    convo_turns = []
    if db is not None:
        try:
            q = (
                db.collection("calls")
                .document(CallSid)
                .collection("messages")
                .order_by("ts")
                .stream()
            )
            for d in q:
                convo_turns.append(d.to_dict())
        except Exception as e:
            print("status fetch fail", e)

    # 2. Build transcript text
    transcript_lines = []
    for turn in convo_turns:
        role = turn.get("role", "unknown")
        text = turn.get("text", "")
        ts = turn.get("ts", "")
        if role == "caller":
            transcript_lines.append(f"[caller @ {ts}] {text}")
        elif role == "agent":
            transcript_lines.append(f"[agent @ {ts}] {text}")
        else:
            transcript_lines.append(f"[{role} @ {ts}] {text}")
    full_transcript = "\n".join(transcript_lines)

    # 3. Summarize with OpenAI for internal notes
    call_summary = "No summary."
    try:
        summary_prompt = (
            "You are an assistant that writes internal call summaries for Mogul Design Agency.\n"
            "Summarize the call in ~5 bullet points:\n"
            "- What they wanted\n"
            "- Urgency / budget signal\n"
            "- Did they ask to book time?\n"
            "- Action items / follow up info we need\n\n"
            f"Transcript:\n\n{full_transcript}"
        )
        summary_resp = oai.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You create CRM-style call summaries for internal staff.",
                },
                {"role": "user", "content": summary_prompt},
            ],
            stream=False,
        )
        call_summary = summary_resp.choices[0].message.content
    except Exception as e:
        print("summary error:", e)

    # 4. Save summary + transcript to Firestore on the call doc
    if db is not None:
        try:
            call_doc_ref = db.collection("calls").document(CallSid)
            call_doc_ref.set(
                {
                    "from": From,
                    "to": To,
                    "status": CallStatus,
                    "duration_seconds": Duration,
                    "ended_at": datetime.utcnow().isoformat() + "Z",
                    "summary": call_summary,
                    "transcript": full_transcript,
                },
                merge=True,
            )
            print("📖 saved call summary for", CallSid)
        except Exception as e:
            print("save summary fail:", e)

    # Twilio just needs 200
    return Response(status_code=200)

# ====================================================
# 🚀 Startup banner
# ====================================================
@app.on_event("startup")
async def startup_event():
    print("🚀 Mogul AI Agent API running with OpenAI + voice + phone.")
