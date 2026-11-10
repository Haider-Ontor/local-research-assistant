"""What this computer has to run AI models with: graphics card(s), their memory, RAM - and which build of the AI
engine (llama.cpp) suits it."""
import ctypes, os, platform, re, subprocess

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CACHE = {}


def _run(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW).stdout
    except Exception:
        return ""


def ram_gb():
    """Installed memory (GB)."""
    if os.name != "nt":
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
        except Exception:
            return 0.0

    class MEM(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
                   [(n, ctypes.c_ulonglong) for n in ("total", "avail", "a", "b", "c", "d", "e")]
    m = MEM()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.total / 2 ** 30


def cpu_name():
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "CPU"


def _nvidia():
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1].isdigit():
            gpus.append({"name": parts[0].replace("NVIDIA ", ""), "vendor": "nvidia", "total": int(parts[1]),
                         "driver": parts[2]})
    if gpus:
        # "CUDA Version: 12.8" (older drivers) or "CUDA UMD Version: 13.5" (newer)
        m = re.search(r"CUDA(?: UMD| Driver)? Version:\s*(\d+)\.(\d+)", _run(["nvidia-smi"]))
        for g in gpus:
            if m:
                g["cuda"] = (int(m.group(1)), int(m.group(2)))
            else:                                     # from the driver number: 580+ runs CUDA 13, 551+ CUDA 12.4
                d = float(re.match(r"\d+(\.\d+)?", g["driver"]).group(0)) if re.match(r"\d", g["driver"]) else 0
                g["cuda"] = (13, 0) if d >= 580 else (12, 4) if d >= 551 else (12, 0) if d >= 525 else None
    return gpus


def _windows_adapters():
    """Every display adapter Windows knows, with its dedicated memory (registry; WMI caps it at 4 GB)."""
    out = []
    if os.name != "nt":
        return out
    import winreg
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return out
    for i in range(64):
        try:
            sub = winreg.EnumKey(root, i)
        except OSError:
            break
        if not sub.isdigit():
            continue
        try:
            with winreg.OpenKey(root, sub) as k:
                name = winreg.QueryValueEx(k, "DriverDesc")[0]
                mem = 0
                for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                    try:
                        v = winreg.QueryValueEx(k, value)[0]
                        mem = int.from_bytes(v, "little") if isinstance(v, bytes) else int(v)
                        break
                    except OSError:
                        continue
        except OSError:
            continue
        low = name.lower()
        vendor = "nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low) else \
            "intel" if "intel" in low else "other"
        out.append({"name": name, "vendor": vendor, "total": mem // 2 ** 20})
    return out


def detect(refresh=False):
    """{'gpu': best graphics card or None, 'gpus': all, 'ram': GB, 'cpu': name, 'arm': bool, 'engine': build}."""
    if "hw" in _CACHE and not refresh:
        return _CACHE["hw"]
    gpus = _nvidia()
    names = {g["name"].lower() for g in gpus}
    for a in _windows_adapters():
        if a["vendor"] == "nvidia" and gpus:
            continue                                  # nvidia-smi already gave the exact numbers
        if a["name"].lower() not in names and a["vendor"] != "other":
            gpus.append(a)
    # an integrated GPU (a few hundred MB of its own memory) is no help - the CPU build is as fast
    usable = [g for g in gpus if g["total"] >= 2000]
    best = max(usable, key=lambda g: (g["vendor"] == "nvidia", g["total"]), default=None)
    arm = platform.machine().lower() in ("arm64", "aarch64")
    hw = {"gpu": best, "gpus": gpus, "ram": round(ram_gb(), 1), "cpu": cpu_name(), "arm": arm}
    hw["engine"], hw["engine_why"] = engine_build(hw)
    _CACHE["hw"] = hw
    return hw


def engine_build(hw):
    """(build name, reason) - which llama.cpp download suits this computer."""
    g = hw["gpu"]
    if hw["arm"]:
        return ("vulkan-arm64", "ARM computer with a graphics chip") if g else ("cpu-arm64", "ARM computer")
    if not g:
        return "cpu", "no graphics card with its own memory was found, so the AI runs on the processor"
    if g["vendor"] == "nvidia" and g.get("cuda"):
        major, minor = g["cuda"]
        if major >= 13:
            return "cuda-13", f"NVIDIA {g['name']}, driver supports CUDA {major}.{minor}"
        if (major, minor) >= (12, 4) and not re.search(r"RTX 50\d\d", g["name"]):
            return "cuda-12", f"NVIDIA {g['name']}, driver supports CUDA {major}.{minor}"
        return "vulkan", (f"NVIDIA {g['name']} with an older driver (CUDA {major}.{minor}) - the Vulkan build works "
                          "with it; updating the driver allows the faster CUDA build")
    if g["vendor"] == "nvidia":
        return "vulkan", f"NVIDIA {g['name']} (nvidia-smi not found)"
    return "vulkan", f"{g['name']} - the Vulkan build works with AMD and Intel graphics cards"


def summary(hw=None):
    hw = hw or detect()
    g = hw["gpu"]
    gpu = f"{g['name']} ({g['total'] / 1024:.0f} GB)" if g else "no dedicated graphics card"
    return f"{gpu} · {hw['ram']:.0f} GB RAM · {hw['cpu']}"
