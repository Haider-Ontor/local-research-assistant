"""Your PhD proposal, built into the assistant.

The newest .docx/.pdf with "proposal" in its name in PROPOSAL_DIR is copied (your file is never touched), turned
into a PDF with real pages (via Microsoft Word, or PyMuPDF if Word isn't available), indexed like a paper (labelled
as your own draft), and summarised into a short brief that the AI keeps in mind for every answer and study guide.
"""
import json, re, shutil, subprocess, time
from pathlib import Path
import config

DIR = config.DATA_DIR / "proposal"
PDF = DIR / "proposal.pdf"
BRIEF = DIR / "brief.json"
NO_WINDOW = 0x08000000


def find_file():
    """The proposal file to use: PROPOSAL_FILE if set, else the newest matching file in PROPOSAL_DIR."""
    if config.PROPOSAL_FILE:
        p = Path(config.PROPOSAL_FILE)
        return p if p.exists() else None
    if not config.PROPOSAL_DIR:
        return None
    d = Path(config.PROPOSAL_DIR)
    if not d.exists():
        return None
    cands = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in (".docx", ".pdf")
             and "proposal" in p.name.lower() and not p.name.startswith("~$")]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _kill_new_word(since):
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Get-Process WINWORD -ErrorAction SilentlyContinue | ? {{ $_.StartTime -gt [datetime]::FromFileTime({since}) }}"
                    " | Stop-Process -Force"], capture_output=True, timeout=30, creationflags=NO_WINDOW)


def to_pdf(src, log=print):
    """Make data/proposal/proposal.pdf from the draft (a copy of it - the original is never opened for writing)."""
    DIR.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".pdf":
        shutil.copy2(src, PDF)
        return PDF
    tmp = DIR / "draft_copy.docx"
    shutil.copy2(src, tmp)
    before = PDF.stat().st_mtime if PDF.exists() else 0
    started = int((time.time() + 11644473600) * 1e7)          # now, as a Windows file time
    ps = ("$w = New-Object -ComObject Word.Application; $w.Visible = $false; $w.DisplayAlerts = 0; "
          "$w.AutomationSecurity = 3; try { "
          f"$d = $w.Documents.Open('{tmp}', $false, $true, $false, '', '', $true, '', '', 0, 0, $false, $false, 0, $true); "
          f"$d.ExportAsFixedFormat('{PDF}', 17); $d.Close(0) }} finally {{ $w.Quit() }}")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=90, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        _kill_new_word(started)
    if PDF.exists() and PDF.stat().st_mtime > before:
        return PDF
    log("Word couldn't convert the draft, using the built-in reader instead (pages may differ slightly).")
    import pymupdf
    with pymupdf.open(tmp) as d:
        PDF.write_bytes(d.convert_to_pdf())
    return PDF


# ---------------------------------------------------------------- the brief the AI keeps in mind
BRIEF_PROMPT = """Below is the researcher's own PhD proposal draft. Write a compact brief of it (at most 450 words) that an AI research assistant will keep in mind while helping the researcher read and understand papers.

Use these labelled lines, in this order (skip a line if the draft has nothing for it):
Title:
Aim:
Objectives: (each objective with its label, e.g. O1, O2 ...)
Approach / system concept:
Methods and work packages: (each with its label and months)
Validation plan:
Key evidence the draft relies on: (the main numbers, each followed by its source as "(first author surname, year)" taken from the draft's reference list - never as a bracketed number)
Research gap:
Main risks and mitigations:
Timeline:
Background: (the researcher's prior experience, if the draft mentions it)

Rules: use only what the draft says; keep its terms, numbers and units exactly; no opinions, suggestions or praise."""


def make_brief(think=True):
    """Ask the local model for the brief, check its numbers against the draft, and save it."""
    import pymupdf, indexer, rag
    with pymupdf.open(PDF) as doc:
        segs = indexer.md_segments(doc)
    body = "\n\n".join(b for _, ref, _, b in segs if not ref)
    refs = "\n".join(b for _, ref, _, b in segs if ref)
    msgs = [{"role": "system", "content": BRIEF_PROMPT},
            {"role": "user", "content": f"PROPOSAL DRAFT:\n\n{body}\n\nREFERENCE LIST OF THE DRAFT:\n{refs}"}]
    text = "".join(rag.stream_chat(msgs, think, max_tokens=1100)).strip()
    text = re.sub(r"\s*\[\d+(?:\s*[,–-]\s*\d+)*\]", "", text)          # bracket numbers would clash with excerpt numbers
    bad, checked = rag.check_numbers(text, lambda seg: [body + "\n" + refs])
    data = {"text": text, "source": str(find_file() or ""), "created": time.time(),
            "checked": checked, "not_found": [text[a:b] for a, b, *_ in bad]}
    DIR.mkdir(parents=True, exist_ok=True)
    BRIEF.write_text(json.dumps(data, indent=1, ensure_ascii=False), "utf-8")
    return data


def reference_coverage(papers, chunks_text):
    """Which references in your draft are already in your library?
    papers: {pid: paper}, chunks_text: the draft's reference-list text. Returns [(ref_no, text, pid or None)]."""
    import library
    lib_doi = {p.get("doi", "").lower(): pid for pid, p in papers.items() if p.get("doi") and p.get("source") != "proposal"}
    lib_titles = [(pid, p.get("title", "")) for pid, p in papers.items() if p.get("source") != "proposal"]
    out = []
    for m in re.finditer(r"\[(\d+)\]\s*(.+?)(?=\s*-?\s*\[\d+\]\s|\Z)", chunks_text, re.S):
        n, ref = int(m.group(1)), " ".join(m.group(2).split())
        pid = None
        dm = library.DOI_RE.search(ref)
        if dm:
            pid = lib_doi.get(library.clean_doi(dm.group(1)))
        if pid is None:
            tm = re.search(r"[\"“](.+?)[,.]?[\"”]", ref)
            if tm:
                best = max(lib_titles, key=lambda x: library.similar(x[1], tm.group(1)), default=(None, ""))
                if best[0] is not None and library.similar(best[1], tm.group(1)) >= 0.85:
                    pid = best[0]
        if pid is None:                                   # patents/standards: match the number to a file name
            for num in re.findall(r"\d[\d ]{6,}\d", ref):
                digits = re.sub(r"\D", "", num)
                pid = next((i for i, p in papers.items() if p.get("source") != "proposal"
                            and digits in Path(p.get("path", "")).name), None)
                if pid:
                    break
        out.append((n, ref, pid))
    return out


def coverage_for_index(idx):
    """reference_coverage() for the proposal currently in the index (empty list if there is none)."""
    import sqlite3
    pid = next((i for i, p in idx.papers.items() if p.get("source") == "proposal"), None)
    if pid is None:
        return []
    con = sqlite3.connect(config.DB_PATH)
    text = "\n".join(r[0] for r in con.execute("SELECT text FROM chunks WHERE paper_id=? AND is_ref=1 ORDER BY id", (pid,)))
    con.close()
    return reference_coverage(idx.papers, text)


def load_brief():
    try:
        return json.loads(BRIEF.read_text("utf-8"))
    except Exception:
        return None


def brief_text():
    b = load_brief()
    return b["text"] if b and b.get("text") else ""


def brief_is_stale():
    """True when the indexed proposal is newer than the brief (or there is no brief yet)."""
    b = load_brief()
    return PDF.exists() and (not b or b.get("created", 0) < PDF.stat().st_mtime)
