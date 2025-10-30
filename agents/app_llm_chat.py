# multi_agent_system/agents/app_streamlit_min.py
from __future__ import annotations
import os
import sys
from pathlib import Path
from textwrap import dedent
import streamlit as st
from dotenv import load_dotenv

_THIS = Path(__file__).resolve()
for parent in [_THIS.parent, *_THIS.parents]:
    if (parent / "helpers").is_dir():
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        break
else:
    st.error("Could not locate 'helpers' directory. Ensure project structure is correct.")
    st.stop()

# Env + Config
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    st.error("OPENAI_API_KEY not set. Add it to your .env file and restart.")
    st.stop()

try:
    from helpers.config import load_config
except Exception as e:
    st.error(f"[config] Missing or invalid config loader (helpers/config.py): {e}")
    st.stop()

cfg = load_config()


# OpenAI SDK
try:
    from openai import OpenAI
except ImportError:
    st.error("Missing dependency: openai\nInstall it with: pip install openai")
    st.stop()


# Optional .docx 
try:
    from docx import Document
    HAVE_DOCX = True
except ImportError:
    HAVE_DOCX = False

# Config values (from config.yaml)
MODEL = cfg.model_for("llm_chat")
TEMPERATURE = cfg.temp_for("llm_chat")
SYSTEM_PROMPT = dedent(cfg.prompt("llm_chat", "system") or "")
GUIDE = dedent(cfg.prompt("llm_chat", "guide") or "")
UCC_RULES = Path(cfg.path("ucc_rules_txt") or "")
PCC_RULES = Path(cfg.path("pcc_rules_txt") or "")
MAX_REGULATIONS_CHARS = 200_000

# Helpers
def load_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")

@st.cache_data(show_spinner=False)
def load_rules_text() -> str:
    missing, texts = [], []
    for p in (UCC_RULES, PCC_RULES):
        if p.exists() and not p.is_dir():
            texts.append(load_txt(p))
        else:
            missing.append(str(p))
    if missing:
        raise FileNotFoundError(
            "Missing rules file(s):\n" + "\n".join(f"- {m}" for m in missing) +
            "\nRun your rules converter first or fix the config paths."
        )
    return "\n\n".join(texts)

# Core LLM logic (no LangChain)
def call_openai(api_key: str, model: str, temperature: float, system_msg: str, user_msg: str) -> str:
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    return (resp.choices[0].message.content or "").strip()

def ask_llm(api_key: str, model: str, temperature: float, question: str, regulations_text: str) -> str:
    user_msg = (
        f"{GUIDE}\n\n"
        f"REGULATIONS:\n{regulations_text}\n\n"
        f"QUESTION:\n{question}"
    )
    return call_openai(api_key, model, temperature, SYSTEM_PROMPT, user_msg)

# Streamlit UI
st.set_page_config(page_title="University Regulations Chatbot", layout="centered")
st.title("University Regulations Chatbot")

# ---- Sidebar: Read-only Settings
st.sidebar.header("⚙️ Settings")
st.sidebar.markdown("These settings are loaded automatically from `config/config.yaml`.")
st.sidebar.markdown(f"**Model:** `{MODEL}`")
st.sidebar.markdown(f"**Temperature:** `{TEMPERATURE}`")
st.sidebar.markdown("---")
st.sidebar.caption("All configuration and API credentials are managed by the project settings.")

# ---- Load regulations text
try:
    regulations_text = load_rules_text()
except Exception as e:
    st.error(str(e))
    st.stop()

if len(regulations_text) > MAX_REGULATIONS_CHARS:
    st.warning(f"⚠️ Regulations text is large ({len(regulations_text):,} chars).")

# ---- Main Chat Section
#st.markdown(f"**Active model:** `{MODEL}` | **Temperature:** `{TEMPERATURE}`")
st.caption("Ask about progression, compensation, reassessment, or classification…")

if "history" not in st.session_state:
    st.session_state.history = []

# Render chat history
for turn in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(turn["q"])
    with st.chat_message("assistant"):
        st.markdown(turn["a"])

# Chat input
q = st.chat_input("Type your question…")
if q:
    with st.chat_message("user"):
        st.markdown(q)
    try:
        a = ask_llm(OPENAI_API_KEY, MODEL, TEMPERATURE, q, regulations_text)
    except Exception as e:
        a = f"[error] {e}"
    with st.chat_message("assistant"):
        st.markdown(a)
    st.session_state.history.append({"q": q, "a": a})