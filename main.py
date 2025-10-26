import os, io, json
from typing import Dict, Any, List
from datetime import datetime
from pathlib import Path
from google.cloud import speech_v1p1beta1 as speech
from google.cloud import texttospeech
from fastapi import FastAPI, Request, UploadFile, File, HTTPException
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

# === local tools ===
from tools import get_booking_link, lookup_customer, add_note

# ── resolve .env next to main.py
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "America/New_York")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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

# === OpenAI client ===
from openai import OpenAI
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing. Check apps/api-python/.env")
oai = OpenAI(api_key=OPENAI_API_KEY)

# === Request/response models ===
from models import ChatRequest, ChatResponse

# === Tools setup ===
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

# === System prompt ===
SYSTEM_PROMPT = (
    "You are Mogul Design Agency's helpful customer service assistant. "
    "Be concise, friendly, and proactive.\n\n"
    "BOOKING FLOW:\n"
    "1) If the user asks to talk, meet, call, schedule, book time, or check availability, assume they want to schedule a call.\n"
    "2) Ask for their full name first, then ask for email.\n"
    "3) After name + email, call `get_booking_link` to give them the scheduling link.\n"
    "4) Never invent data.\n"
)

# === Tool helpers ===
async def _run_one_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    fn = TOOL_IMPL.get(name)
    if fn is None:
        return {"error": "unknown_tool"}
    if name == "lookup_customer":
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
            model=MODEL, messages=msgs_with_tools, stream=False
        )
        return second_resp.choices[0].message.model_dump()
    return msg.model_dump()

# === FastAPI app ===
app = FastAPI(title="AI Agent API (Python + Voice)")

origins = [
    o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === static UI ===
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="static")

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

# === Chat endpoint ===
@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        incoming = [m.model_dump(exclude_none=True) for m in req.messages]
        message = await run_with_tools(incoming)
        save_chat_to_firestore(incoming, message)
        return {"message": message}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"server_error: {str(e)}"})


# === Twilio SMS webhook (still works) ===
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
# 🗣️ NEW: Speech-to-Text (STT) + Text-to-Speech (TTS)
# ====================================================

from google.cloud import speech_v1p1beta1 as speech
from google.cloud import texttospeech

@app.post("/v1/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """
    Accepts short WebM/Opus audio from the browser and returns JSON {text:"..."}.
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

@app.post("/v1/tts")
async def text_to_speech(payload: dict):
    """
    Converts assistant text into an MP3 audio stream.
    """
    try:
        text = payload.get("text", "")
        if not text:
            raise HTTPException(status_code=400, detail="No text provided")
        tts_client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
        )
        audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
        tts_resp = tts_client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        audio_bytes = tts_resp.audio_content
        print("🔊 TTS generated", len(audio_bytes), "bytes")
        return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/mpeg")
    except Exception as e:
        print("❌ TTS error:", e)
        return JSONResponse({"error": str(e)}, status_code=500)

# ====================================================
# 🎧 LiveKit Token (stub for future)
# ====================================================
@app.get("/v1/livekit-token")
async def get_livekit_token(identity: str = "browser-user"):
    return {"token": "livekit-token-placeholder"}

# ====================================================
# 🚀 Startup banner
# ====================================================
@app.on_event("startup")
async def startup_event():
    print("🚀 Mogul AI Agent API running with OpenAI + Voice ready.")
