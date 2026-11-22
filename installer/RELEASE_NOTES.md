## Install (Windows 10/11, 64-bit)
1. Download **ResearchAssistant-Setup-1.2.2.exe** below and run it. No administrator rights are needed, and nothing
   has to be installed first (no Python, Git or Visual C++).
   It isn't code-signed, so Windows may say "Windows protected your PC": click *More info → Run anyway*.
   (The installer is built from this repository's source by GitHub Actions - see `.github/workflows/release.yml`.)
2. Choose a folder with space for AI models (1-20 GB), not one OneDrive syncs.
3. The installer copies the app, installs Python and its libraries inside that folder and downloads the AI engine
   that suits your graphics card (NVIDIA, AMD, Intel or none) - about 5-10 minutes on a normal connection.
4. The app opens in your browser: choose your papers folder and press **Download and set up** for the AI model it
   suggests for your computer.

Already using Research Assistant 1.2.0 or later? Press **⬆️ Update** in the sidebar. Older copies: run this installer.

## New in 1.2.2 (and 1.2.1)
- **The installer alone is enough on any Windows 10/11 PC**: it sets up Python, the libraries and the AI engine
  during installation, and brings the Microsoft C++ runtime the AI engine needs (a fresh Windows often lacks it).
- If the internet drops during installation, the app finishes the setup on first start.
- 1.2.2 fixes 1.2.1's installer, whose Python setup was blocked by a Windows protection (error 448).

## New in 1.2.0
- Windows installer with Start-menu and desktop shortcuts and an uninstaller.
- ⬆️ Update button in the sidebar: installs the newest release without touching your papers, settings or models.
- Academic article mode in ✍️ Rewrite; answers that draw on the whole library; one readable Markdown file per paper.

`ResearchAssistant.zip` is the same app (with the C++ runtime), used by the Update button and for installing by hand.
