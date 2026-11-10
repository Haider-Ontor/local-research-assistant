"""Rebuild catalog.json: the models the app suggests, the AI engine and the search model, with exact file sizes and
checksums. For maintainers - run it from the app folder with the app's Python:  .venv\\Scripts\\python tools\\update_catalog.py

Each model's description (layer layout, how much stays in normal memory, thinking style) is read from the start of
one of its files on Hugging Face, so the app can work out what fits a computer without downloading anything."""
import datetime, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import requests
import hub

ENGINE_TAG = "b11899"           # llama.cpp release the app is tested with
UV_VERSION = "0.13.7"
EMBED = ("nomic-ai/nomic-embed-text-v1.5-GGUF", "nomic-embed-text-v1.5.Q4_K_M.gguf")

# (repository, maker, rank, tested with this app, one line about it). Rank is a rough order of answer quality for
# reading scientific papers - bigger and newer first; models measured in this app get a small bonus in catalog.py.
MODELS = [
    ("unsloth/Qwen3.5-27B-GGUF", "Alibaba Qwen", 95, False, "Dense 27B model; very strong, needs a 24 GB GPU"),
    ("unsloth/gemma-4-31B-it-GGUF", "Google", 92, False, "Google's largest Gemma 4; needs a 24 GB GPU"),
    ("unsloth/Qwen3.5-35B-A3B-GGUF", "Alibaba Qwen", 90, False,
     "Mixture of experts: 35B knowledge, fast because only 3B work per word; needs a 24 GB GPU"),
    ("unsloth/gemma-4-26B-A4B-it-GGUF", "Google", 88, False,
     "Mixture of experts (26B, 4B active); needs a 16 GB GPU or more"),
    ("unsloth/Ministral-3-14B-Instruct-2512-GGUF", "Mistral AI", 82, False, "Mistral's 14B model with vision"),
    ("unsloth/Qwen3.5-9B-GGUF", "Alibaba Qwen", 80, True,
     "Best all-rounder for 8 GB GPUs: reads figures, thinks on request; the one this app was built with"),
    ("unsloth/gemma-4-12b-it-GGUF", "Google", 79, False, "Gemma 4 12B with vision; for 10-12 GB GPUs"),
    ("unsloth/gpt-oss-20b-GGUF", "OpenAI", 76, False, "OpenAI's open model; text only, always reasons first"),
    ("unsloth/GLM-4.6V-Flash-GGUF", "Z.ai", 74, True, "Vision model from Z.ai; reads charts and tables"),
    ("unsloth/Qwen3-VL-8B-Instruct-GGUF", "Alibaba Qwen", 71, False, "Qwen's previous vision model"),
    ("unsloth/Ministral-3-8B-Instruct-2512-GGUF", "Mistral AI", 70, False, "Mistral's 8B model with vision"),
    ("unsloth/gemma-4-E4B-it-GGUF", "Google", 70, True, "Small, quick Gemma 4 with vision; for 6 GB GPUs"),
    ("unsloth/Qwen3-8B-GGUF", "Alibaba Qwen", 66, True, "Text only (no figures); thinks on request"),
    ("unsloth/Qwen3.5-4B-GGUF", "Alibaba Qwen", 62, False,
     "Small model with vision: the best choice for computers without a graphics card or with 4 GB"),
    ("unsloth/gemma-4-E2B-it-GGUF", "Google", 50, False, "Very small Gemma 4 with vision"),
    ("unsloth/Phi-4-mini-instruct-GGUF", "Microsoft", 48, False, "Small, text only"),
    ("unsloth/Qwen3.5-2B-GGUF", "Alibaba Qwen", 45, False, "Tiny model with vision, for older computers"),
    ("unsloth/Qwen3.5-0.8B-GGUF", "Alibaba Qwen", 30, False, "Smallest; only for very weak computers"),
]


def github(path):
    r = requests.get(f"https://api.github.com/repos/{path}", headers={"User-Agent": hub.UA}, timeout=30)
    r.raise_for_status()
    return r.json()


