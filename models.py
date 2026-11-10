"""The local AI models you can run, and which one answers your questions.

Any GGUF chat model works. The app looks in its own models folder, in LM Studio's model folder and in MODEL_DIRS
(local_settings.py). For each model it reads the file's own description (architecture, size, context length, whether
it can "think"), finds its vision file if it has one, and estimates whether it fits in your GPU's memory.

Only the answer model can be changed here. The search model (nomic-embed) stays fixed: changing it would mean
re-indexing the whole library.
"""
import json, os, re, struct, subprocess, time
from pathlib import Path
import config

CACHE = config.DATA_DIR / "model_info.json"
QUANTS = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
          12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
          19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
          26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
          36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4"}
EMBED_ARCHS = {"bert", "nomic-bert", "nomic-bert-moe", "jina-bert-v2", "jina-bert-v3", "t5encoder", "modern-bert",
               "neo-bert", "eurobert"}
_QUANT_RE = re.compile(r"(?i)[-_.](?:i?q\d(?:_[0-9a-z]+)*|bf16|f16|f32|fp16|mxfp4(?:_moe)?)$")
_SPLIT = re.compile(r"(?i)-(\d{5})-of-(\d{5})\.gguf$")
_NOISE = {"gguf", "mmproj", "model", "instruct", "it", "chat", "hf", "lmstudio", "community"}
_SIZE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_FMT = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
KV_BYTES = 1.0625          # q8_0 cache: 34 bytes per 32 values
BASE_MIB = 650             # CUDA context, compute buffers and the search model, measured on an 8 GB RTX laptop GPU
VISION_EXTRA_MIB = 150     # the vision encoder's working memory on top of its file
CPU_TENSORS = ("token_embd.weight", "per_layer_token_embd.weight")    # llama.cpp keeps input embeddings in RAM


