"""What this computer has to run AI models with: graphics card(s), their memory, RAM - and which build of the AI
engine (llama.cpp) suits it."""
import ctypes, os, platform, re, subprocess
from pathlib import Path

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


def _cuda_for_driver(d):
    """The CUDA version an NVIDIA driver runs: 580+ runs CUDA 13, 551+ CUDA 12.4, 525+ CUDA 12.0."""
    return (13, 0) if d >= 580 else (12, 4) if d >= 551 else (12, 0) if d >= 525 else None


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
                g["cuda"] = _cuda_for_driver(d)
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
                try:
                    drv = str(winreg.QueryValueEx(k, "DriverVersion")[0])
                except OSError:
                    drv = ""
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
        a = {"name": name, "vendor": vendor, "total": mem // 2 ** 20}
        digits = re.sub(r"\D", "", drv)[-5:]          # Windows' 32.0.15.8180 is NVIDIA driver 581.80
        if vendor == "nvidia":
            a["name"] = name.removeprefix("NVIDIA ")    # as nvidia-smi names it
        if vendor == "nvidia" and len(digits) == 5:
            a["driver"] = f"{digits[:3]}.{digits[3:]}"
            a["cuda"] = _cuda_for_driver(float(a["driver"]))
        out.append(a)
    return out


_AMD_CARD = re.compile(r"\bRX\b|\bPro\b|\bVII\b|FirePro|Instinct", re.I)    # a Radeon card, not Ryzen graphics


def _classify(g, ram_mib):
    """Integrated graphics (the Radeon in Ryzen processors, the Arc in Core Ultra ones) has little memory of its own
    but Windows lets it use up to half of the computer's memory as shared memory: count that, and mark it."""
    if g["vendor"] == "amd":
        g["integrated"] = not _AMD_CARD.search(g["name"])
    elif g["vendor"] == "intel":
        g["integrated"] = g["total"] < 2000          # Arc A/B-series cards have 4-24 GB of their own
        g["arc"] = "arc" in g["name"].lower()
    else:
        g["integrated"] = False
    if g["integrated"]:
        g["own"] = g["total"]
        g["total"] = g["total"] + ram_mib // 2
    return g


def _usable(g, ram):
    """Can the AI run on this graphics chip?"""
    if not g.get("integrated"):
        return g["total"] >= 2000                    # a graphics card with at least 2 GB
    if ram < 7.5:
        return False                                 # too little memory to share
    return g["vendor"] == "amd" or bool(g.get("arc"))  # any Radeon, any Arc - not Intel's older UHD / Iris Xe


def detect(refresh=False):
    """{'gpu': best graphics chip or None, 'gpus': all, 'ram': GB, 'cpu': name, 'arm': bool, 'engine': build}."""
    if "hw" in _CACHE and not refresh:
        return _CACHE["hw"]
    ram = ram_gb()
    gpus = _nvidia()
    smi = bool(gpus)                                  # nvidia-smi can fail (e.g. without permission to run it)
    names = {g["name"].lower() for g in gpus}
    for a in _windows_adapters():
        if a["vendor"] == "nvidia" and smi:
            continue                                  # nvidia-smi already gave the exact numbers
        if a["name"].lower() not in names and a["vendor"] != "other":
            gpus.append(a)
    for g in gpus:
        _classify(g, int(ram * 1024))
    usable = [g for g in gpus if _usable(g, ram)]
    # a graphics card beats integrated graphics; then NVIDIA (its engine is the fastest); then the most memory
    best = max(usable, key=lambda g: (not g.get("integrated"), g["vendor"] == "nvidia", g["total"]), default=None)
    arm = platform.machine().lower() in ("arm64", "aarch64")
    hw = {"gpu": best, "gpus": gpus, "ram": round(ram, 1), "cpu": cpu_name(), "arm": arm}
    hw["engine"], hw["engine_why"] = engine_build(hw)
    _CACHE["hw"] = hw
    return hw


def engine_build(hw):
    """(build name, reason) - which llama.cpp download suits this computer."""
    g = hw["gpu"]
    if hw["arm"]:
        return ("vulkan-arm64", "ARM computer with a graphics chip") if g else ("cpu-arm64", "ARM computer")
    if not g:
        old = any(x["vendor"] == "intel" and not x.get("arc") for x in hw.get("gpus", []))
        return "cpu", ("Intel's older integrated graphics (UHD / Iris Xe) is no faster than the processor, so the AI "
                       "runs on the processor" if old else
                       "no graphics chip the AI can use was found, so the AI runs on the processor")
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
    kind = "integrated graphics, sharing the computer's memory" if g.get("integrated") else "graphics card"
    if g["vendor"] == "amd":
        return "vulkan", f"{g['name']} ({kind}) - the Vulkan build runs on every AMD Radeon"
    if g["vendor"] == "intel":
        return "vulkan", f"{g['name']} ({kind}) - the Vulkan build runs on every Intel Arc"
    return "vulkan", f"{g['name']} - the Vulkan build works with most graphics chips"


def summary(hw=None):
    hw = hw or detect()
    g = hw["gpu"]
    gpu = (f"{g['name']} (integrated, up to {g['total'] / 1024:.0f} GB shared)" if g and g.get("integrated") else
           f"{g['name']} ({g['total'] / 1024:.0f} GB)" if g else "no graphics chip the AI can use")
    return f"{gpu} · {hw['ram']:.0f} GB RAM · {hw['cpu']}"


# ---------------------------------------------------------------- which device the AI engine runs on
_SKIP = {"r", "tm", "graphics", "gpu", "laptop", "nvidia", "geforce", "amd", "intel"}


def engine_devices(eng_dir):
    """The devices the installed AI engine can use: [{'id': 'Vulkan0', 'name', 'total' MiB, 'free' MiB}]."""
    exe = Path(eng_dir) / "llama-server.exe"
    if not exe.exists():
        return []
    key = (str(exe), exe.stat().st_mtime)
    if _CACHE.get("dev_key") == key:
        return _CACHE["dev"]
    try:
        r = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True, timeout=60,
                           cwd=str(exe.parent), creationflags=NO_WINDOW)
        text = (r.stdout or "") + (r.stderr or "")
    except Exception:
        text = ""
    devs = [{"id": m[1], "name": m[2].strip(), "total": int(m[3]), "free": int(m[4])}
            for m in re.finditer(r"^\s*([A-Za-z]+\d+): (.+?) \((\d+) MiB, (\d+) MiB free\)", text, re.M)]
    _CACHE.update(dev_key=key, dev=devs)
    return devs


def _words(name):
    return set(re.findall(r"[a-z0-9]+", name.lower())) - _SKIP


def match_device(g, devs):
    """The engine device that is graphics chip `g` (Windows and the engine name it a little differently)."""
    if not g or not devs:
        return None
    d = max(devs, key=lambda d: len(_words(d["name"]) & _words(g["name"])))
    return d if _words(d["name"]) & _words(g["name"]) else None


def pick_device(g, eng_dir):
    """--device for the AI engine. With two or more graphics chips (integrated Radeon or Arc plus a card, or cards of
    two makers) the engine would spread a model over all of them, which is slower: use only the chosen one. 'none'
    keeps it on the processor. None: leave it to the engine (one device, or no match)."""
    devs = engine_devices(eng_dir)
    if not devs:
        return None
    if not g:
        return "none"
    d = match_device(g, devs)
    return d["id"] if d and len(devs) > 1 else None
