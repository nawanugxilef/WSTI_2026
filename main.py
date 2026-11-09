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
    "BEAVER": {"name": "BEAVER", "role": "Mechanical CAD",        "animal": "🦫", "color": "#f59e0b",
               "prompt": "You are BEAVER, Mechanical CAD specialist. You handle mechanical design, fits, tolerances, and assemblies. Be technical and precise."},
    "PANDA":  {"name": "PANDA",  "role": "Creative & Characters", "animal": "🐼", "color": "#ec4899",
               "prompt": "You are PANDA, Creative and Characters specialist. You handle organic design, textures, colours, and decorative geometry. Be imaginative."},
    "COLLIE": {"name": "COLLIE", "role": "Independent Advisor",   "animal": "🐕", "color": "#8b5cf6",
               "prompt": "You are COLLIE, Independent Advisor. You provide honest, independent perspective and strategic advice. Be thoughtful and candid."},
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

@app.post("/api/message")
async def send_message(body: dict):
    msg = body.get("message", "")
    agent_name = body.get("agent", "ORCA").upper()
    if agent_name not in AGENTS:
        agent_name = "ORCA"
    agent = AGENTS[agent_name]
    agent_status[agent_name]["state"] = "working"
    agent_status[agent_name]["task"] = msg[:50]
    reply = await call_gemini(agent["prompt"], msg)
    agent_status[agent_name]["state"] = "idle"
    agent_status[agent_name]["task"] = "Waiting for work"
    return {"reply": reply, "agent": agent_name}

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat(), "gemini": bool(GEMINI_KEY)}

app.mount("/", StaticFiles(directory="static", html=True), name="static")
