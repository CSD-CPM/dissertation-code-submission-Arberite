# orchestrator.py
from __future__ import annotations
import os, sys
from pathlib import Path
from dotenv import load_dotenv
import subprocess

load_dotenv()

# Project paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
OUT_DIR = DATA_DIR / "out"
UCC_RULES_TXT = OUT_DIR / "ucc" / "if_then_rules.txt"
PCC_RULES_TXT = OUT_DIR / "pcc" / "if_then_rules.txt"
REPORTS_DIR = OUT_DIR / "reports"

# Import the agents 
# Agent 1: regulations -> IF-THEN
from agents.rules_converter_agent import main as rules_convert_main

# Agent 2: transcripts -> CSV
from agents.transcript_to_csv_agent import read_transcript, run_agent as transcript_llm

# Agent 3: Rule-based decision engine
from agents.rule_agent import main as rule_agent_main

# Agent 4: optional LLM chatbot
try:
    from agents.llm_agent import build_chain as llm_build_chain
except Exception:
    llm_build_chain = None

# ---------- Utils
def ensure_dirs():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

def combine_rules_text() -> str:
    missing = [p for p in (UCC_RULES_TXT, PCC_RULES_TXT) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing IF–THEN rules files. Run regulations converter first.\n" +
            "\n".join(f"- {m}" for m in missing)
        )
    return UCC_RULES_TXT.read_text(encoding="utf-8") + "\n\n" + PCC_RULES_TXT.read_text(encoding="utf-8")

# ---------- Actions
def action_convert_regulations():
    """Run Agent1 to (re)create UCC/PCC IF–THEN files."""
    print("Converting regulations → IF–THEN rules …")
    rules_convert_main()
    print("✓ Regulations converted. Files at:")
    print(f"  - {UCC_RULES_TXT}")
    print(f"  - {PCC_RULES_TXT}")

def action_convert_transcripts():
    """Run Agent2 for every transcript in data/transcripts → CSV in data/out."""
    print("Converting transcripts → CSV …")
    ensure_dirs()
    files = [p for p in TRANSCRIPTS_DIR.glob("*")
             if p.suffix.lower() in {".docx", ".txt"} and p.is_file() and not p.name.startswith("~$")]
    if not files:
        print(f"No transcripts found in {TRANSCRIPTS_DIR}")
        return
    print(f"Found {len(files)} file(s).")
    for infile in sorted(files):
        try:
            text = read_transcript(infile)
            csv_text = transcript_llm(text)
            outfile = OUT_DIR / (infile.stem + ".csv")
            outfile.write_text(csv_text, encoding="utf-8")
            print(f"  ✓ {infile.name} → {outfile.name}")
        except Exception as e:
            print(f"  [error] {infile.name}: {e}")
    print("✓ All transcripts processed.")

def action_run_rule_agent():
    """Run Agent 3 (rule-based decision engine) as a subprocess so it exits cleanly."""
    print("Launching Agent 3 – Rule-based decision engine…")
    rule_agent_path = BASE_DIR / "agents" / "rule_agent.py"
    if not rule_agent_path.exists():
        print(f"[error] rule_agent not found at {rule_agent_path}")
        return

    try:
        subprocess.run([sys.executable, str(rule_agent_path)], check=False)
        print("Agent 3 session ended. Returning to orchestrator menu.\n")
    except Exception as e:
        print(f"[error] Could not launch Agent 3: {e}")

def action_ask_llm():
    """Run Agent 4 (LLM chatbot) directly through the orchestrator."""
    print("Launching Agent 4 – LLM Chatbot…\n")
    llm_agent_path = BASE_DIR / "agents" / "llm_agent.py"
    if not llm_agent_path.exists():
        print(f"[error] llm_agent not found at {llm_agent_path}")
        return

    try:
        subprocess.run([sys.executable, str(llm_agent_path)], check=False)
        print("Agent 4 session ended. Returning to orchestrator menu.\n")
    except Exception as e:
        print(f"[error] Could not launch Agent 4: {e}")

def run_llm_chat_app():
    base_dir = Path(__file__).resolve().parent
    app_path = base_dir / "agents" / "app_llm_chat.py"
    if not app_path.exists():
        print(f"[error] Streamlit app not found at {app_path}")
        return
    print("Launching Streamlit LLM Chatbot...")
    subprocess.Popen(["streamlit", "run", str(app_path)])
    print("It will open automatically in your browser (localhost:8501).")

# Menu
MENU = """
================= Orchestrator =================
1) Convert regulations to IF–THEN rules
2) Convert transcripts to CSV file
3) Call rule-based decision engine
4) Call LLM to ask about regulations
5) Launch LLM Chatbot web app with Streamlit
0) Exit
"""

def main():
    ensure_dirs()
    while True:
        print(MENU)
        choice = input("Choose an option: ").strip()
        if choice == "1":
            action_convert_regulations()
        elif choice == "2":
            action_convert_transcripts()
        elif choice == "3":
            action_run_rule_agent()     
        elif choice == "4":
            action_ask_llm()
        elif choice == "5":
            run_llm_chat_app()
        elif choice == "0":
            print("Goodbye!")
            break
        else:
            print("Please choose 0–5.")

if __name__ == "__main__":
    main()