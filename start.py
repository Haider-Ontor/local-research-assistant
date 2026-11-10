"""One-click start: AI engine, index any new papers, open the app in your browser.

Everything started here lives in a Windows job tied to this window: close the window (or press Quit in the app,
or leave the browser tab closed for a while) and the engine and app stop - nothing stays in the background.

    python start.py                      start with the model you picked last time
    python start.py --list-models        show the models found on this computer
    python start.py --model gemma        start with (and remember) the model whose file name contains "gemma"
    python start.py --model NAME --no-vision     ... without its vision part (saves GPU memory)
"""
import os, socket, subprocess, sys, time, webbrowser
from pathlib import Path
import config, services, indexer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

URL = f"http://127.0.0.1:{config.APP_PORT}"        # the app only accepts connections from this laptop


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def model_options():
    """--list-models / --model NAME [--no-vision]. Returns False when the app shouldn't start."""
    import models
    args = sys.argv[1:]
    if "--list-models" in args:
        found = models.scan(refresh=True)
        if not found:
            print("No models found. Put a GGUF chat model in", config.MODELS_DIR, "or download one in LM Studio.")
        for m in found:
            print(f"\n{models.label(m)}\n  {m['path']}")
            for line in models.explain(m, models.plan(m)):
                print("  ·", line)
        return False
    if "--model" in args:
        i = args.index("--model")
        name = args[i + 1] if i + 1 < len(args) else ""
        hits = models.search(models.scan(), name) if name else []
        if len(hits) != 1:
            print(f'"{name}" matches {len(hits)} models - be more specific. Models found:' if hits else
                  f'No model matches "{name}". Models found:')
            for m in hits or models.scan():
                print("  ", Path(m["path"]).name)
            return False
        vision = "--no-vision" not in args
        models.apply(hits[0]["path"], vision)
        models.save_choice(hits[0]["path"], vision)
        print(f"Model: {models.short(hits[0])}" + ("" if vision else " (vision off)") + " - remembered for next time.")
    return True


def main():
    print("=== Research Assistant ===\n")
    if not model_options():
        return
    if port_open(config.APP_PORT):
        print("Already running - opening it in your browser.")
        webbrowser.open(URL)
        return
    try:
        services.ensure_llm()
    except Exception as e:
        print("WARNING: the AI engine didn't start -", e, "\n(Search still works; answers need the engine.)")
    print("\nChecking for new or changed papers…")
    try:
        indexer.update_index()
    except Exception as e:
        print("WARNING: index not updated -", e)
    print("\nStarting the app…")
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(config.APP_DIR / "app.py"),
                             "--server.port", str(config.APP_PORT), "--server.address", "127.0.0.1",
                             "--server.headless", "true",
                             "--browser.gatherUsageStats", "false"], cwd=str(config.APP_DIR))
    services.adopt(proc)
    for _ in range(60):
        if port_open(config.APP_PORT):
            break
        time.sleep(0.5)
    if not os.environ.get("RA_NO_BROWSER"):
        webbrowser.open(URL)
    print(f"\nResearch Assistant is running at {URL}")
    print("To stop it: press Quit in the app's sidebar, or close this window.")
    print(f"It also stops by itself {config.AUTO_QUIT_MINUTES} minutes after you close the browser tab.\n")
    try:
        proc.wait()
    finally:
        services.stop_all()


if __name__ == "__main__":
    main()
