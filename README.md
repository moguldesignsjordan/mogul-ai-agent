# AI Agent Starter (Python + FastAPI)

A minimal, **production-minded starter** for your powerful customer-service AI agent:
- **Python FastAPI** backend (Cloud Run-ready)
- **OpenAI** LLM with **function-calling** for tools
- **Firestore** (Firebase Admin) read/write
- **Cal.com** booking stub (create booking)
- **Simple Web Chat UI** (served by FastAPI) for instant local testing
- **Twilio SMS** webhook placeholder
- **Dockerfile** for Cloud Run

---

## 1) Setup

```bash
# Clone / unzip, then:
cd apps/api-python

# Create venv
python3 -m venv .venv
source .venv/bin/activate

# Install deps
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill:
- `OPENAI_API_KEY`
- `CALCOM_API_KEY`
- (Optional) `GOOGLE_APPLICATION_CREDENTIALS` → path to a service account JSON
- (Optional) `CORS_ORIGINS` for local front-ends

> For Firestore local dev, the service account should have access to your project.  
> In GCP/Cloud Run, prefer Workload Identity (no JSON key needed).

---

## 2) Run locally

```bash
# From apps/api-python
uvicorn main:app --reload --port 8787
```

Open the test UI at: **http://localhost:8787/**  
Type a message like: “I want to book an appointment next Tuesday at 3pm.”

---

## 3) Endpoints

- `GET /healthz` — health check
- `POST /v1/chat` — chat with tool-calling (JSON in/out)
- `POST /twilio/sms` — SMS webhook stub (to be finished)
- Static files — `/` serves a basic chat UI

---

## 4) Project Layout

```
/apps/api-python
  main.py           # FastAPI app, OpenAI orchestration
  tools.py          # Tool handlers (Firestore, Cal.com, Notes)
  models.py         # Pydantic types
  util/sse.py       # SSE helper (reserved for streaming use)
  static/
    index.html      # Simple chat UI
    chat.js
    styles.css
requirements.txt
.env.example
docker/Dockerfile.api
```

---

## 5) Cloud Run (quick)

### Build
```bash
gcloud builds submit --tag gcr.io/PROJECT_ID/ai-agent-api ./apps/api-python
```

### Deploy
```bash
gcloud run deploy ai-agent-api \
  --image gcr.io/PROJECT_ID/ai-agent-api \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets OPENAI_API_KEY=OPENAI_API_KEY:latest \
  --set-env-vars CALCOM_API_KEY=... \
  --set-env-vars CORS_ORIGINS=https://your-site.com
```

Grant Firestore access to the Cloud Run service account (or use Workload Identity).

---

## 6) Next Steps

- Replace Cal.com stub with your eventTypeId and fields
- Wire Firebase Auth identity from your website (pass customerId/email to backend)
- Add LiveKit voice agent service (Python) and connect to the same tools
- Add Twilio Media Streams for phone realtime
- Add RAG (embeddings + retriever) for your site docs
- Harden security: input validation, PII scrubbing, Twilio signature checks