def _cpu_bytes(f, n_tensors, alignment, file_size):
    """Bytes of the input-embedding tensors, which llama.cpp keeps in normal memory instead of on the GPU."""
    string = lambda: f.read(struct.unpack("<Q", f.read(8))[0]).decode("utf-8", "replace")
    infos = []
    for _ in range(n_tensors):
        name = string()
        n_dims, = struct.unpack("<I", f.read(4))
        f.seek(8 * n_dims, 1)
        _, offset = struct.unpack("<IQ", f.read(12))
        infos.append((offset, name))
    start = -(-f.tell() // alignment) * alignment
    infos.sort()
    names = {n for _, n in infos}
    # tied embeddings (no output.weight): the output layer on the GPU reuses token_embd, so only the rest stays in RAM
    cpu = CPU_TENSORS if "output.weight" in names else tuple(t for t in CPU_TENSORS if t != "token_embd.weight")
    total = 0
    for i, (offset, name) in enumerate(infos):
        if name in cpu:
            end = infos[i + 1][0] if i + 1 < len(infos) else file_size - start
            total += end - offset
    return total


# ---------------------------------------------------------------- reading a GGUF file's description
def read_gguf(path, tensors=False):
    """The key/value header of a GGUF file (no weights are read). Long arrays such as the vocabulary are skipped.
    tensors=True also adds '_cpu_bytes': the size of the tensors that stay in normal memory."""
    with open(path, "rb") as f:
        return parse_header(f, tensors, os.path.getsize(path), split=bool(_SPLIT.search(Path(path).name)))


def parse_header(f, tensors=False, file_size=0, split=False):
    """read_gguf() on any file-like object with read/seek/tell (a local file, or hub.HttpFile for a file online)."""
    out = {}
    if f.read(4) != b"GGUF":
        return None
    version, = struct.unpack("<I", f.read(4))
    if version < 2:
        return None
    n_tensors, n_kv = struct.unpack("<QQ", f.read(16))

    def string():
        n, = struct.unpack("<Q", f.read(8))
        return f.read(n).decode("utf-8", "replace")

    for _ in range(n_kv):
        key = string()
        t, = struct.unpack("<I", f.read(4))
        if t == 8:
            val = string()
        elif t == 9:
            at, n = struct.unpack("<IQ", f.read(12))
            if at == 8:
                if n > 512:
                    for _ in range(n):
                        f.seek(struct.unpack("<Q", f.read(8))[0], 1)
                    val = None
                else:
                    val = [string() for _ in range(n)]
            elif at in _SIZE:
                if n > 512:
                    f.seek(_SIZE[at] * n, 1)
                    val = None
                else:
                    val = [struct.unpack(_FMT[at], f.read(_SIZE[at]))[0] for _ in range(n)]
            else:
                return out                        # nested arrays: nothing we need comes after them
        elif t in _FMT:
            val, = struct.unpack(_FMT[t], f.read(_SIZE[t]))
        else:
            return out
        if val is not None:
            out[key] = val
    if tensors and not split:
        try:
            out["_cpu_bytes"] = _cpu_bytes(f, n_tensors, int(out.get("general.alignment") or 32), file_size)
        except Exception:
            pass
    return out


def _file_size(path):
    """Size on disk, adding up all parts of a split model (model-00001-of-00003.gguf ...)."""
    path = Path(path)
    m = _SPLIT.search(path.name)
    if not m:
        return path.stat().st_size
    stem = path.name[:m.start()]
    return sum((path.parent / f"{stem}-{i:05d}-of-{m.group(2)}.gguf").stat().st_size
               for i in range(1, int(m.group(2)) + 1) if (path.parent / f"{stem}-{i:05d}-of-{m.group(2)}.gguf").exists())


def describe(path):
    """What kind of GGUF file this is and what's needed to run it."""
    md = read_gguf(path, tensors=True)
    return describe_header(md, str(path), _file_size(path)) if md else None


def describe_header(md, path, size):
    """describe() from an already-read header (`path` may be a URL; `size` = total bytes, all parts)."""
    arch = md.get("general.architecture", "")
    g = lambda k, d=None: md.get(f"{arch}.{k}", d)
    gtype = md.get("general.type", "model")
    if gtype == "mmproj" or arch == "clip":
        kind = "mmproj"
    elif arch in EMBED_ARCHS or g("pooling_type") is not None:
        kind = "embed"
    else:
        kind = "chat"
    tmpl = md.get("tokenizer.chat_template") or ""
    tmpl = tmpl if isinstance(tmpl, str) else " ".join(map(str, tmpl))
    heads = g("attention.head_count") or 0
    heads = max(heads) if isinstance(heads, list) else heads
    emb = int(g("embedding_length") or 0)
    k_len = g("attention.key_length") or (emb // heads if heads else 128)
    quant = QUANTS.get(md.get("general.file_type"))
    if not quant:
        m = _QUANT_RE.search(_SPLIT.sub("", Path(str(path).split("?")[0]).name).replace(".gguf", ""))
        quant = m.group(0)[1:].upper() if m else ""
    return {
        "path": str(path), "size": size, "cpu_bytes": md.get("_cpu_bytes", 0), "arch": arch, "kind": kind,
        "basename": (md.get("general.basename") or "").lower(),
        "params": md.get("general.size_label") or "", "quant": quant,
        "ctx_train": int(g("context_length") or 0), "embed": emb,
        "thinking": "switchable" if "enable_thinking" in tmpl else
                    "always" if "<think>" in tmpl or "reasoning_effort" in tmpl else "none",
        "kv": {"layers": int(g("block_count") or 0), "heads_kv": g("attention.head_count_kv") or heads,
               "k": k_len, "v": g("attention.value_length") or k_len,
               "k_swa": g("attention.key_length_swa"), "v_swa": g("attention.value_length_swa"),
               "window": g("attention.sliding_window"), "swa_pattern": g("attention.sliding_window_pattern"),
               "full_interval": g("full_attention_interval"), "shared": g("attention.shared_kv_layers") or 0,
               "recurrent": g("ssm.state_size") is not None},
        "vision": bool(md.get("clip.has_vision_encoder")) if kind == "mmproj" else False,
        "proj_dim": md.get("clip.vision.projection_dim") if kind == "mmproj" else None,
    }


# ---------------------------------------------------------------- finding models
def search_dirs():
    """[(label, folder)] searched for models, in order of preference."""
    home = Path.home()
    dirs = [("models folder", Path(config.MODELS_DIR)),
            ("LM Studio", home / ".lmstudio" / "models"),
            ("LM Studio", home / ".cache" / "lm-studio" / "models")]
    dirs += [("your folder", Path(d)) for d in (getattr(config, "MODEL_DIRS", None) or [])]
    out, seen = [], set()
    for label, d in dirs:
        key = os.path.normcase(str(d))
        if d.is_dir() and key not in seen:
            seen.add(key)
            out.append((label, d))
    return out


def _ggufs(root, depth=4):
    root = Path(root)
    for dirpath, dirnames, files in os.walk(root):
        if len(Path(dirpath).relative_to(root).parts) >= depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in files:
            if name.lower().endswith(".gguf"):
                m = _SPLIT.search(name)
                if m and m.group(1) != "00001":
                    continue
                yield Path(dirpath) / name


def _load_cache():
    try:
        return json.loads(CACHE.read_text("utf-8"))
    except Exception:
        return {}


def _save_cache(cache):
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache), "utf-8")
    except Exception:
        pass


