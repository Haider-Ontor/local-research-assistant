## Install (Windows 10/11, 64-bit)
1. Download **ResearchAssistant-Setup-1.2.0.exe** below and run it. No administrator rights are needed.
   It isn't code-signed, so Windows may say "Windows protected your PC": click *More info → Run anyway*.
   (The installer is built from this repository's source by GitHub Actions - see `.github/workflows/release.yml`.)
2. Choose a folder with space for AI models (1-20 GB), not one OneDrive syncs.
3. The first start installs Python and the libraries inside that folder (about 2 minutes). The setup page then
   suggests an AI model for your computer and downloads it with the AI engine.

Already using Research Assistant? Open it and press **⬆️ Update** in the sidebar (from 1.2.0 on). Older copies:
download `ResearchAssistant.zip` below and copy its files over the app folder, or run the installer into the same folder.

## New in 1.2.0
- **Windows installer** with Start-menu and desktop shortcuts and an uninstaller.
- **⬆️ Update button** in the sidebar: installs the newest release without touching your papers, settings or models.
- **Academic article mode** in ✍️ Rewrite: formal, cautious, objective, topic sentences first, key terms kept, citations
  kept, no essay linkers; one or two paragraphs per pasted paragraph.
- **Answers draw on the whole library**: the best passage of each paper first (about 10 papers per answer instead of
  3), and a list of the other papers that matched.
- **One readable Markdown file per paper** in `data\papers_md` (plus `INDEX.md`).

`ResearchAssistant.zip` is the same source, used by the Update button and for installing without the installer.
