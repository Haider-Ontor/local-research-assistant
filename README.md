# Research Assistant

A private, offline "ChatPDF" for your research library. It reads every PDF in your Zotero library and in your
papers folder, answers questions with citations to the exact paper and page, explains what the papers mean, writes
study guides, and formats references in IEEE, Harvard, APA 7 or BibTeX.
The AI (Qwen 3.5 9B) runs on your own GPU; nothing leaves your computer except metadata lookups on Crossref/arXiv
for PDFs that aren't in Zotero.

PDFs are read as layout-aware Markdown (pymupdf4llm): two-column pages in the right order, tables as real
tables (every value under its column), section headings kept with each passage, page numbers kept for citing.
(MarkItDown was tested too: on two-column papers it mixes the columns and loses page numbers, so it isn't used.)

## Start and stop
- **Start:** double-click **Start Research Assistant.bat** (or a Desktop shortcut to it). A small window opens, the
  AI engine loads (~5 s), any new papers are indexed, and the app opens in your browser (http://127.0.0.1:8510).
- **Stop:** press **Quit** at the bottom of the sidebar, or close the small window. It also stops by itself
  10 minutes after you close the browser tab.
- **Nothing runs in the background.** The app starts its own AI engine (llama.cpp, in the `engine` folder)
  and Windows shuts it down with the app. LM Studio is not needed (it can stay installed for other use).
- The app only accepts connections from this computer, not from other devices on your Wi-Fi.

## Adding papers
- **Zotero:** add papers as usual. Metadata (title, authors, journal, DOI) comes straight from Zotero.
- **Folder:** drop PDFs into your papers folder (`PAPERS_DIR`; subfolders are fine). Details are looked up from
  the DOI printed in the PDF; if a citation looks wrong, open the paper in the Library tab and paste its DOI.
  Patents, theses and reports without a DOI get guessed details - add those to Zotero for proper citations.
- Click **Check for new papers** in the sidebar (or just restart). Only new/changed files are processed.

## Tabs
- **Ask** - chat with your papers (see answer styles below). Under each answer, **Sources** shows the exact
  passage, **🔍 Show the page** shows the real PDF page with the passage highlighted (figures, tables and
  equations included), **📖 Open** opens it in Zotero at that page, and **❝ Cite** gives the citation.
- **Find** - instant search across every page, no AI needed. Best for "which papers talk about X?".
- **Library** - every paper: citations in all styles, closest papers in your library, a **📘 Study guide**,
  and a page viewer.
- **Bibliography** - collect references with **+ Bib**, reorder, copy, or download as .txt / .bib.

## Answer styles (sidebar)
- **📘 Explain (starts in ~2 s, ~30 s in full)** - the answer, then *What this means* (terms, symbols, units,
  intuition), *How they got it* (method), and *Why it matters* for your work.
- **🔬 Careful (~45 s)** - a short answer; the model thinks first (capped) so numbers stay with the right paper.
- **⚡ Quick answer (~10 s)** - for simple look-ups.

## How to tell what came from your papers
- Sentences with a number like **[2]** (answers) or **(p. 4)** (study guides) come from your papers.
  Sentences without one are the model's own explanation from general knowledge - useful, but check it.
- **Automatic number check:** every number in a cited sentence is compared with the cited passage/page.
  Numbers found in no source are **underlined in orange**; numbers that are in a *different* source than the
  one cited get a **dotted blue** underline (hover to see which source - cite that one's page). It can't catch a
  real number from the page attached to the wrong quantity - so for anything going into your thesis, open the page.
- The model is small (9B) and can still slip on details, physics or equations. `REFERENCE_NOTES` in
  `local_settings.py` is a short list of **reference facts** it must follow - add a line whenever you catch it
  getting something wrong (`local_settings.example.py` has a set for RF/microwave dielectric sensing).

## Your PhD proposal (optional)
Set `PROPOSAL_DIR` in `local_settings.py` and the newest file with "proposal" in its name in that folder is built
into the assistant (your file is only ever copied, never changed):
- A **brief** of it (aim, objectives, methods and work packages, key evidence, risks, timeline) is given to the AI
  with every question, so Explain answers and study guides relate papers to *your* objectives ("this informs
  O2/WP2"). Its numbers are checked against your draft.
- The **full draft** is searchable as "📝 your proposal draft" - ask "what does my proposal say about X?" -
  but it is never cited or used as evidence.
- Sidebar → **📝 Your PhD proposal** shows exactly what the AI knows, which file it read, and which of your draft's
  references are not in your library yet. Save a new version and it's picked up when the app starts (or press
  **Re-read my proposal now**).
- This is not retraining the model: retraining a 9B model on one document needs far more GPU than a laptop has,
  tends to make the model misremember the details, and would have to be redone after every edit.

## Study guides
Library → pick a paper → **📘 Create a study guide** (1-2 minutes, saved). Sections: in one paragraph, the
problem, key concepts (glossary), method step by step, equations and models (with symbols and units, and the
page to look at - equations are clearest on the page itself), results, limitations, how it helps your work.

## Checks it does for you
- **Duplicates**: the same paper saved twice (two Zotero items, or Zotero + folder) is indexed once.
  PDFs under the same Zotero item (e.g. supplementary files) are kept separately.
- **Wrong PDF attached in Zotero**: if a PDF's printed DOI and title belong to a different paper than its Zotero
  item, it is cited by its own DOI and flagged in the Library tab.
- **Loose PDFs**: a DOI found on page 1-2 is only trusted if that DOI's title is printed there too.

## Settings
- **`local_settings.py`** (your own, never uploaded - copy `local_settings.example.py` to start):
  `PAPERS_DIR`, `PROPOSAL_DIR`, `RESEARCH_CONTEXT` (a few lines about your work; Explain mode and study guides
  relate findings to it), `REFERENCE_NOTES`, `CHAT_MODEL_NAME`, and any other value from `config.py`.
- **`config.py`** (defaults): default citation style, passages per answer, `AUTO_QUIT_MINUTES`, `CONTEXT_LENGTH`,
  `REASONING_BUDGET` (caps how long the model thinks in Careful mode and for study guides).
- Restart the app after a change.

## Setup on a new computer
Needs Windows 10/11, an NVIDIA GPU with 8 GB (an RTX 5060 Laptop GPU was used for testing), Python 3.13, and about
7 GB of disk for the engine and models. Zotero and Microsoft Word are optional.

1. **Code and Python packages** - in a terminal:
   ```
   git clone https://github.com/Haider-Ontor/local-research-assistant.git ResearchAssistant
   cd ResearchAssistant
   py -3.13 -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
2. **AI engine** - from [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases) download the Windows
   CUDA build (`llama-<build>-bin-win-cuda-13.x-x64.zip`) and the matching `cudart-llama-bin-win-cuda-13.x-x64.zip`,
   and extract both into a folder named `engine` here, so that `engine\llama-server.exe` exists.
   Tested with build b11899.
3. **Models** - into a folder named `models` here:
   - a Qwen3.5-9B **Q4_K_M** GGUF (for example from
     [unsloth/Qwen3.5-9B-GGUF](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) or
     [bartowski/Qwen_Qwen3.5-9B-GGUF](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF)), renamed to
     `qwen3.5-9b-q4_k_m.gguf` - or keep its name and set `CHAT_MODEL_NAME` in `local_settings.py`;
   - `nomic-embed-text-v1.5.Q4_K_M.gguf` from
     [nomic-ai/nomic-embed-text-v1.5-GGUF](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF).

   If LM Studio already has a model, a hard link avoids a second copy:
   `mklink /H models\qwen3.5-9b-q4_k_m.gguf "<path to LM Studio's .gguf>"` (in cmd).
4. **Your settings** - copy `local_settings.example.py` to `local_settings.py` and set your papers folder,
   proposal folder (optional) and a few lines about your research.
5. **Start** - double-click `Start Research Assistant.bat`. The first start indexes every paper (a few seconds per
   PDF). For a Desktop shortcut: right-click the .bat → Send to → Desktop, then give it `icon.ico`.

## What is not in this repository
Only the code is on GitHub. These stay on your computer (see `.gitignore`):
`local_settings.py` (your folders and research context) · `data\` (the search index with text from your papers,
the Zotero database copy, your bibliography, the copy and brief of your proposal, engine logs) · `engine\` and
`models\` (large downloads, see above) · `.venv\`.

## Files
`app.py` (interface) · `rag.py` (search, answers, guides, page images, number check) · `indexer.py` (PDF
processing) · `library.py` (Zotero, Crossref, arXiv) · `citations.py` (styles) · `proposal.py` (your proposal
draft and its brief) · `services.py` (AI engine) · `start.py` (startup) · `config.py` (defaults) ·
`local_settings.example.py` (template for your own settings) · `data\index.db` (the index; delete it to rebuild) ·
`data\bibliography.json` (your reference list) · `data\engine-*.log` (engine logs).