def asset(a):
    return {"name": a["name"], "url": a["browser_download_url"], "size": a["size"],
            "sha256": (a.get("digest") or "").removeprefix("sha256:") or None}


def engine():
    rel = github(f"ggml-org/llama.cpp/releases/tags/{ENGINE_TAG}")
    by = {a["name"]: asset(a) for a in rel["assets"]}
    find = lambda pat, cpu="x64": next(v for k, v in by.items() if k.startswith(pat) and k.endswith(f"-{cpu}.zip"))
    builds = {
        "cuda-13": [find(f"llama-{ENGINE_TAG}-bin-win-cuda-13"), find("cudart-llama-bin-win-cuda-13")],
        "cuda-12": [find(f"llama-{ENGINE_TAG}-bin-win-cuda-12"), find("cudart-llama-bin-win-cuda-12")],
        "vulkan": [find(f"llama-{ENGINE_TAG}-bin-win-vulkan")],
        "cpu": [find(f"llama-{ENGINE_TAG}-bin-win-cpu")],
        "cpu-arm64": [find(f"llama-{ENGINE_TAG}-bin-win-cpu", "arm64")],
    }
    try:
        builds["vulkan-arm64"] = [find(f"llama-{ENGINE_TAG}-bin-win-vulkan", "arm64")]
    except StopIteration:
        builds["vulkan-arm64"] = builds["cpu-arm64"]
    return {"tag": ENGINE_TAG, "builds": builds}


def uv():
    rel = github(f"astral-sh/uv/releases/tags/{UV_VERSION}")
    by = {a["name"]: asset(a) for a in rel["assets"]}
    return {"version": UV_VERSION, "x64": by["uv-x86_64-pc-windows-msvc.zip"], "arm64": by["uv-aarch64-pc-windows-msvc.zip"]}


def embed():
    r = hub.repo(EMBED[0])
    f = next(f for f in r["files"] if f["path"] == EMBED[1])
    return {"repo": EMBED[0], "path": f["path"], "size": f["size"], "sha256": f["sha256"], "license": r["license"]}


def model(repo_id, maker, rank, tested, about):
    r = hub.repo(repo_id)
    versions, vision = hub.group(r["files"])
    versions = [v for v in versions if hub.quality(v["quant"]) is not None]
    probe = min(versions, key=lambda v: (abs(hub.quality(v["quant"])), v["size"]))   # Q4_K_M or closest
    info = hub.describe_remote(repo_id, probe, r["files"])
    keep = ("arch", "basename", "params", "ctx_train", "embed", "thinking", "kv", "cpu_fraction")
    return {"repo": repo_id, "name": repo_id.split("/")[1].removesuffix("-GGUF").replace("-it", "").replace("-Instruct", ""),
            "maker": maker, "rank": rank, "tested": tested, "about": about, "license": r["license"],
            "gated": bool(r["gated"]), "info": {k: info.get(k) for k in keep},
            "versions": [{k: v[k] for k in ("quant", "path", "parts", "sizes", "size", "sha256s")} for v in versions],
            "vision": {k: vision[0][k] for k in ("path", "size", "sha256")} if vision else None}


if __name__ == "__main__":
    out = {"generated": datetime.date.today().isoformat(), "engine": engine(), "uv": uv(), "embed": embed(), "models": []}
    for row in MODELS:
        try:
            m = model(*row)
            out["models"].append(m)
            print(f"{m['name']:34} {m['info']['arch']:10} ctx={m['info']['ctx_train']:>7} {m['info']['thinking']:10} "
                  f"cpu={m['info']['cpu_fraction']:.3f} vision={'yes' if m['vision'] else 'no '} "
                  f"versions={[v['quant'] for v in m['versions']]}")
        except Exception as e:
            print(f"!! {row[0]}: {e}")
    (ROOT / "catalog.json").write_text(json.dumps(out, indent=1), "utf-8")
    print(f"wrote catalog.json: {len(out['models'])} models")
