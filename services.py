"""The app's own AI engine: llama.cpp's llama-server, started with the app and stopped with it.

Two small servers run while the app is open - one for Qwen 3.5 (answers, and looking at figures), one for
nomic-embed (search).
They are placed in a Windows "job object" that Windows kills automatically when the process that started
them ends, so nothing is left running in the background - even if the window is closed abruptly.
"""
import ctypes, os, subprocess, time
from ctypes import wintypes
import requests
import config

NO_WINDOW = 0x08000000
_procs = {}
_job = None


# ---------------------------------------------------------------- Windows job object (kill children on exit)
class _IO(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rt", "wt", "ot")]


class _BASIC(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _EXTENDED(ctypes.Structure):
    _fields_ = [("Basic", _BASIC), ("Io", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _job_handle():
    global _job
    if _job is None and os.name == "nt":
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        job = k32.CreateJobObjectW(None, None)
        info = _EXTENDED()
        info.Basic.LimitFlags = 0x2000                       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        _job = job
    return _job


def adopt(proc):
    """Tie a child process's life to this process (Windows kills it when we exit)."""
    job = _job_handle()
    if job:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject(job, int(proc._handle))


# ---------------------------------------------------------------- engine
def _up(port):
    try:
        return requests.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200
    except requests.RequestException:
        return False


def api_up():
    """True when both the answer model and the search model are loaded and ready."""
    return _up(config.CHAT_PORT) and _up(config.EMBED_PORT)


def chat_plan():
    """(model info, how to run it) for the chosen answer model - see models.plan()."""
    import models
    m = models.lookup(config.CHAT_MODEL_FILE)
    if not m or m.get("kind") != "chat":
        return m, {"ctx": config.CONTEXT_SETTING, "vision": False, "mmproj": None, "mmproj_gpu": False,
                   "ngl": "auto", "fit": "unknown", "gpu": None}
    if not m.get("mmproj") and config.MMPROJ_FILE.exists() and config.CHAT_MODEL_FILE.name == config.CHAT_MODEL_NAME:
        m = dict(m, mmproj={"path": str(config.MMPROJ_FILE), "size": config.MMPROJ_FILE.stat().st_size})
    return m, models.plan(m, config.VISION_ON)


def _server_args(kind):
    exe = str(config.ENGINE_DIR / "llama-server.exe")
    common = ["--host", "127.0.0.1", "--no-webui"]
    if kind == "chat":
        _, p = chat_plan()
        config.CONTEXT_LENGTH = p["ctx"]
        vision = ["--no-mmproj"]
        if p["mmproj"]:
            vision = ["--mmproj", p["mmproj"], "--image-max-tokens", str(config.VISION_IMAGE_TOKENS)]
            if not p["mmproj_gpu"]:
                vision.append("--no-mmproj-offload")          # vision encoder on the CPU, to leave the GPU to the model
        # whole model on the GPU when it fits; otherwise the engine works out how many layers fit (the rest runs on
        # the CPU), keeping 512 MB free for the search model
        gpu = (["-ngl", "999"] if p["ngl"] == "999" else ["-ngl", "0"] if p["fit"] == "cpu"
               else ["--fit", "on", "--fit-target", "512"])
        return [exe, "-m", str(config.CHAT_MODEL_FILE), "--alias", config.CHAT_MODEL, "--port", str(config.CHAT_PORT),
                "--jinja", "-c", str(p["ctx"]), "-np", "1", "-fa", "on",
                "--reasoning-budget", str(config.REASONING_BUDGET),
                "--reasoning-budget-message", "I have thought about this enough. Now I will write the answer.",
                "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", *vision, *gpu, *common]
    return [exe, "-m", str(config.EMBED_MODEL_FILE), "--alias", config.EMBED_MODEL, "--port", str(config.EMBED_PORT),
            "--embedding", "-c", "8192", "-np", "4", "-b", "2048", "-ub", "2048", "-ngl", "999", *common]


def _start(kind, args):
    log_file = open(config.DATA_DIR / f"engine-{kind}.log", "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(args, cwd=str(config.ENGINE_DIR), stdout=log_file, stderr=subprocess.STDOUT,
                         creationflags=NO_WINDOW)
    adopt(p)
    _procs[kind] = p


def ensure_llm(log=print, timeout=240):
    """Start whichever engine servers aren't running yet and wait until they're ready."""
    if api_up():
        return
    import models
    if not (config.ENGINE_DIR / "llama-server.exe").exists():
        raise RuntimeError(f"The AI engine isn't installed yet ({config.ENGINE_DIR}): open the app and use its "
                           "first-run setup, or the 🧠 Models tab, to download it.")
    if not config.EMBED_MODEL_FILE.exists():
        raise RuntimeError(f"The search model isn't downloaded yet ({config.EMBED_MODEL_FILE.name}): open the app and "
                           "use its first-run setup, or the 🧠 Models tab.")
    if not config.CHAT_MODEL_FILE.exists():                   # first start, or the chosen file was moved
        pick = models.best_default(models.scan())
        if not pick:
            raise RuntimeError("No AI model found. Download one in the app's 🧠 Models tab (it suggests one that fits "
                               f"this computer), or put a GGUF chat model in {config.MODELS_DIR}.")
        log(f"{config.CHAT_MODEL_FILE.name} isn't there - using {models.short(pick)} instead "
            "(pick another one under 🧠 AI model in the app's sidebar).")
        models.apply(pick["path"], config.VISION_ON)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _up(config.EMBED_PORT) and not ("embed" in _procs and _procs["embed"].poll() is None):
        _start("embed", _server_args("embed"))
    if not _up(config.CHAT_PORT) and not ("chat" in _procs and _procs["chat"].poll() is None):
        args = _server_args("chat")
        if "--fit" in args:                    # it measures free GPU memory, so let the search model load first
            t0 = time.time()
            while not _up(config.EMBED_PORT) and time.time() - t0 < 60:
                time.sleep(0.5)
        m, p = chat_plan()
        log(f"Loading {models.short(m) if m else config.CHAT_MODEL_FILE.name} ({p['ctx']:,} tokens"
            + (", vision" if p["mmproj"] else "")
            + (", on the processor" if p["fit"] == "cpu" else ", partly on the CPU" if p["ngl"] != "999" else "") + ")…")
        _start("chat", args)
    log("Loading the AI models…")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if api_up():
            log(f"AI engine ready ({time.time() - t0:.0f} s).")
            return
        for kind, p in _procs.items():
            if p.poll() is not None:
                tail = (config.DATA_DIR / f"engine-{kind}.log").read_text("utf-8", "replace")[-800:]
                raise RuntimeError(f"The {kind} engine stopped while starting. Last log lines: {tail}")
        time.sleep(0.5)
    raise RuntimeError("The AI engine took too long to start (see data\\engine-chat.log).")


def stop_all():
    for p in _procs.values():
        if p.poll() is None:
            p.terminate()
    _procs.clear()


def _chat_server_pids():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
                          f"? {{ $_.CommandLine -like '*--port {config.CHAT_PORT} *' }} | % {{ $_.ProcessId }}"],
                         capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
    return [int(x) for x in out.split() if x.isdigit()]


def stop_chat(timeout=20):
    """Stop the answer engine (whichever process started it); the search engine keeps running."""
    p = _procs.pop("chat", None)
    if p and p.poll() is None:
        p.terminate()
    for pid in _chat_server_pids():
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    t0 = time.time()
    while time.time() - t0 < timeout and (_up(config.CHAT_PORT) or _chat_server_pids()):
        time.sleep(0.5)


def switch_chat(path, vision=True, log=print):
    """Load another answer model while the app keeps running. If it won't start, the previous one is restored."""
    import models
    prev = (config.CHAT_MODEL_FILE, config.VISION_ON)
    stop_chat()
    models.apply(path, vision)
    try:
        ensure_llm(log=log, timeout=300)
    except Exception as e:
        stop_chat()
        models.apply(*prev)
        try:
            ensure_llm(log=lambda *_: None)
        except Exception:
            pass
        raise RuntimeError(f"{models.short({'path': str(path)})} didn't start, so the app went back to "
                           f"{models.short({'path': str(prev[0])})}. {e}") from e
    models.save_choice(path, vision)


def engine_pids():
    """PIDs of any llama-server processes belonging to this app."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
                          f"? {{ $_.ExecutablePath -like '{config.ENGINE_DIR}*' }} | % {{ $_.ProcessId }}"],
                         capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
    return [int(x) for x in out.split() if x.isdigit()]
