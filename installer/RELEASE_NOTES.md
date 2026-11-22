## Install (Windows 10/11, 64-bit)
1. Download **ResearchAssistant-Setup.exe** below (or any time from https://github.com/Haider-Ontor/local-research-assistant/releases/latest/download/ResearchAssistant-Setup.exe) and run it.
   No administrator rights are needed, and nothing has to be installed first (no Python, Git or Visual C++).
   It isn't code-signed, so Windows may say "Windows protected your PC": click *More info → Run anyway*.
   (The installer is built from this repository's source by GitHub Actions - see `.github/workflows/release.yml`.)
2. Choose a folder with space for AI models (1-20 GB), not one OneDrive syncs.
3. The installer copies the app, installs Python and its libraries inside that folder and downloads the AI engine
   that suits your graphics card (NVIDIA, AMD, Intel or none) - a few minutes on a normal connection.
4. The app opens in your browser: choose your papers folder and press **Download and set up** for the AI model it
   suggests for your computer.

Already using Research Assistant 1.2.0 or later? Press **⬆️ Update** in the sidebar. Older copies: run this installer.

## New in 1.3.0
- The installer is now always called `ResearchAssistant-Setup.exe`, with a direct download link at the top of the README.
- The ✍️ Rewrite tab is no longer part of the open-source app.

## Since 1.2.0
- The installer alone sets everything up on any Windows 10/11 PC: Python, the libraries, the AI engine for your
  graphics card, and the Microsoft C++ runtime the engine needs. If the internet drops, the app finishes on first start.
- NVIDIA graphics cards are recognised even when `nvidia-smi` can't run.
- ⬆️ Update button in the sidebar; answers draw on the whole library; one readable Markdown file per paper.

`ResearchAssistant.zip` is the same app (with the C++ runtime), used by the Update button and for installing by hand.
