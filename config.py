"""Research Assistant settings.

The defaults are below. Your own values - folders, what you work on, reference facts, which model file - go in
local_settings.py next to this file (copy local_settings.example.py to start); anything set there overrides the default
of the same name here. local_settings.py is listed in .gitignore, so it never goes to GitHub.
Edit a value, save, then restart the app."""
import importlib.util
import json
import os
import re
from pathlib import Path

HOME = Path.home()
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "index.db"
BIB_PATH = DATA_DIR / "bibliography.json"

# Drop new PDFs here (subfolders are included).
PAPERS_DIR = HOME / "Documents" / "Research Papers"

# Your Zotero library. Set USE_ZOTERO = False to ignore it.
USE_ZOTERO = True
ZOTERO_DIR = HOME / "Zotero"
# How to open Zotero papers at a page: "zotero" (Zotero's reader) or "browser" (Microsoft Edge)
OPEN_ZOTERO_PDFS_IN = "zotero"

# Built-in AI engine (llama.cpp). It runs only while the app is open - no LM Studio needed.
ENGINE_DIR = APP_DIR / "engine"
MODELS_DIR = APP_DIR / "models"           # model files (.gguf); LM Studio's model folder is searched as well
# More folders to look for .gguf models in, e.g. [r"D:\AI models"].
MODEL_DIRS = []
# The answer model. Pick any model you have in the app's sidebar (🧠 AI model) or with  start.py --model NAME ; your
# choice is remembered (data\model_choice.json). This file name in MODELS_DIR is only the default until you pick one.
# Qwen 3.5 9B tested clearly better than Qwen3-8B here (fewer mix-ups, correct physics, less GPU memory).
# The RA_CHAT_MODEL environment variable overrides everything for one run (a file name in MODELS_DIR, or a full path).
CHAT_MODEL_NAME = "qwen3.5-9b-q4_k_m.gguf"
EMBED_MODEL_NAME = "nomic-embed-text-v1.5.Q4_K_M.gguf"
# Vision: lets the answer model look at figures and tables (explain a graph, read a table printed as an image).
# Needs the model's vision file ("mmproj-....gguf") in the same folder as the model; it is found automatically
# (MMPROJ_NAME names it for the default model above). About 0.5-1.7 GB more GPU memory. Missing file = vision off.
VISION = True
MMPROJ_NAME = "mmproj-qwen3.5-9b-bf16.gguf"
VISION_IMAGE_TOKENS = 1280     # most image tokens per figure (more = finer detail, slower)
EMBED_MODEL = "nomic-embed-text-v1.5"
CHAT_PORT = 18431
EMBED_PORT = 18432
CONTEXT_LENGTH = 16384         # tokens the model can read at once (lowered automatically for a model that needs it)
MAX_ANSWER_TOKENS = 1200
MAX_EXPLAIN_TOKENS = 2000      # Explain mode answers are longer
REASONING_BUDGET = int(os.environ.get("RA_REASONING_BUDGET", 1500))   # max tokens the model may "think" before answering
WIDE_CONTEXT = True            # give the model the passages either side of each match, not just the match
AUTO_QUIT_MINUTES = float(os.environ.get("RA_AUTO_QUIT_MINUTES", 10))   # stop everything this long after the last tab closes

# What you work on, in a few lines. Explain mode, study guides and figure explanations relate findings to it.
# Set it in local_settings.py.
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
ZOTERO_DIR = Path(ZOTERO_DIR)
MODEL_DIRS = [Path(d) for d in MODEL_DIRS]
CONTEXT_SETTING = CONTEXT_LENGTH          # what you asked for; CONTEXT_LENGTH becomes what the running model uses
CHOICE_FILE = DATA_DIR / "model_choice.json"


def model_alias(path):
    """Short name the engine serves a model under: Qwen3.5-9B-Instruct-Q4_K_M.gguf -> qwen3.5-9b-instruct."""
    stem = re.sub(r"(?i)(-\d{5}-of-\d{5})?\.gguf$", "", Path(path).name)
    return re.sub(r"(?i)[-_.](i?q\d(_[0-9a-z]+)*|bf16|f16|f32|fp16|mxfp4(_moe)?)$", "", stem).lower() or "model"


def _remembered():
    try:
        c = json.loads(CHOICE_FILE.read_text("utf-8"))
        return c if Path(c.get("path", "")).exists() else {}
    except Exception:
        return {}


_choice = {} if os.environ.get("RA_CHAT_MODEL") else _remembered()
CHAT_MODEL_FILE = Path(_choice["path"]) if _choice else MODELS_DIR / os.environ.get("RA_CHAT_MODEL", CHAT_MODEL_NAME)
VISION_ON = bool(VISION and _choice.get("vision", True))
EMBED_MODEL_FILE = MODELS_DIR / EMBED_MODEL_NAME
MMPROJ_FILE = MODELS_DIR / MMPROJ_NAME
CHAT_MODEL = model_alias(CHAT_MODEL_FILE)
CHAT_URL = f"http://127.0.0.1:{CHAT_PORT}/v1"     # 127.0.0.1, not "localhost" (Windows tries IPv6 first: +2 s per call)
EMBED_URL = f"http://127.0.0.1:{EMBED_PORT}/v1"
