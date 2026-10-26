"""Research Assistant settings.

The defaults are below. Your own values - folders, what you work on, reference facts, which model file - go in
local_settings.py next to this file (copy local_settings.example.py to start); anything set there overrides the default
of the same name here. local_settings.py is listed in .gitignore, so it never goes to GitHub.
Edit a value, save, then restart the app."""
import importlib.util
import os
from pathlib import Path

HOME = Path.home()
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "index.db"
BIB_PATH = DATA_DIR / "bibliography.json"

# Drop new PDFs here (subfolders are included).
PAPERS_DIR = HOME / "Documents" / "Research Papers"

# Your own proposal/thesis draft (optional): the newest .docx/.pdf with "proposal" in its name in PROPOSAL_DIR is built
# into the assistant (re-read automatically whenever you save a new version). To pin one file instead, set
# PROPOSAL_FILE to its path. Leave both as None to switch this off.
PROPOSAL_DIR = None
PROPOSAL_FILE = None

# Your Zotero library. Set USE_ZOTERO = False to ignore it.
USE_ZOTERO = True
ZOTERO_DIR = HOME / "Zotero"
# How to open Zotero papers at a page: "zotero" (Zotero's reader) or "browser" (Microsoft Edge)
OPEN_ZOTERO_PDFS_IN = "zotero"

# Built-in AI engine (llama.cpp). It runs only while the app is open - no LM Studio needed.
ENGINE_DIR = APP_DIR / "engine"
MODELS_DIR = APP_DIR / "models"           # the model files (hard links to LM Studio's copies save disk space)
# Answer model file in MODELS_DIR. Qwen 3.5 9B tested clearly better than Qwen3-8B here (fewer mix-ups, correct physics,
# less GPU memory). The RA_CHAT_MODEL environment variable overrides it for one run.
# To use Qwen3-8B instead, name a qwen3-8b file (and consider turning thinking on for Explain in app.py).
CHAT_MODEL_NAME = "qwen3.5-9b-q4_k_m.gguf"
EMBED_MODEL_NAME = "nomic-embed-text-v1.5.Q4_K_M.gguf"
EMBED_MODEL = "nomic-embed-text-v1.5"
CHAT_PORT = 18431
EMBED_PORT = 18432
CONTEXT_LENGTH = 16384         # tokens the model can read at once (fits an 8 GB GPU with a compressed cache)
MAX_ANSWER_TOKENS = 1200
MAX_EXPLAIN_TOKENS = 2000      # Explain mode answers are longer
REASONING_BUDGET = int(os.environ.get("RA_REASONING_BUDGET", 1500))   # max tokens the model may "think" before answering
WIDE_CONTEXT = True            # give the model the passages either side of each match, not just the match
AUTO_QUIT_MINUTES = float(os.environ.get("RA_AUTO_QUIT_MINUTES", 10))   # stop everything this long after the last tab closes

# What you work on, in a few lines. Explain mode and study guides relate findings to it (a brief of your proposal is
# added automatically). Set it in local_settings.py.
RESEARCH_CONTEXT = ""

# Correct reference facts the model must follow when it explains things (a small local model gets some textbook facts
# wrong on its own). Set them in local_settings.py - local_settings.example.py has an example.
REFERENCE_NOTES = ""

DEFAULT_STYLE = "IEEE"         # IEEE, Harvard, APA 7, BibTeX
DEFAULT_EXCERPTS = 6           # passages given to the model per question
CHUNK_WORDS = 220              # passage size when indexing
CHUNK_OVERLAP = 40

# Optional: an email address Crossref can contact (gives faster, more reliable metadata lookups).
CROSSREF_MAILTO = ""

APP_PORT = 8510

# ---------------------------------------------------------------- your own settings override everything above
_LOCAL = APP_DIR / "local_settings.py"
if _LOCAL.exists():
    _spec = importlib.util.spec_from_file_location("local_settings", _LOCAL)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    globals().update({k: v for k, v in vars(_mod).items() if k.isupper()})

# ---------------------------------------------------------------- worked out from the settings above
PAPERS_DIR = Path(PAPERS_DIR)
PROPOSAL_DIR = Path(PROPOSAL_DIR) if PROPOSAL_DIR else None
ZOTERO_DIR = Path(ZOTERO_DIR)
CHAT_MODEL_FILE = MODELS_DIR / os.environ.get("RA_CHAT_MODEL", CHAT_MODEL_NAME)
EMBED_MODEL_FILE = MODELS_DIR / EMBED_MODEL_NAME
CHAT_MODEL = "qwen3.5-9b" if "3.5" in CHAT_MODEL_FILE.name else "qwen3-8b"
CHAT_URL = f"http://127.0.0.1:{CHAT_PORT}/v1"     # 127.0.0.1, not "localhost" (Windows tries IPv6 first: +2 s per call)
EMBED_URL = f"http://127.0.0.1:{EMBED_PORT}/v1"
