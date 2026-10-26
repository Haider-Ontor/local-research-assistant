"""The app's own AI engine: llama.cpp's llama-server, started with the app and stopped with it.

Two small servers run while the app is open - one for Qwen3 (answers), one for nomic-embed (search).
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


def _server_args(kind):
    exe = str(config.ENGINE_DIR / "llama-server.exe")
    common = ["--host", "127.0.0.1", "--no-webui", "-ngl", "999"]
    if kind == "chat":
        return [exe, "-m", str(config.CHAT_MODEL_FILE), "--alias", config.CHAT_MODEL, "--port", str(config.CHAT_PORT),
                "--jinja", "-c", str(config.CONTEXT_LENGTH), "-np", "1", "-fa", "on",
                "--reasoning-budget", str(config.REASONING_BUDGET),
                "--reasoning-budget-message", "I have thought about this enough. Now I will write the answer.",
                "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", *common]
    return [exe, "-m", str(config.EMBED_MODEL_FILE), "--alias", config.EMBED_MODEL, "--port", str(config.EMBED_PORT),
            "--embedding", "-c", "8192", "-np", "4", "-b", "2048", "-ub", "2048", *common]


def ensure_llm(log=print, timeout=180):
    """Start whichever engine servers aren't running yet and wait until they're ready."""
    if api_up():
        return
    if not (config.ENGINE_DIR / "llama-server.exe").exists():
        raise RuntimeError(f"The AI engine is missing from {config.ENGINE_DIR}.")
    for f in (config.CHAT_MODEL_FILE, config.EMBED_MODEL_FILE):
        if not f.exists():
            raise RuntimeError(f"Model file not found: {f}")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    for kind, port in (("chat", config.CHAT_PORT), ("embed", config.EMBED_PORT)):
        if _up(port) or (kind in _procs and _procs[kind].poll() is None):
            continue
        log_file = open(config.DATA_DIR / f"engine-{kind}.log", "w", encoding="utf-8", errors="replace")
        p = subprocess.Popen(_server_args(kind), cwd=str(config.ENGINE_DIR), stdout=log_file,
                             stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        adopt(p)
        _procs[kind] = p
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


def engine_pids():
    """PIDs of any llama-server processes belonging to this app."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
                          f"? {{ $_.ExecutablePath -like '{config.ENGINE_DIR}*' }} | % {{ $_.ProcessId }}"],
                         capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
    return [int(x) for x in out.split() if x.isdigit()]