def _tokens(path):
    stem = _QUANT_RE.sub("", Path(path).stem.lower())
    stem = _SPLIT.sub("", stem)
    return {t for t in re.split(r"[^a-z0-9]+", stem) if t and t not in _NOISE}


def short(m):
    """A readable model name from its file name: Qwen3.5-9B-Instruct-Q4_K_M.gguf -> Qwen3.5-9B-Instruct."""
    if not m:
        return "?"
    stem = Path(m["path"]).name
    stem = _SPLIT.sub("", stem)
    stem = re.sub(r"(?i)\.gguf$", "", stem)
    return _QUANT_RE.sub("", stem) or stem


def _vision_score(model, proj):
    """How sure we are that this vision file belongs to this model (0 = not at all)."""
    if proj.get("proj_dim") and model.get("embed") and proj["proj_dim"] != model["embed"]:
        return 0                                  # the projector must output the model's hidden size
    if proj.get("basename") and model.get("basename"):
        return 10 if proj["basename"] == model["basename"] else 0
    return len(_tokens(model["path"]) & _tokens(proj["path"]))


def _attach_vision(chats, projs):
    by_folder = {}
    for p in projs:
        if p["vision"]:
            by_folder.setdefault(os.path.normcase(str(Path(p["path"]).parent)), []).append(p)
    for m in chats:
        folder = os.path.normcase(str(Path(m["path"]).parent))
        cands = [(s, p) for p in by_folder.get(folder, []) if (s := _vision_score(m, p)) > 0]
        if not cands:
            m["mmproj"] = None
            continue
        best = max(cands, key=lambda c: (c[0], c[1]["size"]))
        m["mmproj"] = {"path": best[1]["path"], "size": best[1]["size"]}


def scan(refresh=False):
    """Every chat model found, best name first within each folder; duplicates (hard links, copies) listed once."""
    cache = {} if refresh else _load_cache()
    found, new_cache = [], {}
    for label, root in search_dirs():
        for path in _ggufs(root):
            try:
                st = path.stat()
            except OSError:
                continue
            key = str(path)
            c = cache.get(key)
            if c and c.get("mtime") == st.st_mtime and c.get("bytes") == st.st_size:
                info = c["info"]
            else:
                try:
                    info = describe(path)
                except Exception:
                    info = None
            new_cache[key] = {"mtime": st.st_mtime, "bytes": st.st_size, "info": info}
            if info:
                found.append(dict(info, source=label, ident=[st.st_dev, st.st_ino] if st.st_ino else [st.st_size]))
    _save_cache(new_cache)
    chats = [i for i in found if i["kind"] == "chat"]
    _attach_vision(chats, [i for i in found if i["kind"] == "mmproj"])
    merged = {}
    for m in chats:                               # the same file reached twice (e.g. a hard link): keep one entry
        key = tuple(m["ident"]) if len(m["ident"]) == 2 else (m["size"], m["arch"], m["quant"])
        prev = merged.get(key)
        if prev is None:
            merged[key] = dict(m, aliases=[m["path"]])
            continue
        prev["aliases"].append(m["path"])
        if not prev.get("mmproj") and m.get("mmproj"):
            prev["mmproj"] = m["mmproj"]
        if len(short(m)) > len(short(prev)) and (m.get("mmproj") or not prev.get("mmproj")):
            prev.update({k: m[k] for k in ("path", "source", "mmproj")})
    return sorted(merged.values(), key=lambda m: short(m).lower())


def same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def find(models, path):
    """The entry for a model file (also when it was reached through another path)."""
    if not path:
        return None
    for m in models:
        if any(same_file(a, path) for a in m.get("aliases", [m["path"]])):
            return m
    return None


