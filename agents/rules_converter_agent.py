from __future__ import annotations
import os, sys
from pathlib import Path
from dotenv import load_dotenv


_THIS = Path(__file__).resolve()
for parent in [_THIS.parent, *_THIS.parents]:
    if (parent / "helpers").is_dir():
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        break
else:
    print("Could not locate 'helpers' folder.", file=sys.stderr)
    sys.exit(1)

# Env + Config
load_dotenv()  
try:
    from helpers.config import load_config
except Exception as e:
    print(f"[config] Failed to import helpers.config: {e}", file=sys.stderr)
    sys.exit(1)

cfg = load_config()

# OpenAI SDK + optional .docx
try:
    from openai import OpenAI
except ImportError:
    print("Please install OpenAI SDK: pip install openai", file=sys.stderr)
    sys.exit(1)

try:
    from docx import Document
    HAVE_DOCX = True
except ImportError:
    HAVE_DOCX = False

# Config values (all from config.yaml)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("Missing OPENAI_API_KEY in .env", file=sys.stderr)
    sys.exit(3)

MODEL = cfg.model_for("rules_converter")
TEMPERATURE = cfg.temp_for("rules_converter")

SYSTEM_PROMPT = cfg.prompt("rules_converter", "system")
USER_TEMPLATE = cfg.prompt("rules_converter", "user_template")

REGL_PATH = Path(cfg.path("regulations_docx"))
OUT_UCC = Path(cfg.path("ucc_rules_txt")).parent
OUT_PCC = Path(cfg.path("pcc_rules_txt")).parent

# Helpers
def load_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        if not HAVE_DOCX:
            raise RuntimeError("Install python-docx to read .docx files (pip install python-docx).")
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    return path.read_text(encoding="utf-8", errors="ignore")

def call_openai(model: str, system_msg: str, user_msg: str) -> str:
    """Call OpenAI chat completion."""
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        temperature=TEMPERATURE,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    return (resp.choices[0].message.content or "").strip()

def run_for_jurisdiction(regs_text: str, jurisdiction: str, out_dir: Path):
    """Generate IF–THEN rules for one jurisdiction and save output."""
    print(f"→ Generating rules for {jurisdiction} using {MODEL} …")
    user_msg = USER_TEMPLATE.format(jurisdiction=jurisdiction, regs=regs_text)
    result = call_openai(MODEL, SYSTEM_PROMPT, user_msg)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "if_then_rules.txt"
    out_path.write_text(result, encoding="utf-8")
    print(f"✓ {jurisdiction}: wrote {out_path}")

# Main
def main():
    if not REGL_PATH.exists():
        print(f"[config] regulations_docx not found: {REGL_PATH}", file=sys.stderr)
        sys.exit(1)

    regs_text = load_text(REGL_PATH)
    run_for_jurisdiction(regs_text, "UCC", OUT_UCC)
    run_for_jurisdiction(regs_text, "PCC", OUT_PCC)
    print("\nAll jurisdictions processed successfully.")

if __name__ == "__main__":
    main()