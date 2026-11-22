"""Updates: check GitHub for a newer release of Research Assistant and install it in place.

Only the app's own files are replaced. Your settings (local_settings.py), papers index and writing samples (data),
AI engine (engine), models (models) and Python (.venv, tools/uv, tools/python) are never touched. A copy that was
cloned with git is updated with "git pull" instead. Changed libraries are installed on the next start."""
import hashlib, io, re, subprocess, zipfile
from pathlib import Path
import requests
import config

REPO = "Haider-Ontor/local-research-assistant"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
KEEP = {"data", "engine", "engine.new", "engine.old", "models", ".venv", ".git", "local_settings.py",
        ".github", "installer"}                                      # the last two are only for building releases
KEEP_TOOLS = {"uv", "python", "uv-cache"}
NO_WINDOW = 0x08000000


def current():
    try:
        return (config.APP_DIR / "VERSION").read_text("utf-8").strip()
    except Exception:
        return "0.0.0"


def _v(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def latest(timeout=6):
    """The newest release on GitHub: {'version', 'name', 'notes', 'url', 'zip', 'size', 'sha256'}."""
    r = requests.get(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "ResearchAssistant"},
                     timeout=timeout)
    r.raise_for_status()
    j = r.json()
    a = next((x for x in j.get("assets", []) if x["name"] == "ResearchAssistant.zip"), None) or {}
    return {"version": j["tag_name"].lstrip("v"), "name": j.get("name") or j["tag_name"], "notes": j.get("body") or "",
            "url": j.get("html_url"), "zip": a.get("browser_download_url"), "size": a.get("size"),
            "sha256": (a.get("digest") or "").removeprefix("sha256:") or None}


def available(rel):
    return bool(rel) and _v(rel["version"]) > _v(current())


def is_git():
    return (config.APP_DIR / ".git").exists()


def install(rel, log=print):
    """Install release `rel` over this copy. Returns a short message."""
    app = config.APP_DIR
    if is_git():
        out = subprocess.run(["git", "pull", "--ff-only"], cwd=str(app), capture_output=True, text=True, timeout=180,
                             creationflags=NO_WINDOW)
        if out.returncode:
            raise RuntimeError("git pull didn't work: " + (out.stderr or out.stdout).strip()[-400:])
        return f"Updated with git pull to {current()}"
    if not rel.get("zip"):
        raise RuntimeError("This release has no ResearchAssistant.zip to install from.")
    log(f"Downloading Research Assistant {rel['version']}…")
    r = requests.get(rel["zip"], headers={"User-Agent": "ResearchAssistant"}, timeout=180)
    r.raise_for_status()
    data = r.content
    if rel.get("sha256") and hashlib.sha256(data).hexdigest() != rel["sha256"].lower():
        raise RuntimeError("The download arrived damaged (its checksum doesn't match). Try again.")
    root = app.resolve()
    n = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            rel_path = name.split("/", 1)[1] if "/" in name else name       # strip "ResearchAssistant/"
            parts = Path(rel_path).parts
            if not parts or parts[0] in KEEP or (parts[0] == "tools" and len(parts) > 1 and parts[1] in KEEP_TOOLS):
                continue
            target = (app / rel_path).resolve()
            if root not in target.parents:
                raise RuntimeError(f"Unexpected path in the update: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
            n += 1
    return f"Updated to {current()} ({n} files)"
