# Research Assistant

A private, offline "ChatPDF" for your research library. It reads every PDF in your Zotero library and in your
papers folder, answers questions with citations to the exact paper and page, explains what the papers mean, writes
study guides, finds and explains tables and figures, formats references in IEEE, Harvard, APA 7, BibTeX and
LaTeX (IEEE and Elsevier templates), and rewrites any text in your own voice.
The AI runs on your own computer - any open GGUF model, which the app suggests, downloads and sets up for you
(Qwen 3.5, Gemma 4, Mistral, GLM, gpt-oss and thousands more from Hugging Face). Nothing you read or ask leaves your
computer; the only internet use is downloading the engine and models, and metadata lookups on Crossref/arXiv for
PDFs that aren't in Zotero.

## Download and install (Windows 10/11)
1. **Download** the latest release: on GitHub, **Releases → Research Assistant vX → `ResearchAssistant.zip`** (or
   **Code → Download ZIP**).
2. **Extract** it to a folder of your choice that OneDrive doesn't sync - for example `C:\ResearchAssistant` (the AI
   models are several GB).
3. **Double-click `Start Research Assistant.bat`.** If Windows says "Windows protected your PC", click *More info →
   Run anyway* (it's a short script you can open in Notepad to read).
   - The first time, it installs Python and the app's libraries **inside that folder** (about 400 MB, 1-2 minutes;
     nothing is installed anywhere else) and puts a shortcut on your desktop.
   - Then the app opens in your browser with a **setup page**: it shows what your computer has (graphics card,
     memory), asks for your papers folder, and **suggests the AI model that suits your computer**. Press
     **Download and set up**: the AI engine (llama.cpp, the right build for your graphics card), the search model and
     the AI model download in the background (resumable, each file checked against its published checksum), then
     your papers are indexed and you can start asking.
4. **Next time:** the desktop shortcut. Everything stays in that folder; to uninstall, delete the folder and the
   shortcut.

**What it needs:** Windows 10 or 11 (64-bit), an internet connection for the first setup, and disk space for the
model you pick (1-20 GB; about 7 GB for the suggested model on an 8 GB graphics card). A graphics card is optional:
NVIDIA cards use the CUDA engine, AMD and Intel cards the Vulkan engine, and without one the AI runs on the processor
(slower; the app then suggests a small model). Zotero is optional.

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

## Getting more models: the 🧠 Models tab
- **This computer** - graphics card, memory, processor, and the AI engine build that suits them (cuda-13 or cuda-12
  for NVIDIA, vulkan for AMD/Intel, cpu without a graphics card). You can install or switch the engine here.
- **Suggested for this computer** - a curated list of open models (Qwen 3.5, Gemma 4, Ministral 3, GLM-4.6V,
  Qwen3-VL, gpt-oss, Phi-4, …), each with the **best version that runs fully on your computer** (Q6_K, Q5_K_M,
  Q4_K_M, … - smaller versions need less memory and are a little less accurate). ⭐ marks the app's suggestion;
  **Download**, then **Use it**. Toggle 👁 Vision off to see text-only models too.
- **Any model from Hugging Face** - search by name, or paste a link to a model page or file. The app lists the
  versions with a rough fit, and **🔍 Work out exactly how it runs** reads the model's description (its first ~10 MB,
  not the whole file) to tell you precisely which versions fit, how much text they can read at once, whether they can
  look at figures and whether they think before answering. Gated models (e.g. Llama) need you to accept their licence
  on Hugging Face and add an access token (🔑).
- **Downloads** run in the background while you keep working, pick up where they stopped if the connection drops,
  and are checked against Hugging Face's checksums. Models go into `models\<publisher>\<model>\`.

How the suggestion is made: each model's memory use is worked out from its layout (layers, attention cache, what
stays in normal memory, the vision part) for your graphics card's memory; the versions that run fully on the card
are kept; then models are ordered by a rough quality rank for reading papers (bigger and newer first, a small
bonus for models tested with this app), plus the version's quality. Without a graphics card, smaller models are
preferred because the processor is slower. Maintainers refresh the list with `tools\update_catalog.py`.

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

## ✍️ Rewrite in your voice
The **✍️ Rewrite** tab rewrites any text you paste - an AI draft, rough notes, a paragraph that doesn't sound like
you - the way *you* write, and checks texts for the usual signs of AI writing.

- **Your writing samples** (under the tab): paste pieces you wrote yourself, or add .txt / .docx / .pdf files (a file
  holding several pieces under headings such as "Essay 1: …" becomes one sample each). Aim for 1,500+ words in the
  kind of writing you'll rewrite. They stay in `data\writing_samples.json` on your computer.
- **How it learns your style:** it measures your habits (sentence length, how you open sentences, the phrases you
  reuse, punctuation, contractions, "I") and the local model writes a short description of your style once.
- **In my voice:** the text is first boiled down to plain notes (so none of its wording survives), then written up
  the way you write, using the samples most like it (your reports for a report, your essays for an essay). A second
  pass rewrites any sentence that still shows a sign of AI writing.
