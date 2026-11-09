from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os, random
from datetime import datetime
import google.generativeai as genai

app = FastAPI(title="neo.madewithfelix.app")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

MODEL_FALLBACK = [
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

AGENTS = {
    "ORCA":   {"name": "ORCA",   "role": "Chief of Staff",        "animal": "🐋", "color": "#2dd4bf",
               "prompt": "You are ORCA, Chief of Staff and coordinator. You route tasks, give clear direction, and keep things moving. Be concise and decisive."},
    "BEAVER": {"name": "BEAR",   "role": "QA / Reviewer",         "animal": "🐻", "color": "#f59e0b",
               "prompt": "You are BEAR, QA and Reviewer agent. You are firm, strict, and a bit fierce — you inspect all work and receipts before Felix sees them, catching any mistakes or discrepancies. Be sharp, no-nonsense, and decisive."},
    "PANDA":  {"name": "GIRAFFE","role": "Knowledge Manager",     "animal": "🦒", "color": "#ec4899",
               "prompt": "You are GIRAFFE, Knowledge Manager. You balance debits and credits, remember vendors, and keep accurate records. Be organised and helpful."},
    "COLLIE": {"name": "CORGI",  "role": "Finance Agent",         "animal": "🐕", "color": "#8b5cf6",
               "prompt": "You are CORGI, the cute and friendly Finance agent. You read receipts, sort expenses, and get everything tax-ready in AUD. Be cheerful, helpful, and concise."},
}

STATES = ["idle", "working", "walking"]
agent_status = {k: {**{x: v[x] for x in ["name","role","animal","color"]}, "state": "idle", "task": "Waiting for work", "last_seen": datetime.utcnow().isoformat()} for k, v in AGENTS.items()}

async def call_gemini(system_prompt: str, user_message: str) -> str:
    if not GEMINI_KEY:
        return "Gemini API key not configured."
    for model_name in MODEL_FALLBACK:
        try:
            model = genai.GenerativeModel(model_name, system_instruction=system_prompt)
            response = model.generate_content(user_message)
            return response.text
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ["overloaded", "429", "quota", "busy", "unavailable"]):
                continue
            return f"Error: {str(e)}"
    return "All models are currently busy. Please try again in a moment."

@app.get("/api/status")
def get_status():
    for name, agent in agent_status.items():
        if random.random() < 0.2:
            agent["state"] = random.choice(STATES)
        agent["last_seen"] = datetime.utcnow().isoformat()
    return agent_status

@app.post("/chat")
@app.post("/api/message")
async def send_message(body: dict):
    msg = body.get("message", "")
    raw_name = str(body.get("agent", "ORCA")).upper()
    ALIASES = {
        "CORGI": "COLLIE",
        "FINANCE": "COLLIE",
        "BEAR": "BEAVER",
        "QA": "BEAVER",
        "GIRAFFE": "PANDA",
        "KNOWLEDGE_MANAGER": "PANDA",
        "CHIEF_OF_STAFF": "ORCA",
    }
    agent_name = ALIASES.get(raw_name, raw_name)
    if agent_name not in AGENTS:
        agent_name = "ORCA"
    agent = AGENTS[agent_name]
    agent_status[agent_name]["state"] = "working"
    agent_status[agent_name]["task"] = msg[:50]
    reply = await call_gemini(agent["prompt"], msg)
    agent_status[agent_name]["state"] = "idle"
    agent_status[agent_name]["task"] = "Waiting for work"
    return {"reply": reply, "agent": raw_name}

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat(), "gemini": bool(GEMINI_KEY)}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
