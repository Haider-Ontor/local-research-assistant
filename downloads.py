"""Downloads that run in the background while you keep using the app: they resume where they stopped (after a lost
connection, or the next time you start the app), each file is checked against its published SHA-256 checksum, and the
app shows their progress. Also installs the AI engine (llama.cpp) from its zip files."""
import hashlib, json, os, shutil, threading, time, uuid, zipfile
from pathlib import Path
import requests
import config, hub

CHUNK = 1 << 20
RETRIES = 8
_jobs = {}
_lock = threading.Lock()


class Cancelled(Exception):
    pass


class Job:
    """One download of one or more files, run in its own thread. `after(job)` runs once all files are in place."""

    def __init__(self, title, files, after=None, kind="model"):
        self.id, self.title, self.files, self.after, self.kind = uuid.uuid4().hex[:8], title, files, after, kind
        self.total = sum(f.get("size") or 0 for f in files)
        self.done, self.speed, self.status, self.error, self.message = 0, 0.0, "queued", "", ""
        self.started, self.finished = time.time(), None
        self._stop = threading.Event()
        self._window = []                         # (time, bytes) for the speed shown in the app
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"download {title}")

    # ---------------------------------------------------------------- what the app shows
    @property
    def active(self):
        return self.status in ("queued", "running")

    def fraction(self):
        return min(1.0, self.done / self.total) if self.total else 0.0

    def eta(self):
        """Seconds left at the current speed, or None."""
        return (self.total - self.done) / self.speed if self.speed > 0 and self.total else None

    def cancel(self):
        self._stop.set()

    # ---------------------------------------------------------------- the work
    def _run(self):
        self.status = "running"
        try:
            self._check_space()
            for f in self.files:
                self._fetch(f)
            if self.after:
                self.message = "Finishing…"
                self.after(self)
            self.status, self.message = "done", ""
        except Cancelled:
            self.status, self.message = "cancelled", "Stopped. Start it again to continue where it left off."
        except Exception as e:
            self.status, self.error = "error", str(e)
        self.finished = time.time()

    def _check_space(self):
        dest = Path(self.files[0]["dest"]).parent
        dest.mkdir(parents=True, exist_ok=True)
        need = sum((f.get("size") or 0) - _have(f) for f in self.files)
        free = shutil.disk_usage(dest).free
        if need > free - 2 ** 30:
            raise RuntimeError(f"Not enough free disk space on {dest.anchor}: {need / 2 ** 30:.1f} GB needed, "
                               f"{free / 2 ** 30:.1f} GB free.")

    def _progress(self, n):
        self.done += n
        now = time.time()
        self._window.append((now, self.done))
        while len(self._window) > 2 and now - self._window[0][0] > 8:
            self._window.pop(0)
        t0, d0 = self._window[0]
        if now - t0 > 0.5:
            self.speed = (self.done - d0) / (now - t0)

    def _fetch(self, f):
        dest, size, url = Path(f["dest"]), f.get("size"), f["url"]
        name = dest.name
        if dest.exists() and (not size or dest.stat().st_size == size):
            self._progress(dest.stat().st_size if not size else size)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        sha = hashlib.sha256()
        have = part.stat().st_size if part.exists() else 0
        if size and have > size:
            part.unlink()
            have = 0
        if have:                                  # carry on from an earlier attempt: checksum what's already here
            self.message = f"Checking the part of {name} downloaded before…"
            with open(part, "rb") as fh:
                for chunk in iter(lambda: fh.read(8 * CHUNK), b""):
                    sha.update(chunk)
                    if self._stop.is_set():
                        raise Cancelled
            self._progress(have)
        attempt = 0
        while not (size and have >= size):
            self.message = f"Downloading {name}"
            headers = {"User-Agent": hub.UA}
            if url.startswith(hub.HF) and hub.token():
                headers["Authorization"] = f"Bearer {hub.token()}"
            if have:
                headers["Range"] = f"bytes={have}-"
            try:
                with requests.get(url, headers=headers, stream=True, timeout=(20, 60)) as r:
                    if r.status_code in (401, 403) and url.startswith(hub.HF):
                        raise RuntimeError(f"Hugging Face refused {name}: this model needs you to accept its licence "
                                           "on its Hugging Face page and add an access token (🧠 Models tab).")
                    if r.status_code == 416 and size and have >= size:
                        break
                    r.raise_for_status()
                    if have and r.status_code == 200:     # the server ignored "carry on from": start again
                        self._progress(-have)
                        have, sha = 0, hashlib.sha256()
                    with open(part, "ab" if have else "wb") as out:
                        for chunk in r.iter_content(CHUNK):
                            if self._stop.is_set():
                                raise Cancelled
                            out.write(chunk)
                            sha.update(chunk)
                            have += len(chunk)
                            self._progress(len(chunk))
                attempt = 0
                if not size:
                    break
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                attempt += 1
                if attempt > RETRIES:
                    raise RuntimeError(f"The connection keeps dropping ({e.__class__.__name__}). What's downloaded "
                                       "so far is kept - start the download again later to continue.") from e
                for s in range(min(60, 2 ** attempt), 0, -1):
                    self.message = f"Connection lost - trying again in {s} s (attempt {attempt} of {RETRIES})"
                    if self._stop.wait(1):
                        raise Cancelled
        if size and have != size:
            raise RuntimeError(f"{name}: got {have:,} bytes, expected {size:,}.")
        if f.get("sha256") and sha.hexdigest() != f["sha256"].lower():
            part.unlink()
            raise RuntimeError(f"{name} arrived damaged (its checksum doesn't match), so it was deleted. "
                               "Start the download again.")
        part.replace(dest)


