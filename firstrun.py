"""First start on a new computer: what's still missing (settings, AI engine, search model, an answer model), and
writing your settings file. The app shows its setup page until everything is in place."""
import json, re
from pathlib import Path
import config

LOCAL = config.APP_DIR / "local_settings.py"
PENDING = config.DATA_DIR / "setup_pending.json"


def mark_pending(choice):
    """Remember the setup choice while its downloads run: {'path': model file, 'vision': bool, 'name': ...}."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(choice), "utf-8")


def pending():
    try:
        return json.loads(PENDING.read_text("utf-8"))
    except Exception:
        return None


def clear_pending():
    PENDING.unlink(missing_ok=True)


def status():
    """{'settings', 'engine', 'embed', 'model'}: True for each part that's in place."""
    import downloads, models
    chat = Path(config.CHAT_MODEL_FILE).exists() or any(m["kind"] == "chat" for m in models.scan())
    return {"settings": LOCAL.exists(), "engine": downloads.engine_info() is not None,
            "embed": Path(config.EMBED_MODEL_FILE).exists(), "model": chat}


def complete():
    return all(status().values())


def ready(pend):
    """Everything the setup choice `pend` needs is in place."""
    s = status()
    return s["settings"] and s["engine"] and s["embed"] and Path(pend["path"]).exists()


def zotero_dir():
    """Zotero's data folder if Zotero is installed for this user, else None."""
    d = Path(config.ZOTERO_DIR)
    return d if (d / "zotero.sqlite").exists() else None


def write_settings(papers_dir, use_zotero, research_context):
    """Create local_settings.py (only if it doesn't exist yet - it's yours to edit afterwards) and use the values
    in this running app straight away."""
    papers = Path(papers_dir).expanduser()
    papers.mkdir(parents=True, exist_ok=True)
    text = research_context.strip()
    lines = ['"""Your own settings, written by the first-run setup. Edit freely: anything here overrides the default',
             'of the same name in config.py (local_settings.example.py shows more). Save, then restart the app."""',
             "",
             "# Folder you drop PDFs into (subfolders are included).",
             f"PAPERS_DIR = {str(papers)!r}",
             "",
             "# Your Zotero library (USE_ZOTERO = False ignores it).",
             f"USE_ZOTERO = {bool(use_zotero)!r}",
             f"ZOTERO_DIR = {str(config.ZOTERO_DIR)!r}",
             "",
             "# A few lines about what you work on. Explain mode and study guides relate findings to it.",
             f"RESEARCH_CONTEXT = {text!r}",
             "",
             "# Correct textbook facts the model must follow when it explains things (see local_settings.example.py).",
             'REFERENCE_NOTES = ""',
             ""]
    if not LOCAL.exists():
        LOCAL.write_text("\n".join(lines), "utf-8")
    config.PAPERS_DIR, config.USE_ZOTERO, config.RESEARCH_CONTEXT = papers, bool(use_zotero), text


def browse_folder(start):
    """Windows' folder picker (runs in a helper process so the app keeps working). The chosen folder, or None."""
    import subprocess, sys
    code = ("import sys, tkinter as tk, tkinter.filedialog as fd\n"
            "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
            "print(fd.askdirectory(title='Folder with your research papers (PDFs)', initialdir=sys.argv[1]) or '')\n")
    try:
        out = subprocess.run([sys.executable, "-c", code, str(start)], capture_output=True, text=True, timeout=600,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except Exception:
        return None
    return str(Path(out)) if out and re.match(r"^[A-Za-z]:|^\\\\", out) else None