def lookup(path, models=None):
    """Everything about one model file - from the scan if it's in a searched folder, else read directly."""
    models = scan() if models is None else models
    m = find(models, path)
    if m:
        return m
    if not Path(path).exists():
        return None
    m = describe(path)
    if not m or m["kind"] != "chat":
        return m
    projs = [p for p in (describe(q) for q in Path(path).parent.glob("*.gguf") if q.name.lower().startswith("mmproj"))
             if p and p["kind"] == "mmproj"]
    _attach_vision([m], projs)
    return dict(m, source="elsewhere", aliases=[m["path"]])


def search(models, text):
    """Models whose file name or name contains `text` (for start.py --model)."""
    p = Path(text)
    if p.exists():
        return [lookup(str(p), models)]
    t = text.lower()
    exact = [m for m in models if short(m).lower() == t or Path(m["path"]).name.lower() == t]
    return exact or [m for m in models if t in Path(m["path"]).name.lower() or t in short(m).lower()]


# ---------------------------------------------------------------- will it fit?
_GPU = {}


def gpu():
    """{'name', 'total' (MiB), ...} of the graphics card the AI runs on, or None (see hardware.detect)."""
    if "v" not in _GPU:
        import hardware
        engine = Path(config.ENGINE_DIR)
        cpu_engine = engine.exists() and not any(engine.glob("ggml-cuda*.dll")) and not any(engine.glob("ggml-vulkan*.dll"))
        _GPU["v"] = None if cpu_engine else hardware.detect()["gpu"]      # a CPU-only engine can't use the GPU
    return _GPU["v"]


def ram():
    """This computer's memory in GB."""
    import hardware
    return hardware.detect()["ram"]


def kv_mib(m, ctx):
    """Memory for the model's attention cache at `ctx` tokens (q8_0), following its layer layout."""
    k = m.get("kv") or {}
    layers = k.get("layers") or 0
    heads = k.get("heads_kv") or 0
    heads = heads if isinstance(heads, list) else [heads] * layers
    pattern = k.get("swa_pattern") if isinstance(k.get("swa_pattern"), list) else None
    every = k.get("full_interval") or 0
    total = 0.0
    for i in range(max(0, layers - (k.get("shared") or 0))):
        h = heads[i] if i < len(heads) else (heads[-1] if heads else 0)
        if not h or (every and (i + 1) % every):
            continue                              # no own attention cache (recurrent or shared layer)
        if pattern and i < len(pattern) and pattern[i] and k.get("window"):
            total += min(ctx, k["window"]) * h * ((k.get("k_swa") or k["k"]) + (k.get("v_swa") or k["v"]))
        else:
            total += ctx * h * (k["k"] + k["v"])
    return total * KV_BYTES / 2 ** 20 + (40 if k.get("recurrent") else 0)


def estimate(m, ctx, vision_on_gpu):
    mib = (m["size"] - m.get("cpu_bytes", 0)) / 2 ** 20 + kv_mib(m, ctx) + BASE_MIB
    if vision_on_gpu and m.get("mmproj"):
        mib += m["mmproj"]["size"] / 2 ** 20 + VISION_EXTRA_MIB
    return mib


def plan(m, vision=True):
    """How to run a model on this computer: context size, vision on GPU or CPU, all layers on the GPU or not.
    fit: 'ok', 'tight', 'cpu-vision' (vision encoder on the CPU to make room), 'too-big' (part of the model runs on the
    CPU - slow; or, without a GPU, it doesn't fit in memory), 'cpu' (no usable GPU: runs on the processor)."""
    want = int(getattr(config, "CONTEXT_SETTING", config.CONTEXT_LENGTH))
    cap = m.get("ctx_train") or want
    use_vision = bool(vision and m.get("mmproj"))
    out = {"vision": use_vision, "mmproj": m["mmproj"]["path"] if use_vision else None, "mmproj_gpu": use_vision,
           "ngl": "999", "gpu": gpu()}
    g = out["gpu"]
    if not g:          # runs on the processor: shorter prompts (it reads them slowly), and it must fit in RAM
        ctx = min(8192, want, cap)
        est = estimate(m, ctx, use_vision)
        out.update(ctx=ctx, est=est, mmproj_gpu=False, ngl="auto",
                   fit="cpu" if est + 1536 < ram() * 1024 * 0.75 else "too-big")
        return out
    limit = g["total"] - 350
    for ctx in sorted({min(want, cap), min(8192, want, cap)}, reverse=True):
        full = estimate(m, ctx, use_vision)
        if full <= limit:
            out.update(ctx=ctx, est=full, fit="tight" if full > g["total"] - 800 else "ok")
            return out
        if use_vision and estimate(m, ctx, False) <= limit:
            est = estimate(m, ctx, False)
            out.update(ctx=ctx, est=est, fit="tight" if est > g["total"] - 800 else "cpu-vision", mmproj_gpu=False)
            return out
    ctx = min(8192, want, cap)
    out.update(ctx=ctx, est=estimate(m, ctx, use_vision), fit="too-big", ngl="auto", mmproj_gpu=False)
    return out


