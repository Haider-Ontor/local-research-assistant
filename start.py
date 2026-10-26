"""One-click start: AI engine, index any new papers, open the app in your browser.

Everything started here lives in a Windows job tied to this window: close the window (or press Quit in the app,
or leave the browser tab closed for a while) and the engine and app stop - nothing stays in the background.
"""
import os, socket, subprocess, sys, time, webbrowser
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


def main():
    print("=== Research Assistant ===\n")
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
    try:
        import proposal
        if services.api_up() and proposal.brief_is_stale():
            print("Writing a brief of your PhD proposal for the AI to keep in mind (about a minute)…")
            b = proposal.make_brief()
            print(f"Proposal brief ready ({len(b['text'].split())} words).")
    except Exception as e:
        print("WARNING: proposal brief not updated -", e)
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
