# Research Assistant

A private, offline "ChatPDF" for your research library. It reads every PDF in your Zotero library and in your
papers folder, answers questions with citations to the exact paper and page, explains what the papers mean, writes
study guides, finds and explains tables and figures, and formats references in IEEE, Harvard, APA 7, BibTeX and
LaTeX (IEEE and Elsevier templates).
The AI runs on your own GPU - any local GGUF model you choose (Qwen 3.5 9B by default). Nothing leaves your computer
except metadata lookups on Crossref/arXiv for PDFs that aren't in Zotero.

PDFs are read as layout-aware Markdown (pymupdf4llm): two-column pages in the right order, tables as real
tables (every value under its column), section headings kept with each passage, page numbers kept for citing.
(MarkItDown was tested too: on two-column papers it mixes the columns and loses page numbers, so it isn't used.)

## Start and stop
- **Start:** double-click **Start Research Assistant.bat** (or a Desktop shortcut to it). A small window opens, the
  AI engine loads (~10 s), any new papers are indexed, and the app opens in your browser (http://127.0.0.1:8510).
- **Stop:** press **Quit** at the bottom of the sidebar, or close the small window. It also stops by itself
  10 minutes after you close the browser tab.
- **Nothing runs in the background.** The app starts its own AI engine (llama.cpp, in the `engine` folder)
  and Windows shuts it down with the app. LM Studio is not needed (its downloaded models can be used, though).
- The app only accepts connections from this computer, not from other devices on your Wi-Fi.

## Choosing the AI model
Sidebar → **🧠 AI model** lists every chat model on your computer: the `.gguf` files in the app's `models` folder, in
LM Studio's model folder, and in any folder you add to `MODEL_DIRS`. For each one it shows:
- its size and quantisation, how much text it can read at once, whether it can think before answering
  (🔬 Careful mode) and whether it can look at figures (👁 - it has a vision file next to it);
- whether it **fits your GPU**: the app reads each model's layout and estimates the GPU memory it needs (within
  ~0.15 GB of measured use on the test laptop). A model that only fits without its vision part runs that part on the
  CPU; a model too big for the GPU runs partly on the CPU (it works, but answers are several times slower).

Pick one and press **Use …**: the answer engine reloads with it in 10-20 s while the app keeps running, and your
choice is remembered. If the model won't start, the app goes back to the previous one and shows why. Switch
**👁 Vision** off to free GPU memory when you don't need figure explanations. Searching your papers always uses
nomic-embed, so your index stays valid whichever model you pick.

From a terminal: `python start.py --list-models` shows the models found; `python start.py --model gemma` starts
with (and remembers) the model whose file name contains "gemma"; add `--no-vision` to leave its vision part off.
The `.bat` passes these through too.

Tested with Qwen3.5-9B (default - best answers), Qwen3-8B, Gemma-4-E4B and GLM-4.6V-Flash, all Q4_K_M on an
8 GB GPU. Any model llama.cpp can run should work; models with a chat template that supports `enable_thinking`
think in Careful mode only.

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
  Switch to **🖼 Tables & figures** to search the captions of every table and figure instead.
- **Library** - every paper: citations in all styles (including LaTeX), closest papers in your library, a
  **📘 Study guide**, its **🖼 tables and figures**, and a page viewer.
- **Bibliography** - collect references with **+ Bib**, reorder, copy, or download as .txt / .bib - or, with a
  LaTeX style, as a template-ready `references.bib` or a `thebibliography` block.

## Tables and figures
Every captioned table and figure is found in each PDF (PyMuPDF's layout analysis matches each "Fig. 3" / "Table II"
caption to the picture or table next to it) and cropped from the page. Open one with **🔍 Open** to:
- see it full size, and for tables the **cells** as a table (⬇ CSV, opens in Excel);
- **⬇ PNG (300 dpi)**, **📖 open the PDF** at that page, and **❝ Cite** it ("[5, Fig. 3]", `\cite[Fig.~3]{key}`, plus
  a credit line for your own copy - reusing a published figure may need the publisher's permission);
- **🧠 Explain** it: the AI looks at the image, reads the caption and the passages that discuss it, and explains
  what it shows and what it means. **Ask** your own question about it too. Every number is checked against the whole
  paper: values read off the image by eye get a dotted grey underline (approximate), values written nowhere in
  the paper an orange one;
- **📋 Read the cells with AI** for tables printed as images (check the result against the image).

In Ask answers, a source passage that mentions "Fig. 3" or "Table 2" gets a **🖼 Show** toggle with those items.
Explaining figures needs a model marked 👁 with its vision switched on (see *Choosing the AI model*); set
`VISION = False` to turn vision off everywhere.

## LaTeX citations (IEEE and Elsevier)
Pick a LaTeX style under **Citation style** in the sidebar - **IEEE (IEEEtran)**, **Elsevier numbered** or
**Elsevier author–year** (both `elsarticle`). Every **❝ Cite** then gives:
- the command for your text: `\cite{smith2019microwave}` (or `\citep{...}` / `\citet{...}` for author-year), with
  the page or figure when you cite one (`\cite[p.~1240]{...}`, `\cite[Fig.~3]{...}`);
- the **BibTeX entry** tuned to that template's style file (IEEEtran has no DOI field, so the DOI goes in a note the
  way IEEE prints it; article numbers appear as "Art. no.");
- a ready **`\bibitem`** if you'd rather not use BibTeX.

The **Bibliography** tab gives the whole list as **references.bib** (use with `\bibliographystyle{IEEEtran}`,
`{elsarticle-num}` or `{elsarticle-harv}`) or as a **thebibliography** block, one `\cite{...}` for all of them, and
the preamble to use. Each paper keeps one citation key, unique across your library. Titles keep their capitalisation
from Zotero (braced, so the template can't lower-case "RF", "LSTM" or "pH"); accents and Greek letters are written
as LaTeX commands, so pdfLaTeX, XeLaTeX and Overleaf all accept them. With Elsevier's styles load `hyperref`
(their template already does): their DOI links need it.

## Answer styles (sidebar)
- **📘 Explain (starts in ~2 s, ~30 s in full)** - the answer, then *What this means* (terms, symbols, units,
  intuition), *How they got it* (method), and *Why it matters* for your work.
- **🔬 Careful (~45 s)** - a short answer; the model thinks first (capped) so numbers stay with the right paper.
- **⚡ Quick answer (~10 s)** - for simple look-ups.

(Times are for Qwen 3.5 9B on an 8 GB laptop GPU.)

## How to tell what came from your papers
- Sentences with a number like **[2]** (answers) or **(p. 4)** (study guides) come from your papers.
  Sentences without one are the model's own explanation from general knowledge - useful, but check it.
- **Automatic number check:** every number in a cited sentence is compared with the cited passage/page.
  Numbers found in no source are **underlined in orange**; numbers that are in a *different* source than the
  one cited get a **dotted blue** underline (hover to see which source - cite that one's page). It can't catch a
  real number from the page attached to the wrong quantity - so for anything going into your thesis, open the page.
- A small local model can still slip on details, physics or equations. `REFERENCE_NOTES` in `local_settings.py` is a
  short list of **reference facts** it must follow - add a line whenever you catch it getting something wrong
  (`local_settings.example.py` has a set for RF/microwave dielectric sensing).

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
  `PAPERS_DIR`, `RESEARCH_CONTEXT` (a few lines about your work; Explain mode and study guides
  relate findings to it), `REFERENCE_NOTES`, `MODEL_DIRS` (more folders with models), and any other value from
  `config.py`.
- **`config.py`** (defaults): default citation style, passages per answer, `AUTO_QUIT_MINUTES`, `CONTEXT_LENGTH`
  (lowered automatically for a model that needs it), `REASONING_BUDGET` (caps how long the model thinks in Careful
  mode and for study guides), `CHAT_MODEL_NAME` (the model used until you pick one in the app).
- Restart the app after a change.

## Setup on a new computer
Needs Windows 10/11, an NVIDIA GPU (8 GB for a 9B model; an RTX 5060 Laptop GPU was used for testing; smaller models
run on smaller GPUs), Python 3.13, and about 7 GB of disk for the engine and models. Zotero and Microsoft Word are
optional.

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
3. **Models** (`.gguf` files):
   - **a chat model** - any GGUF chat model. Recommended: Qwen3.5-9B **Q4_K_M** (for example from
     [unsloth/Qwen3.5-9B-GGUF](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) or
     [bartowski/Qwen_Qwen3.5-9B-GGUF](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF)), plus its vision file
     (`mmproj-….gguf`, same page) in the same folder if you want figure explanations. Put them in a folder named
     `models` here - or just download them in **LM Studio**: its models are found automatically. The first start
     uses the biggest model that fits your GPU; pick another under 🧠 AI model.
   - **the search model** `nomic-embed-text-v1.5.Q4_K_M.gguf` from
     [nomic-ai/nomic-embed-text-v1.5-GGUF](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF), into the
     `models` folder.
4. **Your settings** - copy `local_settings.example.py` to `local_settings.py` and set your papers folder
   and a few lines about your research.
5. **Start** - double-click `Start Research Assistant.bat`. The first start indexes every paper (a few seconds per
   PDF). For a Desktop shortcut: right-click the .bat → Send to → Desktop, then give it `icon.ico`.

## What is not in this repository
Only the code is on GitHub. These stay on your computer (see `.gitignore`):
`local_settings.py` (your folders and research context) · `data\` (the search index with text from your papers,
the Zotero database copy, your bibliography, your model choice, engine logs) ·
`engine\` and `models\` (large downloads, see above) · `.venv\`.

## Files
`app.py` (interface) · `rag.py` (search, answers, guides, page images, number check) · `indexer.py` (PDF
processing) · `figures.py` (tables and figures) · `models.py` (finding models, GPU memory estimates) ·
`library.py` (Zotero, Crossref, arXiv) · `citations.py` (citation styles, LaTeX/BibTeX) · `services.py` (AI engine) · `start.py` (startup) · `config.py` (defaults) ·
`local_settings.example.py` (template for your own settings) · `data\index.db` (the index; delete it to rebuild) ·
`data\bibliography.json` (your reference list) · `data\engine-*.log` (engine logs).

## Licence
The code in this repository is released under the [MIT licence](LICENSE).

It depends on other software with its own licences, which apply when you install or redistribute them:
[PyMuPDF and PyMuPDF4LLM](https://pymupdf.io/) are AGPL-3.0 (or an Artifex commercial licence), llama.cpp is MIT,
Streamlit is Apache-2.0, and each AI model comes with its own licence (see its download page). If you share a
packaged copy of the whole app, check what those licences ask of you.