- **Only the AI-sounding parts:** rewrites just the flagged sentences and keeps the rest word for word.
- **Correct grammar and spelling** (on by default) copies your voice without your slips; off keeps your habits too.
- **Safety checks:** every number in the original must still be there (and nothing new added); if the model copies
  from your samples, reuses a sentence of yours, drops a paragraph, adds an opinion or a conclusion you didn't give,
  or describes the text instead of rewriting it, that part is written again (it is told what went wrong) - and as a
  last resort only its AI-sounding sentences are fixed. It can't check facts that aren't numbers, so read the result.
- It works best on the kinds of writing your samples cover; for a different kind (say an email when your samples are
  essays), add a sample or two of that kind first.
- **🔍 Check for AI signs** works without the AI: it underlines what reads as machine-written. Older signs: overused
  words like "delve" or "testament", "serves as" instead of "is", "not just X, but Y", tacked-on "…, highlighting …",
  inflated importance, "Despite its…, it faces challenges" endings, vague "experts say", dashes, bold and bullet
  points. Newer ones (2026 chatbots): "should be treated as… rather than…", "does not by itself establish",
  "while specific details are limited", "is associated with" instead of saying what the link is, a cluster of their
  new everyday words ("quietly", "dependable", "prioritize"…), and every sentence 8-20 words long with nothing but
  commas and full stops. Also chatbot leftovers: "I hope this helps", `[Your Name]` blanks, `oaicite` markers,
  `utm_source=chatgpt.com` links. Weaker signs only count when another sign is in the same paragraph, and
  **anything you use in your own samples is never flagged** - the signs are hints, not proof.
- **What is deliberately not flagged**, because the guide lists it as unreliable or as more typical of people:
  perfect grammar, formal wording, ordinary connectors ("Moreover", "Furthermore"), "in order to", "the fact that",
  "very", "perhaps".

The checks follow Wikipedia's guide [Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing)
(checked against its November 2026 revision; the signs that only exist on Wikipedia, such as edit summaries and
wikitext, are left out) and the [humanizer](https://github.com/blader/humanizer) skill (MIT), restated in this app's
own words.
A 9B model is good at structure, connectors and vocabulary but not perfect: read the result before you use it.

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

## Installing from the source code (developers)
`git clone https://github.com/Haider-Ontor/local-research-assistant.git`, then double-click
`Start Research Assistant.bat` as above - `tools\install.ps1` gets [uv](https://github.com/astral-sh/uv), which
installs Python 3.13 and `requirements.txt` into `.venv`. With your own Python 3.13 instead:
`py -3.13 -m venv .venv` and `.venv\Scripts\python -m pip install -r requirements.txt`. The app's setup page
downloads the rest. To set things up by hand instead: extract a llama.cpp Windows build (tested: b11899) into
`engine\`, put `nomic-embed-text-v1.5.Q4_K_M.gguf` and any GGUF chat model in `models\` (LM Studio's models are
found automatically), and copy `local_settings.example.py` to `local_settings.py`.

## What is not in this repository
Only the code is on GitHub. These stay on your computer (see `.gitignore`):
`local_settings.py` (your folders and research context) · `data\` (the search index with text from your papers,
the Zotero database copy, your bibliography, your model choice, your writing samples and style, a Hugging Face token
if you added one, engine logs) ·
`engine\` and `models\` (large downloads) · `.venv\`, `tools\uv\` and `tools\python\` (Python, installed on first
start).

## Files
`app.py` (interface) · `ui_setup.py` (setup page and 🧠 Models tab) · `writing.py` + `ui_writing.py` (✍️ Rewrite:
your style, AI-sign checks, rewriting) · `rag.py` (search, answers, guides, page images,
number check) · `indexer.py` (PDF processing) · `figures.py` (tables and figures) · `models.py` (finding models,
GPU memory estimates) · `hardware.py` (graphics card, memory, engine build) · `catalog.py` + `catalog.json`
(suggested models) · `hub.py` (Hugging Face) · `downloads.py` (background downloads, engine install) ·
`firstrun.py` (first start) · `library.py` (Zotero, Crossref, arXiv) · `citations.py` (citation styles,
LaTeX/BibTeX) · `services.py` (AI engine) · `start.py` (startup) · `config.py` (defaults) ·
`local_settings.example.py` (template for your own settings) · `tools\install.ps1` (installs Python and the
libraries) · `tools\update_catalog.py` (rebuilds catalog.json) · `data\index.db` (the index; delete it to rebuild) ·
`data\bibliography.json` (your reference list) · `data\engine-*.log` (engine logs).

## Licence
The code in this repository is released under the [MIT licence](LICENSE).

It depends on other software with its own licences, which apply when you install or redistribute them:
[PyMuPDF and PyMuPDF4LLM](https://pymupdf.io/) are AGPL-3.0 (or an Artifex commercial licence), llama.cpp is MIT,
Streamlit is Apache-2.0, and each AI model comes with its own licence (see its download page). If you share a
packaged copy of the whole app, check what those licences ask of you.
