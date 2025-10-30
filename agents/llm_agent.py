from __future__ import annotations
import os, sys
from pathlib import Path
from textwrap import dedent
from dotenv import load_dotenv


_THIS = Path(__file__).resolve()
for parent in [_THIS.parent, *_THIS.parents]:
    if (parent / "helpers").is_dir():
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        break
else:
    print("Could not locate 'helpers' directory.", file=sys.stderr)
    sys.exit(1)


# Load environment + config
load_dotenv()

try:
    from helpers.config import load_config
except Exception as e:
    print(f"[config] Missing or invalid config loader (helpers/config.py): {e}", file=sys.stderr)
    sys.exit(1)

cfg = load_config()

# OpenAI SDK
try:
    from openai import OpenAI
except ImportError:
    print("Please install OpenAI SDK: pip install openai", file=sys.stderr)
    sys.exit(1)

# Optional .docx support
try:
    from docx import Document
    HAVE_DOCX = True
except ImportError:
    HAVE_DOCX = False


# Config values (from config.yaml)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("Missing OPENAI_API_KEY in .env", file=sys.stderr)
    sys.exit(2)

MODEL = cfg.model_for("llm_chat")
TEMPERATURE = cfg.temp_for("llm_chat")

SYSTEM_PROMPT = dedent(cfg.prompt("llm_chat", "system") or "")
GUIDE = dedent(cfg.prompt("llm_chat", "guide") or "")

UCC_RULES = Path(cfg.path("ucc_rules_txt") or "")
PCC_RULES = Path(cfg.path("pcc_rules_txt") or "")

MAX_REGULATIONS_CHARS = 200_000


# Helper functions
def load_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")

def load_docx(path: Path) -> str:
    if not HAVE_DOCX:
        raise RuntimeError("Missing dependency: python-docx. Install with: pip install python-docx")
    doc = Document(str(path))
    return "\n".join(p.text.strip() for p in doc.paragraphs if p.text)

def load_regulations(path: Path) -> str:
    ext = path.suffix.lower()
    return load_docx(path) if ext == ".docx" else load_txt(path)

def _validate_rule_path(p: Path, label: str):
    if not p or not str(p).strip() or str(p) == ".":
        print(f"[config] '{label}' points to an empty/invalid path. Fix config/config.yaml.", file=sys.stderr)
        sys.exit(3)
    if not p.exists() or p.is_dir():
        print(f"[config] '{label}' not found or is a directory: {p}", file=sys.stderr)
        sys.exit(3)

def load_rules_text() -> str:
    _validate_rule_path(UCC_RULES, "paths.ucc_rules_txt")
    _validate_rule_path(PCC_RULES, "paths.pcc_rules_txt")
    ucc = UCC_RULES.read_text(encoding="utf-8")
    pcc = PCC_RULES.read_text(encoding="utf-8")
    return ucc + "\n\n" + pcc

# ----------------------------
# Core LLM call
# ----------------------------
def call_openai(system_msg: str, user_msg: str) -> str:
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    return (resp.choices[0].message.content or "").strip()

def ask_llm(question: str, regulations_text: str) -> str:
    user_msg = (
        f"{GUIDE}\n\n"
        f"REGULATIONS:\n{regulations_text}\n\n"
        f"QUESTION:\n{question}"
    )
    return call_openai(SYSTEM_PROMPT, user_msg)

# CLI entry
def main():
    print(f"\nUniversity Regulations Chatbot (Model: {MODEL}) — type 'exit' to quit\n")

    regs = load_rules_text()
    full_context = regs

    transcript_text = ""
    transcript_path = input("Transcript file path (.docx or .txt, leave blank to skip): ").strip()
    if transcript_path:
        path = Path(transcript_path)
        if path.exists() and path.is_file():
            transcript_text = load_regulations(path)
            full_context += "\n\nSTUDENT TRANSCRIPT\n" + transcript_text
            print(f"Loaded transcript from: {path.name}")
        else:
            print("⚠️ File not found. Proceeding without transcript.\n")

    if len(full_context) > MAX_REGULATIONS_CHARS:
        print(f"⚠️ Context too large ({len(full_context):,} chars). Consider trimming rules.", file=sys.stderr)

    while True:
        try:
            q = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not q:
            continue
        if q.lower() in {"exit", "quit", ":q"}:
            break

        try:
            answer = ask_llm(q, full_context)
            print(f"\nAssistant:\n{answer}\n")
        except Exception as e:
            print(f"[error] {e}")

if __name__ == "__main__":
    main()