def best_default(models):
    """For a first start without the configured model: the biggest model that fits completely."""
    def rank(m):
        p = plan(m)
        return (p["fit"] != "too-big", p["fit"] in ("ok", "tight", "cpu"), m["size"])
    return max(models, key=rank) if models else None


# ---------------------------------------------------------------- the current choice
def running():
    """What the answer engine is actually running: {'path', 'ctx', 'vision'}, or None if it isn't up."""
    import requests
    try:
        props = requests.get(f"http://127.0.0.1:{config.CHAT_PORT}/props", timeout=2).json()
    except Exception:
        return None
    return {"path": props.get("model_path"), "ctx": (props.get("default_generation_settings") or {}).get("n_ctx"),
            "vision": bool((props.get("modalities") or {}).get("vision"))}


def apply(path, vision=True):
    """Make `path` the answer model for this process (the engine must be (re)started to load it)."""
    config.CHAT_MODEL_FILE = Path(path)
    config.CHAT_MODEL = config.model_alias(path)
    config.VISION_ON = bool(vision) and config.VISION


def save_choice(path, vision=True):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CHOICE_FILE.write_text(json.dumps({"path": str(path), "vision": bool(vision), "saved": time.time()},
                                             indent=1), "utf-8")


# ---------------------------------------------------------------- words for the app
def label(m):
    bits = [short(m)] + [b for b in (m.get("params"), m.get("quant")) if b]
    return " · ".join(bits) + (" · 👁" if m.get("mmproj") else "")


THINKING = {"switchable": "🧠 Thinks first in 🔬 Careful mode, answers straight away otherwise",
            "always": "🧠 Always thinks before answering (answers start later)",
            "none": "No thinking: 🔬 Careful mode answers directly"}


def explain(m, p):
    """Lines describing a model and how it would run here."""
    gb = lambda mib: f"{mib / 1024:.1f}"
    lines = [" · ".join(b for b in (f"{m['params']} parameters" if m.get("params") else "", m.get("quant", ""),
                                     f"{m['size'] / 2 ** 30:.1f} GB", f"from {m['source']}") if b),
             f"Reads up to {p['ctx']:,} tokens at once" + (f" (the model allows {m['ctx_train']:,})"
                                                         if m.get("ctx_train", 0) > p["ctx"] else ""),
             THINKING[m.get("thinking", "none")]]
    if not m.get("mmproj"):
        lines.append("No vision: figure explanations need a model marked 👁")
    elif not p["vision"]:
        lines.append("👁 Vision is off")
    elif p["mmproj_gpu"] or p["fit"] == "cpu":
        lines.append(f"👁 Can look at figures (vision file {Path(m['mmproj']['path']).name})")
    else:
        lines.append("👁 Can look at figures - its vision part runs on the CPU to leave room on the GPU, so figure "
                     "explanations take longer")
    g = p["gpu"]
    fit = {"ok": f"✅ Fits your GPU: ≈{gb(p.get('est', 0))} of {gb(g['total'])} GB" if g else "",
           "tight": f"⚠️ Only just fits your GPU (≈{gb(p.get('est', 0))} of {gb(g['total'])} GB): close other "
                    "GPU-heavy apps" if g else "",
           "cpu-vision": f"✅ Fits your GPU with vision on the CPU (≈{gb(p.get('est', 0))} of {gb(g['total'])} GB)"
           if g else "",
           "too-big": f"🐢 Too big for your GPU (needs ≈{gb(p.get('est', 0))} of {gb(g['total'])} GB): part of it runs "
                      "on the CPU, so answers are several times slower" if g else
                      f"🐢 Too big for this computer's memory (needs ≈{gb(p.get('est', 0))} GB of {ram():.0f} GB)",
           "cpu": f"💻 Runs on the processor (no suitable graphics card): needs ≈{gb(p.get('est', 0))} GB of "
                  f"{ram():.0f} GB memory; answers take noticeably longer than with a GPU"}.get(p["fit"], "")
    return lines + [fit]
