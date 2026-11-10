"""Hugging Face: search GGUF models, list a repository's files, and read a model file's description over the internet
(only its first few MB) so the app can tell whether - and in which version - it fits your computer before you
download gigabytes."""
import json, os, re, threading
from pathlib import Path
from urllib.parse import quote
import requests
import config, models

HF = "https://huggingface.co"
UA = "ResearchAssistant/1.0 (local research app; github.com/Haider-Ontor/local-research-assistant)"
TOKEN_FILE = config.DATA_DIR / "hf_token.txt"
REMOTE_CACHE = config.DATA_DIR / "remote_info.json"
_LOCK = threading.Lock()

# How much each version keeps of the original model, relative to Q4_K_M (0). Versions below Q3 lose too much for
# scientific reading; Q8/F16 are too big for what they add. Unsloth's "UD-" versions are a little better than plain.
QUALITY = {"Q6_K": 3, "Q5_K_M": 2, "Q5_K_S": 1.5, "Q4_K_M": 0, "Q4_K_S": -1, "IQ4_XS": -2, "IQ4_NL": -2, "Q4_0": -3,
           "Q3_K_M": -8, "IQ3_M": -9, "Q3_K_S": -10}
FIT_SCORE = {"ok": 0, "cpu-vision": -4, "tight": -6, "cpu": 0}
CPU_GB_PENALTY = 10      # without a GPU every GB above 3 makes answers slower: prefer small versions and models


def quality(quant):
    """QUALITY score of a version name such as Q4_K_M or UD-Q4_K_XL, or None if it isn't one we'd suggest."""
    q = quant.upper()
    base = q.replace("UD-", "").replace("_XL", "_M")
    return QUALITY[base] + (0.5 if q.startswith("UD-") else 0) if base in QUALITY else None


# ---------------------------------------------------------------- connection
def token():
    t = os.environ.get("HF_TOKEN") or getattr(config, "HF_TOKEN", "") or ""
    if not t and TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text("utf-8").strip()
    return t


def save_token(t):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if t.strip():
        TOKEN_FILE.write_text(t.strip(), "utf-8")
    elif TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def headers():
    h = {"User-Agent": UA}
    if token():
        h["Authorization"] = f"Bearer {token()}"
    return h


