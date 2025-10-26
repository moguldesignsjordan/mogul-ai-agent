import os, json
from typing import Dict, Any, AsyncGenerator, List
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
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

from tools import (
    get_booking_link,
    lookup_customer,
    add_note,
)

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
print("OPENAI_API_KEY length:", len(OPENAI_API_KEY) if OPENAI_API_KEY else 0)
print("MODEL:", MODEL)
print("DEFAULT_TZ:", DEFAULT_TZ)
print("–––––––– END STARTUP DEBUG ––––––––")

# Firestore init (optional)
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

# OpenAI client
from openai import OpenAI
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing. Check apps/api-python/.env")
oai = OpenAI(api_key=OPENAI_API_KEY)

# Request/response models
from models import ChatRequest, ChatResponse

# Tool schemas exposed to the model
TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_booking_link",
            "description": "Return the public booking link for a 30-minute call with Jordan. Use this any time the user wants to book, schedule, talk live, or check availability.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": "Check Firestore for an existing customer record by email or phone, so we know if this is a returning lead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": { "type": "string", "description": "Email if they gave one." },
                    "phone": { "type": "string", "description": "Phone number if they gave one. Can be messy formatting." }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Attach a short summary of this conversation to an existing Firestore customer record. Use after sharing the booking link so Jordan can follow up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "Internal conversation/session ID from the chat client."
                    },
                    "customer_id": {
                        "type": "string",
                        "description": "Firestore customer doc ID, if known."
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief human-readable note of what they want."
                    }
                },
                "required": ["conversation_id", "customer_id", "summary"]
            }
        }
    }
]

# Map tool names -> actual coroutine in tools.py
TOOL_IMPL = {
    "get_booking_link": get_booking_link,
    "lookup_customer": lookup_customer,
    "add_note": add_note,
}

# Assistant behavior
SYSTEM_PROMPT = (
    "You are Mogul Design Agency's helpful customer service assistant. "
    "Be concise, friendly, and proactive.\n"
    "\n"
    "BOOKING FLOW:\n"
    "1) If the user asks to talk, meet, call, schedule, book time, or check availability "
    "with Jordan, assume they want to schedule a call.\n"
    "2) Ask for their full name first. If they only give a first name, ask \"What's your last name too?\".\n"
    "3) Then ask for their best email. Check that it looks like name@domain.tld. "
    "If it's not valid, ask them to confirm it. Never invent an email.\n"
    "4) After you have name + email, OR if the user says \"just send me the link\":\n"
    "   - Call the `get_booking_link` tool.\n"
    "   - Tell them: \"Here’s the direct booking link, you can pick any open 30-minute slot that works for you.\"\n"
    "5) Do NOT promise you will personally add events to the calendar. You only give them the link right now.\n"
    "\n"
    "CRM / FOLLOW-UP:\n"
    "• If you already know their Firestore customer_id, you MAY call `add_note` with a short summary "
    "  of what they want so Jordan can follow up. If you don't know a customer_id, skip that.\n"
    "\n"
    "GENERAL QUESTIONS:\n"
    "• Be warm, clear, and concrete. If they ask general questions about services, answer briefly.\n"
    "• You may include one helpful link to the agency site if relevant. Never make up a link.\n"
    "\n"
    "RULES:\n"
    "• Never invent personal data.\n"
    "• Never ask for credit card numbers, SSNs, or other sensitive info.\n"
    "• If the user asks for a human, say you can connect them and summarize what they need.\n"
)

# internal helpers for tool calling
async def _run_one_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    fn = TOOL_IMPL.get(name)
    if fn is None:
        return {"error": "unknown_tool"}

    # lookup_customer is special (either email or phone is okay)
    if name == "lookup_customer":
        email, phone = args.get("email"), args.get("phone")
        if not email and not phone:
            return {"error": "provide_email_or_phone"}
        return await fn(email=email, phone=phone)

    # others map directly
    return await fn(**args)

def _assistant_tool_call_dict(msg_obj) -> dict:
    calls = []
    for tc in (msg_obj.tool_calls or []):
        calls.append({
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.function.name,
                "arguments": tc.function.arguments or "{}",
            },
        })
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

        tool_msgs.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(result),
        })

    return base_msgs + [assistant_tool] + tool_msgs

async def run_with_tools(messages: List[dict]) -> dict:
    """
    messages: chat history WITHOUT the system prompt.
    returns: {"role":"assistant","content":"..."} final message for frontend.
    """

    if not OPENAI_API_KEY:
        return {
            "role": "assistant",
            "content": "⚠️ Missing OPENAI_API_KEY. Add it to .env and restart the server."
        }

    base_msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    # 1. first LLM call: allow tool calls
    from openai.types.chat import ChatCompletionMessage
    first_resp = oai.chat.completions.create(
        model=MODEL,
        messages=base_msgs,
        tools=TOOL_SCHEMA,
        tool_choice="auto",
        stream=False
    )

    choice = first_resp.choices[0]
    msg: ChatCompletionMessage = choice.message

    # If tools were requested
    if choice.finish_reason == "tool_calls" and msg.tool_calls:
        # run tools
        msgs_with_tools = await _apply_tool_calls(base_msgs, msg)

        # 2. second LLM call: give tool results back to model, NO new tools
        second_resp = oai.chat.completions.create(
            model=MODEL,
            messages=msgs_with_tools,
            stream=False
        )
        return second_resp.choices[0].message.model_dump()

    # No tool call, just answer
    return msg.model_dump()

# FastAPI app + CORS
app = FastAPI(title="AI Agent API (Python)")

origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# static UI
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="static")

# / -> /ui
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

# main chat endpoint
from models import ChatRequest, ChatResponse

@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        incoming = [m.model_dump(exclude_none=True) for m in req.messages]
        message = await run_with_tools(incoming)
        save_chat_to_firestore(incoming, message)  # no-op if db is None
        return {"message": message}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"server_error: {str(e)}"})

# (We'll leave /v1/chat/stream for later if you still want streaming.)


# Twilio SMS webhook placeholder (still fine to keep)
@app.post("/twilio/sms")
async def sms_webhook(request: Request):
    form = await request.form()
    body = form.get("Body", "")
    return HTMLResponse(
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>Thanks! The AI assistant received: {body}</Message>
</Response>""",
        media_type="application/xml"
    )