def _have(f):
    dest = Path(f["dest"])
    if dest.exists() and (not f.get("size") or dest.stat().st_size == f["size"]):
        return f.get("size") or 0
    part = dest.with_name(dest.name + ".part")
    return part.stat().st_size if part.exists() else 0


# ---------------------------------------------------------------- the list of downloads
def start(title, files, after=None, kind="model"):
    """Start a download (or return the one already running with this title)."""
    with _lock:
        for j in _jobs.values():
            if j.title == title and j.active:
                return j
        job = Job(title, files, after, kind)
        _jobs[job.id] = job
    job.thread.start()
    return job


def jobs():
    return sorted(_jobs.values(), key=lambda j: j.started)


def active():
    return [j for j in jobs() if j.active]


def forget_finished():
    with _lock:
        for k in [k for k, j in _jobs.items() if not j.active]:
            del _jobs[k]


def partial_size(files):
    return sum(_have(f) for f in files)


# ---------------------------------------------------------------- the AI engine
BUILD_FILE = "build.json"


def engine_info():
    """{'tag', 'build'} of the installed AI engine, or None if there isn't one."""
    eng = Path(config.ENGINE_DIR)
    if not (eng / "llama-server.exe").exists():
        return None
    try:
        return json.loads((eng / BUILD_FILE).read_text("utf-8"))
    except Exception:          # installed by hand: work out what it is from its files
        build = ("cuda" if any(eng.glob("ggml-cuda*.dll")) else "vulkan" if any(eng.glob("ggml-vulkan*.dll"))
                 else "cpu")
        if build == "cuda":
            build = "cuda-13" if any(eng.glob("cudart64_13*.dll")) else "cuda-12"
        return {"tag": "own", "build": build}


def install_engine(build):
    """Download and install the AI engine `build` (see hardware.engine_build). Replaces any engine already there."""
    import catalog
    files = catalog.engine_files(build)
    tag = catalog.load()["engine"]["tag"]
    return start(f"AI engine ({build})", files, after=lambda job: _unpack_engine(job, build, tag, files), kind="engine")


def _unpack_engine(job, build, tag, files):
    import models, services
    eng = Path(config.ENGINE_DIR)
    new, old = eng.with_name("engine.new"), eng.with_name("engine.old")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    for f in files:
        job.message = f"Unpacking {Path(f['dest']).name}…"
        with zipfile.ZipFile(f["dest"]) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            tops = {n.split("/", 1)[0] for n in names}
            strip = len(tops) == 1 and all("/" in n for n in names)        # everything inside one folder
            for n in names:
                rel = n.split("/", 1)[1] if strip else n
                target = (new / rel).resolve()
                if new.resolve() not in target.parents:
                    raise RuntimeError(f"Unexpected path in {Path(f['dest']).name}: {n}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    if not (new / "llama-server.exe").exists():
        raise RuntimeError("The engine download doesn't contain llama-server.exe.")
    (new / BUILD_FILE).write_text(json.dumps({"tag": tag, "build": build}), "utf-8")
    if eng.exists():                               # swap in the new engine: the old one has to stop first
        job.message = "Stopping the old engine…"
        services.stop_all()
        services.stop_chat()
        for pid in services.engine_pids():
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        shutil.rmtree(old, ignore_errors=True)
        for _ in range(20):
            try:
                eng.rename(old)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("The old engine is still in use - quit the app, start it again and retry.")
    new.rename(eng)
    shutil.rmtree(old, ignore_errors=True)
    for f in files:
        Path(f["dest"]).unlink(missing_ok=True)
    models._GPU.clear()                            # what the engine can use may have changed