def _get(url, **kw):
    r = requests.get(url, headers=headers(), timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def file_url(repo, path):
    return f"{HF}/{repo}/resolve/main/{quote(path)}"


# ---------------------------------------------------------------- searching and listing
def search(text, sort="downloads", limit=40):
    """GGUF repositories matching `text`: [{'id', 'downloads', 'likes', 'gated', 'updated', 'arch', 'params'}]."""
    params = {"search": text, "filter": "gguf", "sort": sort, "direction": -1, "limit": limit,
              "expand[]": ["gated", "downloads", "likes", "lastModified", "gguf"]}
    out = []
    for m in _get(f"{HF}/api/models", params=params):
        g = m.get("gguf") or {}
        out.append({"id": m["id"], "downloads": m.get("downloads", 0), "likes": m.get("likes", 0),
                    "gated": m.get("gated") or False, "updated": (m.get("lastModified") or "")[:10],
                    "arch": g.get("architecture", ""), "params": g.get("total") or 0})
    return out


def repo(repo_id):
    """Everything needed to choose a file: {'id', 'gated', 'license', 'arch', 'params', 'ctx', 'files': [...]}."""
    j = _get(f"{HF}/api/models/{repo_id}", params={"expand[]": ["gguf", "gated", "cardData", "downloads", "likes"]})
    tree = _get(f"{HF}/api/models/{repo_id}/tree/main", params={"recursive": "true"})
    files = [{"path": f["path"], "size": f.get("size") or 0, "sha256": (f.get("lfs") or {}).get("oid")}
             for f in tree if f.get("type") == "file" and f["path"].lower().endswith(".gguf")]
    g = j.get("gguf") or {}
    return {"id": repo_id, "gated": j.get("gated") or False, "license": (j.get("cardData") or {}).get("license") or "",
            "arch": g.get("architecture", ""), "params": g.get("total") or 0, "ctx": g.get("context_length") or 0,
            "downloads": j.get("downloads", 0), "likes": j.get("likes", 0), "files": files}


_SPLIT = re.compile(r"(?i)-(\d{5})-of-(\d{5})\.gguf$")


def quant_of(path):
    stem = _SPLIT.sub("", Path(path).name)
    stem = re.sub(r"(?i)\.gguf$", "", stem)
    m = re.search(r"(?i)(?:^|[-_.])((?:UD-)?(?:I?Q\d(?:_[0-9A-Z]+)*|BF16|F16|F32|MXFP4(?:_MOE)?))$", stem)
    return m.group(1).upper() if m else ""


def group(files):
    """GGUF files -> (model versions [{'path', 'parts', 'size', 'quant', 'sha256s'}], vision files [...])."""
    versions, vision = {}, []
    for f in files:
        name = Path(f["path"]).name
        if name.lower().startswith("mmproj"):
            vision.append(f)
            continue
        m = _SPLIT.search(f["path"])
        key = _SPLIT.sub("", f["path"]) if m else f["path"]
        v = versions.setdefault(key, {"path": None, "files": [], "size": 0, "quant": quant_of(f["path"])})
        v["files"].append(f)
        v["size"] += f["size"]
        if not m or m.group(1) == "00001":
            v["path"] = f["path"]
    out = []
    for v in versions.values():
        if v["path"]:
            fs = sorted(v.pop("files"), key=lambda f: f["path"])
            out.append(dict(v, parts=[f["path"] for f in fs], sizes=[f["size"] for f in fs],
                            sha256s=[f.get("sha256") for f in fs]))
    out.sort(key=lambda v: (-(quality(v["quant"]) if quality(v["quant"]) is not None else -99), v["size"]))
    # F16 vision files first (smaller than F32, same quality in practice)
    vision.sort(key=lambda f: ("f16" not in f["path"].lower(), "bf16" in f["path"].lower(), f["size"]))
    return out, vision


# ---------------------------------------------------------------- reading a file online
class HttpFile:
    """Read-only, seekable view of a file on a web server, fetched in 4 MB pieces (HTTP Range requests)."""
    BLOCK = 4 * 2 ** 20

    def __init__(self, url, size):
        self.url, self.size, self.pos, self.blocks = url, size, 0, {}
        self.session = requests.Session()
        self.session.headers.update(headers())

    def _block(self, i):
        if i not in self.blocks:
            a, b = i * self.BLOCK, min(self.size, (i + 1) * self.BLOCK) - 1
            r = self.session.get(self.url, headers={"Range": f"bytes={a}-{b}"}, timeout=60)
            r.raise_for_status()
            if r.history:      # Hugging Face sends files from a download server: go there directly next time
                self.url = r.url
                self.session = requests.Session()
                self.session.headers["User-Agent"] = UA     # the signed link is the permission; no token needed
            self.blocks[i] = r.content
        return self.blocks[i]

    def read(self, n):
        out = bytearray()
        while n > 0 and self.pos < self.size:
            i = self.pos // self.BLOCK
            block = self._block(i)
            chunk = block[self.pos - i * self.BLOCK:self.pos - i * self.BLOCK + n]
            if not chunk:
                break
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return bytes(out)

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def tell(self):
        return self.pos


def _cache():
    try:
        return json.loads(REMOTE_CACHE.read_text("utf-8"))
    except Exception:
        return {}


def describe_remote(repo_id, version, files):
    """models.describe() for a file on Hugging Face - reads only its header (a few MB). Cached on disk."""
    key = f"{repo_id}/{version['path']}"
    cache = _cache()
    if key in cache and cache[key].get("size") == version["size"]:
        return cache[key]
    first = next(f for f in files if f["path"] == version["path"])
    f = HttpFile(file_url(repo_id, version["path"]), first["size"])
    md = models.parse_header(f, tensors=True, file_size=first["size"], split=len(version["parts"]) > 1)
    if not md:
        raise ValueError("not a GGUF file")
    info = models.describe_header(md, file_url(repo_id, version["path"]), version["size"])
    info["cpu_fraction"] = info.get("cpu_bytes", 0) / max(first["size"], 1)
    with _LOCK:
        cache = _cache()
        cache[key] = info
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        REMOTE_CACHE.write_text(json.dumps(cache), "utf-8")
    return info


def as_model(info, version, vision_file=None):
    """A model description for models.plan(): this version's size, plus its vision file if wanted."""
    m = dict(info, size=version["size"], cpu_bytes=info.get("cpu_fraction", 0) * version["size"])
    m["mmproj"] = {"path": vision_file["path"], "size": vision_file["size"]} if vision_file else None
    return m


def best_version(info, versions, vision_file=None):
    """The version to suggest for this computer: the best quality that runs well here (all on the GPU, or on the
    processor when there's no usable GPU). Returns (version, plan, score) or (None, None, None)."""
    best = (None, None, None)
    for v in versions:
        q = quality(v["quant"])
        if q is None:
            continue
        p = models.plan(as_model(info, v, vision_file), vision=bool(vision_file))
        if p["fit"] not in FIT_SCORE:
            continue
        score = q + FIT_SCORE[p["fit"]]
        if p["fit"] == "cpu":
            score -= CPU_GB_PENALTY * max(0.0, v["size"] / 2 ** 30 - 3)
        if best[2] is None or score > best[2] or (score == best[2] and v["size"] < best[0]["size"]):
            best = (v, p, score)
    return best


def quick_fit(size, vision_size=0):
    """A rough fit before the file's description has been read: 'ok', 'tight', 'too-big' or 'cpu'."""
    g = models.gpu()
    if not g:
        return "cpu" if size + 1.5 * 2 ** 30 < models.ram() * 2 ** 30 * 0.7 else "too-big"
    mib = size / 2 ** 20 * 0.95 + 450 + models.BASE_MIB + (vision_size / 2 ** 20 + models.VISION_EXTRA_MIB
                                                          if vision_size else 0)
    return "ok" if mib <= g["total"] - 800 else "tight" if mib <= g["total"] - 350 else "too-big"


def download_dir(repo_id):
    """Where a repository's files go: models/<owner>/<repo>/ (like LM Studio), so vision files never clash."""
    owner, _, name = repo_id.partition("/")
    return Path(config.MODELS_DIR) / re.sub(r"[^\w.-]", "_", owner) / re.sub(r"[^\w.-]", "_", name)


def parse_link(link):
    """A Hugging Face page/file link or a direct .gguf URL -> {'repo', 'path'} or {'url', 'name'}."""
    link = link.strip()
    m = re.match(r"https?://(?:www\.)?(?:huggingface\.co|hf\.co)/([^/]+/[^/]+)(?:/(?:blob|resolve)/[^/]+/(.+?))?/?(?:\?.*)?$",
                 link)
    if m:
        return {"repo": m.group(1), "path": m.group(2)}
    if re.match(r"https?://", link) and link.split("?")[0].lower().endswith(".gguf"):
        return {"url": link, "name": link.split("?")[0].rsplit("/", 1)[-1]}
    if re.fullmatch(r"[\w.-]+/[\w.-]+", link):
        return {"repo": link, "path": None}
    return None
