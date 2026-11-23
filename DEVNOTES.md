# Developer notes (handoff)

State of the project, so work can continue in a new session without the chat history.

## Releases
- v1.0.0 one-click install (uv bootstrap, first-run setup, 🧠 Models tab, hardware-based model suggestions).
- v1.1.x a Rewrite tab; from v1.3.0 it is a private add-on kept off GitHub (writing.py, ui_writing.py and
  DEVNOTES-private.md are git-ignored; the tab shows when local_settings.py has REWRITE_TAB = True).

## Testing
- UI: `streamlit.testing.v1.AppTest.from_file("app.py")`.

## Library coverage + paper files (2026-11-18)
- `paper_files.py`: one Markdown file per paper in `data/papers_md/` + `INDEX.md`; called at the end of
  `indexer.update_index` (rewrites only changed papers, removes stale ones). `paper_files.export()` to run by hand.
- Ask (app.py): search with per_paper=1 over the whole library first (best passage of each paper), then 2nd passages;
  `rag.expand_hits(..., wide=3)` widens only the top 3 so ~10 papers fit in a 16k context (was ~3).
  Papers that matched but didn't fit: `msg["also"]`, shown by `also_relevant()`.
- Follow-up questions only borrow the previous question's words when they start like a follow-up ("and", "what
  about", "it", ...), so the same question always searches the same way.

## Installer + updates (v1.2.0, 2026-11-22)
- `VERSION` holds the version. Release = bump VERSION, commit, `git tag vX.Y.Z`, push the tag: GitHub Actions
  (`.github/workflows/release.yml`) builds `ResearchAssistant-Setup.exe` with Inno Setup
  (`installer/ResearchAssistant.iss`, per-user install in %LOCALAPPDATA%\ResearchAssistant, no admin) and
  `ResearchAssistant.zip` (git archive), creating the release from `installer/RELEASE_NOTES.md` if needed.
- `updater.py` + `ui_setup.update_panel()` (sidebar): checks the latest GitHub release (cached 6 h), installs the zip
  over the app (sha256-checked; never touches data, engine, models, .venv, tools/uv, tools/python, local_settings.py),
  or runs `git pull --ff-only` in a git checkout. New requirements install on the next start (install.ps1 hash check).

## Installer does the whole setup (v1.2.1, 2026-11-22)
- The installer's [Code] (ssPostInstall) runs `tools\install.ps1 -NoShortcut` (Python + libraries) and then
  `start.py --install-engine` (hardware.engine_build -> downloads.install_engine, waits), hidden, output appended to
  `install-log.txt`. On failure it says the app finishes on first start (the .bat and setup page still do both).
- llama.cpp needs msvcp140/vcruntime140(_1).dll. The workflow copies Visual Studio's redist CRT DLLs into
  `runtime\x64` and `runtime\arm64` (git-ignored) before building; they go into the installer and are appended to
  ResearchAssistant.zip. `downloads.add_runtime()` copies them next to the engine (on engine install and in
  `services.ensure_llm`); it does nothing when `runtime\` is absent (git checkouts).
- Installer: ArchitecturesAllowed/InstallIn64BitMode=x64compatible (64-bit {sys}/{cmd}), MinVersion=10.0.
- `RedirectionGuard=no` in the .iss: Setup's children inherit Inno Setup's RedirectionGuard mitigation, and uv's
  Python minor-version junction then fails with error 448 (v1.2.1; ExecAsOriginalUser didn't help in v1.2.2 because a
  non-elevated Setup runs it like Exec). The mitigation matters for elevated installs; this one is per-user.
- hardware.detect(): if nvidia-smi fails (it said "insufficient permissions" from the Claude desktop shell), the
  NVIDIA card comes from the registry and its CUDA version from DriverVersion (32.0.15.8180 -> 581.80). Before, an
  AMD/Intel integrated GPU listed first made the NVIDIA card be skipped -> cpu engine (fixed in 1.2.4).
- Tested by a silent install on the laptop into %TEMP% (setup /VERYSILENT /DIR=... /TASKS=""), then unins000 /VERYSILENT.

## Public download link + Rewrite made private (v1.3.0, 2026-11-22)
- The installer is named `ResearchAssistant-Setup.exe` (no version), so https://github.com/Haider-Ontor/local-research-assistant/releases/latest/download/ResearchAssistant-Setup.exe
  always gives the newest one; README starts with that link.
- Rewrite tab: see the Releases line above. Old releases (<= v1.2.4) and git history still contain writing.py.

## AMD Radeon + Intel Arc (v1.4.0, 2026-11-23)
- hardware._classify: AMD names without RX/Pro/VII/FirePro/Instinct = integrated (Ryzen graphics; UMA carve-out can be
  large, so the name decides); Intel < 2 GB own memory = integrated. Integrated total = own + RAM/2 (Windows' shared
  limit); models.gpu() replaces it with the engine's own figure (`llama-server --list-devices`). Usable: cards >= 2 GB;
  integrated Radeon or Arc with >= 7.5 GB RAM; never Intel UHD/Iris Xe. Order: card > integrated, NVIDIA, memory.
- services._device(): `--device <id>` for both servers when the engine lists 2+ devices (else llama.cpp splits the
  model over iGPU + dGPU); `--device none` when no usable chip. Device matched to the Windows name by shared words.
- catalog: rocm (llama-*-win-rocm-*.zip) and sycl builds, optional; default for AMD/Intel stays vulkan.
- Laptop test: Ryzen 7 260 / Radeon 780M (Vulkan0, 8327 MiB) ran Qwen3.5-9B Q4_K_M fully on the iGPU:
  pp1024 143 t/s, tg 5.7 t/s (processor only: pp512 64, tg 4.0). Integrated graphics keeps 1.5 GB spare (models.plan)
  and uses the engine's free figure: without that, chat + search model + vision ran out of shared memory. (This shell can't see the RTX 5060 through Vulkan or nvidia-smi - sandbox.)
