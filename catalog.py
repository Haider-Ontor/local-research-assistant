"""The models the app suggests (catalog.json, made by tools/update_catalog.py) and which of them suits this computer.

Every model gets the best version (Q6_K, Q5_K_M, Q4_K_M, ...) that runs well here - all of it on the graphics card, or
on the processor when there's no usable card - and the models are ordered by a rough quality rank for reading papers
plus that version's quality. Nothing is downloaded to work this out: the catalogue holds each model's layout."""
import json
from pathlib import Path
import config, hub, models

FILE = Path(__file__).with_name("catalog.json")
TESTED_BONUS = 2      # measured in this app: its memory estimate and its answers are known to be good
NO_VISION_PENALTY = 6     # when you want figure explanations, a text-only model has to be clearly better
_C = {}


def load():
    if "c" not in _C:
        _C["c"] = json.loads(FILE.read_text("utf-8"))
    return _C["c"]


def entry(repo_id):
    return next((e for e in load()["models"] if e["repo"].lower() == repo_id.lower()), None)


def options(vision=True):
    """Every catalogue model with the version that suits this computer, best first:
    [{'e': entry, 'v': version or None (doesn't fit), 'p': plan, 'score': float or None}]."""
    out = []
    for e in load()["models"]:
        vf = e["vision"] if vision else None
        v, p, s = hub.best_version(e["info"], e["versions"], vf)
        if v is None:      # doesn't fit: describe its smallest sensible version
            v0 = min(e["versions"], key=lambda x: x["size"])
            out.append({"e": e, "v": None, "smallest": v0, "p": models.plan(hub.as_model(e["info"], v0, vf), bool(vf)),
                        "score": None})
            continue
        score = e["rank"] + s + (TESTED_BONUS if e["tested"] else 0) - (NO_VISION_PENALTY if vision and not vf else 0)
        out.append({"e": e, "v": v, "p": p, "score": score})
    out.sort(key=lambda o: (o["score"] is not None, o["score"] or -o["smallest"]["size"]), reverse=True)
    return out


def recommend(vision=True):
    """The option the app suggests for this computer (None if not even the smallest model fits)."""
    return next((o for o in options(vision) if o["v"]), None)


def as_model(o, vision=True):
    """A models.scan()-style description of an option, for models.explain()."""
    e, v = o["e"], o["v"] or o["smallest"]
    m = hub.as_model(e["info"], v, e["vision"] if vision else None)
    return dict(m, path=str(hub.download_dir(e["repo"]) / v["path"]), quant=v["quant"], source="Hugging Face",
                params=e["info"].get("params") or "")


def find_local(e, v, vision=True):
    """(model path, vision path or None) if this version is already on this computer - downloaded by the app, or in
    LM Studio's folders - else None."""
    d = hub.download_dir(e["repo"])
    want_vision = vision and e["vision"]
    if (d / v["path"]).exists() and all((d / p).exists() for p in v["parts"]):
        vp = d / e["vision"]["path"] if want_vision else None
        if not want_vision or vp.exists():
            return str(d / v["path"]), str(vp) if vp else None
    name = Path(v["path"]).name.lower()
    for m in models.scan():
        if Path(m["path"]).name.lower() == name and m["size"] == v["size"]:
            if not want_vision:
                return m["path"], None
            if m.get("mmproj"):
                return m["path"], m["mmproj"]["path"]
    return None


def download_files(e, v, vision=True):
    """What to download for this version (all parts, plus the vision file): [{'url', 'dest', 'size', 'sha256'}]."""
    d = hub.download_dir(e["repo"])
    files = [{"url": hub.file_url(e["repo"], p), "dest": str(d / p), "size": size, "sha256": sha}
             for p, size, sha in zip(v["parts"], v.get("sizes") or [v["size"]], v["sha256s"])]
    if vision and e["vision"]:
        files.append({"url": hub.file_url(e["repo"], e["vision"]["path"]), "dest": str(d / e["vision"]["path"]),
                      "size": e["vision"]["size"], "sha256": e["vision"]["sha256"]})
    return files


def download_size(e, v, vision=True):
    return v["size"] + (e["vision"]["size"] if vision and e["vision"] else 0)


def embed_files():
    em = load()["embed"]
    return [{"url": hub.file_url(em["repo"], em["path"]), "dest": str(config.EMBED_MODEL_FILE), "size": em["size"],
             "sha256": em["sha256"]}]


def engine_files(build):
    """The AI engine download for a build name from hardware.engine_build()."""
    eng = load()["engine"]
    return [{"url": a["url"], "dest": str(config.DATA_DIR / "downloads" / a["name"]), "size": a["size"],
             "sha256": a["sha256"]} for a in eng["builds"][build]]
