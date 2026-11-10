"""Tables and figures in your papers.

Each PDF page is read with PyMuPDF's layout analysis (the same reading the index uses), which marks every region as text,
picture, table, caption, formula ... Each "Fig. N" / "Table N" caption is matched to the picture(s) or table next to it,
so a figure or table can be shown cropped from the page, downloaded (PNG / CSV), cited ("[5, Fig. 3]") and - because
Qwen 3.5 can look at images - explained by the AI, which also reads tables that are printed as images.
"""
import base64, json, re, sqlite3, time, unicodedata
import pymupdf
import config

FIG_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS figures(
  id INTEGER PRIMARY KEY, paper_id INTEGER, kind TEXT, num TEXT, label TEXT, page INTEGER, rect TEXT, cap_rect TEXT,
  caption TEXT, rows TEXT, rows_src TEXT, inner TEXT, explained TEXT, explained_at REAL);
CREATE INDEX IF NOT EXISTS figures_paper ON figures(paper_id);
CREATE TABLE IF NOT EXISTS figure_runs(paper_id INTEGER PRIMARY KEY, version INTEGER, sha1 TEXT, n INTEGER, at REAL);
"""

_WORD = r"(?i:(figures?|figs?|scheme|plate|chart|tables?|tab))\b\.?\s*"
_NUM = r"((?i:[IVXL]{1,6})|[A-Z]?\d{1,3}(?:\.\d{1,2})?)\b"
CAP_LOOSE = re.compile(r"^\W{0,3}" + _WORD + _NUM)                        # region already marked "caption"
CAP_STRICT = re.compile(r"^\W{0,3}" + _WORD + _NUM + r"\s*[a-z]?(?:\s*[.:|)–—-]|\s+[A-Z(\[]|\s*$)")
TEXTISH = {"text", "list-item", "section-header", "title", "caption", "footnote", "formula"}
_PICTEXT = re.compile(r"<!-- Start of picture text -->(.*?)<!-- End of picture text -->", re.S)
PAD = 3


# ---------------------------------------------------------------- finding them on the page
def _kind(word):
    return "table" if word.lower().startswith("tab") else "figure"


def _label(kind, num, word):
    w = word.lower()
    if kind == "table":
        return f"Table {num}"
    if w.startswith("scheme"):
        return f"Scheme {num}"
    if w.startswith("plate"):
        return f"Plate {num}"
    return f"Fig. {num}"


def _norm(t):
    t = unicodedata.normalize("NFKC", t or "").replace(chr(0xFFFD), " ")
    return re.sub(r"\s+", " ", t).strip()


def _xover(a, b):
    """Horizontal overlap of two rects, as a share of the narrower one."""
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    return max(0.0, w) / max(1.0, min(a.width, b.width))


def _clip_text(page, r):
    return _norm(page.get_text("text", clip=pymupdf.Rect(r) + (-1, -1, 1, 1)))


def _blocked(obstacles, lo, hi, span, skip=None):
    """Is a block of real text (or another caption) between heights lo and hi, inside the column `span`?"""
    for o in obstacles:
        if o is skip:
            continue
        r = o["r"]
        if r.y0 >= lo - 2 and r.y1 <= hi + 2 and _xover(r, span) > 0.3 and (o["cap"] or len(o["txt"]) > 60):
            return True
    return False


def _gap(r, cap, below):
    return (r.y0 - cap.y1) if below else (cap.y0 - r.y1)


def _clean_cell(c):
    c = re.sub(r"<br\s*/?>", " ", c, flags=re.I)
    c = re.sub(r"<sup>(.*?)</sup>", r"^\1", c, flags=re.I | re.S)
    c = re.sub(r"<sub>(.*?)</sub>", r"\1", c, flags=re.I | re.S)
    c = re.sub(r"</?[a-zA-Z][^>]*>", "", c)
    c = re.sub(r"(?<![\w])[_*]+|[_*]+(?![\w])", "", c)
    return _norm(c)


def md_table_rows(md):
    """Cells of a Markdown table (pymupdf4llm and the AI both write tables as | a | b |)."""
    rows = []
    for line in (md or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.fullmatch(r"\|?(\s*:?-{2,}:?\s*\|)+\s*:?-*:?\s*\|?", line):
            continue
        rows.append([_clean_cell(c) for c in line.strip("|").split("|")])
    return _tidy(rows)


def _tidy(rows):
    rows = [r for r in rows if any(r)]
    if not rows:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [j for j in range(width) if any(r[j] for r in rows)]
    rows = [[r[j] for j in keep] for r in rows]
    return rows if len(rows) >= 2 and len(keep) >= 2 else None


def grid_rows(page, rect):
    """Cells of a table PyMuPDF finds inside `rect` (for tables the layout marked as a picture). Each cell's text is
    read on its own, which keeps decimal points and spaces where they belong. None if the table is only an image."""
    clip = pymupdf.Rect(rect) + (-2, -2, 2, 2)
    for strategy in ("lines", "text"):
        try:
            tabs = page.find_tables(clip=clip, strategy=strategy)
        except Exception:
            continue
        best = max(tabs.tables, key=lambda t: t.row_count * t.col_count, default=None)
        if best and best.row_count >= 2 and best.col_count >= 2:
            rows = _tidy([[_clip_text(page, c) if c else "" for c in row.cells] for row in best.rows])
            if rows:
                return rows
    return None


def page_items(page, pg):
    """Captioned tables and figures on one page: [{kind, num, label, rect, cap_rect, caption, rows, inner}]."""
    text = pg.get("text") or ""
    area = page.rect.width * page.rect.height
    pics, tables, flow = [], [], []
    for b in pg.get("page_boxes") or []:
        cls, r = b.get("class"), pymupdf.Rect(b["bbox"])
        a, z = b.get("pos") or (0, 0)
        md = text[a:z]
        if r.is_empty:
            continue
        if cls == "picture":
            if r.width * r.height > 0.004 * area and r.width > 30 and r.height > 30:
                inner = " ; ".join(re.sub(r"<br\s*/?>", " ; ", m, flags=re.I).strip() for m in _PICTEXT.findall(md))
                pics.append({"r": r, "inner": _norm(inner)[:1500], "used": False})
        elif cls == "table":
            tables.append({"r": r, "md": md, "used": False, "table": True})
        elif cls in TEXTISH:
            flow.append({"r": r, "cls": cls, "txt": _clip_text(page, r), "cap": False, "used": False})

    caps = []
    for f in flow:
        if f["used"]:
            continue
        m = (CAP_LOOSE if f["cls"] == "caption" else CAP_STRICT).match(f["txt"])
        if not m or len(f["txt"]) > 2500:
            continue
        f["cap"] = True
        kind, num = _kind(m.group(1)), m.group(2).upper()
        cap = {"kind": kind, "num": num, "label": _label(kind, num, m.group(1)), "r": pymupdf.Rect(f["r"]),
               "txt": f["txt"], "flow": f}
        if len(f["txt"]) - m.end() < 4:              # IEEE style: "TABLE II" alone, its title on the next line
            nxt = min((g for g in flow if g is not f and not g["used"] and 0 <= g["r"].y0 - f["r"].y1 < 14
                       and _xover(g["r"], f["r"]) > 0.5 and len(g["txt"]) < 400
                       and not CAP_STRICT.match(g["txt"])), key=lambda g: g["r"].y0, default=None)
            if nxt:
                nxt["used"] = True
                cap["r"] |= nxt["r"]
                cap["txt"] += " " + nxt["txt"]
        caps.append(cap)
    obstacles = [f for f in flow if not f["used"]]

    def nearest(cands, cap, below):
        ok = []
        for c in cands:
            if c["used"] or _xover(c["r"], cap["r"]) < 0.3 or _gap(c["r"], cap["r"], below) < -8:
                continue
            lo, hi = (cap["r"].y1, c["r"].y0) if below else (c["r"].y1, cap["r"].y0)
            if hi - lo > 2 and _blocked(obstacles, lo, hi, cap["r"], skip=cap["flow"]):
                continue
            ok.append(c)
        return min(ok, key=lambda c: (_gap(c["r"], cap["r"], below), not c.get("table")), default=None)

    def finish(rect, cap, below):
        """Pad the crop a little, but never into the caption or off the page."""
        r = (pymupdf.Rect(rect) + (-PAD, -PAD, PAD, PAD)) & page.rect
        if below:
            r.y0 = max(r.y0, cap["r"].y1 + 0.5)
        else:
            r.y1 = min(r.y1, cap["r"].y0 - 0.5)
        return [round(v, 1) for v in r]

    out = []
    # tables first: a table printed as a picture must not be taken for part of a figure
    for cap in [c for c in caps if c["kind"] == "table"]:
        item = {"kind": "table", "num": cap["num"], "label": cap["label"], "rect": None,
                "cap_rect": [round(v, 1) for v in cap["r"]], "caption": cap["txt"], "rows": None, "inner": ""}
        for below in (True, False):
            hit = nearest(tables + pics, cap, below)
            if hit:
                hit["used"] = True
                item["rect"] = finish(hit["r"], cap, below)
                item["rows"] = (md_table_rows(hit["md"]) if hit.get("table") else None) or grid_rows(page, hit["r"])
                break
        out.append(item)
    for cap in [c for c in caps if c["kind"] == "figure"]:
        item = {"kind": "figure", "num": cap["num"], "label": cap["label"], "rect": None,
                "cap_rect": [round(v, 1) for v in cap["r"]], "caption": cap["txt"], "rows": None, "inner": ""}
        for below in (False, True):                       # captions are usually under the figure
            seed = nearest(pics, cap, below)
            if not seed:
                continue
            group, members = pymupdf.Rect(seed["r"]), [seed]
            seed["used"] = True
            grew = True
            while grew:                                   # multi-panel figures: add neighbouring pictures
                grew = False
                span = pymupdf.Rect(min(group.x0, cap["r"].x0), 0, max(group.x1, cap["r"].x1), 1)
                for p in pics:
                    r = p["r"]
                    if p["used"] or _xover(r, span) < 0.5:
                        continue
                    if not below and not (r.y1 <= cap["r"].y0 + 8 and r.y1 >= group.y0 - 45):
                        continue
                    if below and not (r.y0 >= cap["r"].y1 - 8 and r.y0 <= group.y1 + 45):
                        continue
                    lo, hi = (r.y1, group.y0) if r.y1 <= group.y0 else (group.y1, r.y0)
                    if hi - lo > 2 and _blocked(obstacles, lo, hi, span):
                        continue
                    p["used"] = True
                    group |= r
                    members.append(p)
                    grew = True
            # short labels just outside the pictures - "(a)", "(b)", axis titles - belong to the figure
            span = pymupdf.Rect(min(group.x0, cap["r"].x0) - 5, 0, max(group.x1, cap["r"].x1) + 5, 1)
            for f in flow:
                r = f["r"]
                if f["cap"] or f["used"] or len(f["txt"]) > 40 or r.x0 < span.x0 or r.x1 > span.x1:
                    continue
                inside = (r.y0 >= group.y0 - 20 and r.y1 <= cap["r"].y0 + 1 and r.y0 <= group.y1 + 20) if not below else \
                         (r.y0 >= cap["r"].y1 - 1 and r.y1 <= group.y1 + 20 and r.y1 >= group.y0 - 20)
                if inside:
                    group |= r
            item["rect"] = finish(group, cap, below)
            item["inner"] = " ; ".join(p["inner"] for p in members if p["inner"])[:1500]
            break
        out.append(item)
    return sorted(out, key=lambda it: (it["rect"] or it["cap_rect"])[1])


def extract(path):
    """All captioned tables and figures in a PDF, in page order."""
    import pymupdf4llm
    doc = pymupdf.open(path)
    try:
        pages = pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False)
        out = []
        for i, pg in enumerate(pages):
            pno = int((pg.get("metadata") or {}).get("page_number") or i + 1)
            for it in page_items(doc[pno - 1], pg):
                it["page"] = pno
                out.append(it)
        seen = {}
        for it in out:                                   # "Table 2" again = continued on the next page
            seen[it["label"]] = seen.get(it["label"], 0) + 1
            if seen[it["label"]] > 1:
                it["label"] += " (cont.)"
        return out
    finally:
        doc.close()


# ---------------------------------------------------------------- storing them
def connect_tables(con):
    con.executescript(SCHEMA)


def sync(con, log=print, progress=None, force=False):
    """Find tables and figures in every paper that hasn't been looked at yet (or has changed)."""
    connect_tables(con)
    rows = con.execute("SELECT id, path, sha1, meta FROM papers WHERE dup_of IS NULL AND status = 'ok' "
                       "AND source != 'proposal'").fetchall()
    live = {r[0] for r in rows}
    done = {pid: (v, s) for pid, v, s in con.execute("SELECT paper_id, version, sha1 FROM figure_runs")}
    for pid in [p for p in done if p not in live]:
        con.execute("DELETE FROM figures WHERE paper_id=?", (pid,))
        con.execute("DELETE FROM figure_runs WHERE paper_id=?", (pid,))
    con.commit()
    todo = [r for r in rows if force or done.get(r[0]) != (FIG_VERSION, r[2])]
    if not todo:
        return 0
    log(f"Finding tables and figures in {len(todo)} papers (a few seconds each)…")
    for i, (pid, path, sha, meta) in enumerate(todo, 1):
        title = json.loads(meta or "{}").get("title", "")
        if progress:
            progress(i, len(todo), title[:60])
        try:
            items = extract(path)
        except Exception as e:
            log(f"[{i}/{len(todo)}] couldn't read figures of {title[:60]}: {e}")
            items = []
        same_file = done.get(pid, (None, None))[1] == sha
        old = {r[0]: r[1:] for r in con.execute(
            "SELECT label, explained, explained_at, rows, rows_src FROM figures WHERE paper_id=?", (pid,))} if same_file else {}
        con.execute("DELETE FROM figures WHERE paper_id=?", (pid,))
        for it in items:
            prev = old.get(it["label"])
            rows_, src = (it["rows"], "pdf") if it["rows"] else ((json.loads(prev[2]), "ai") if prev and prev[3] == "ai" and prev[2] else (None, None))
            con.execute("INSERT INTO figures(paper_id, kind, num, label, page, rect, cap_rect, caption, rows, rows_src, inner, "
                        "explained, explained_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, it["kind"], it["num"], it["label"], it["page"], json.dumps(it["rect"]), json.dumps(it["cap_rect"]),
                         it["caption"], json.dumps(rows_) if rows_ else None, src, it["inner"],
                         prev[0] if prev else None, prev[1] if prev else None))
        con.execute("INSERT OR REPLACE INTO figure_runs(paper_id, version, sha1, n, at) VALUES(?,?,?,?,?)",
                    (pid, FIG_VERSION, sha, len(items), time.time()))
        con.commit()
        nf = sum(it["kind"] == "figure" for it in items)
        log(f"[{i}/{len(todo)}] {nf} figures, {len(items) - nf} tables · {title[:70]}")
    return len(todo)


_COLS = ["id", "paper_id", "kind", "num", "label", "page", "rect", "cap_rect", "caption", "rows", "rows_src", "inner",
         "explained", "explained_at"]


def _row(r):
    f = dict(zip(_COLS, r))
    for k in ("rect", "cap_rect", "rows"):
        f[k] = json.loads(f[k]) if f[k] else None
    return f


def load_all(db_path=config.DB_PATH):
    """{paper_id: [figure, ...]} sorted figures first, then tables, by number."""
    con = sqlite3.connect(db_path)
    try:
        connect_tables(con)
        rows = con.execute(f"SELECT {', '.join(_COLS)} FROM figures").fetchall()
    finally:
        con.close()
    out = {}
    for r in rows:
        f = _row(r)
        out.setdefault(f["paper_id"], []).append(f)
    for items in out.values():
        items.sort(key=sort_key)
    return out


def stamp(db_path=config.DB_PATH):
    con = sqlite3.connect(db_path)
    try:
        connect_tables(con)
        return con.execute("SELECT count(*), coalesce(max(id),0), coalesce(sum(explained_at),0), "
                           "coalesce(sum(length(rows)),0) FROM figures").fetchone()
    finally:
        con.close()


def get(fid, db_path=config.DB_PATH):
    con = sqlite3.connect(db_path)
    try:
        r = con.execute(f"SELECT {', '.join(_COLS)} FROM figures WHERE id=?", (fid,)).fetchone()
    finally:
        con.close()
    return _row(r) if r else None


def save(fid, db_path=config.DB_PATH, **fields):
    con = sqlite3.connect(db_path)
    try:
        for k, v in fields.items():
            if k not in ("explained", "explained_at", "rows", "rows_src"):
                raise ValueError(k)
            con.execute(f"UPDATE figures SET {k}=? WHERE id=?", (json.dumps(v) if k == "rows" and v is not None else v, fid))
        con.commit()
    finally:
        con.close()


_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50}


def num_value(num):
    s = (num or "").upper()
    if s and all(c in _ROMAN for c in s):
        total = 0
        for i, c in enumerate(s):
            v = _ROMAN[c]
            total += -v if i + 1 < len(s) and _ROMAN[s[i + 1]] > v else v
        return total
    m = re.match(r"([A-Z]?)(\d+)(?:\.(\d+))?", s)
    if not m:
        return 9999
    return (1000 if m.group(1) else 0) + int(m.group(2)) + int(m.group(3) or 0) / 100


def sort_key(f):
    return (f["kind"] != "figure", num_value(f["num"]), "(cont.)" in f["label"], f["page"])


# ---------------------------------------------------------------- using them
_MENTION = re.compile(r"\b(?i:(fig(?:ure)?s?|tab(?:le)?s?))\.?\s*((?:[IVXL]{1,6}|[A-Z]?\d{1,3})"
                      r"(?:\s*(?:,|and|&|[–-])\s*(?:[IVXL]{1,6}|[A-Z]?\d{1,3}))*)\b")


def mentions(text):
    """{('figure', '3'), ('table', 'II'), ...} referred to in a passage ("Fig. 3", "Figs. 4-6", "Table II")."""
    out = set()
    for m in _MENTION.finditer(text or ""):
        kind = "table" if m.group(1).lower().startswith("tab") else "figure"
        parts = re.findall(r"[IVXL]{1,6}|[A-Z]?\d{1,3}|[–-]", m.group(2))
        prev = None
        for i, tok in enumerate(parts):
            if tok in "-–":
                if prev and prev.isdigit() and i + 1 < len(parts) and parts[i + 1].isdigit():
                    for n in range(int(prev) + 1, min(int(parts[i + 1]), int(prev) + 12)):
                        out.add((kind, str(n)))
                continue
            out.add((kind, tok))
            prev = tok
    return out


def find_mentioned(items, text):
    want = mentions(text)
    return [f for f in items if (f["kind"], f["num"]) in want and "(cont.)" not in f["label"]]


def search(all_items, q, allowed=None, k=24):
    """Figures and tables whose caption (or AI explanation) matches the words of `q`."""
    from rag import STOP
    terms = [t for t in re.findall(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]", (q or "").lower()) if t not in STOP]
    if not terms:
        return []
    phrase = " ".join(terms)
    scored = []
    for pid, items in all_items.items():
        if allowed is not None and pid not in allowed:
            continue
        for f in items:
            cap = (f["label"] + " " + (f["caption"] or "") + " " + (f["inner"] or "")).lower()
            exp = (f["explained"] or "").lower()
            words = set(re.findall(r"[a-z0-9]+(?:[\-\.][a-z0-9]+)*", cap))
            ewords = set(re.findall(r"[a-z0-9]+(?:[\-\.][a-z0-9]+)*", exp))
            s = 0.0
            for t in terms:
                if t in words or any(w.startswith(t) for w in words if len(t) > 3):
                    s += 2
                elif t in ewords or any(w.startswith(t) for w in ewords if len(t) > 3):
                    s += 1
            if len(terms) > 1 and phrase in cap:
                s += 3
            if s >= max(2, len(terms)):
                scored.append((s, f))
    scored.sort(key=lambda x: -x[0])
    return [f for _, f in scored[:k]]


def render(path, page, rect, dpi=170):
    """PNG bytes of one region of a page."""
    doc = pymupdf.open(path)
    try:
        pg = doc[page - 1]
        clip = pymupdf.Rect(rect) & pg.rect
        return pg.get_pixmap(clip=clip, dpi=dpi).tobytes("png")
    finally:
        doc.close()


def vision_dpi(rect, target_px=1100):
    r = pymupdf.Rect(rect)
    return int(max(100, min(300, 72 * target_px / max(r.width, r.height, 1))))


def csv_text(rows):
    import csv, io
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue()


def md_table(rows):
    head, body = rows[0], rows[1:]
    esc = lambda c: (c or "").replace("|", "/")
    return "\n".join(["| " + " | ".join(esc(c) for c in head) + " |", "|" + "---|" * len(head)] +
                     ["| " + " | ".join(esc(c) for c in r) + " |" for r in body])


def cite_lines(p, f, style, n=None):
    """(in-text citation pointing at the figure, credit line for reusing it in your own document)."""
    import citations
    lab = f["label"].replace(" (cont.)", "")
    if style == "APA 7":
        lab = lab.replace("Fig.", "Figure")
    if style == "IEEE":
        num = n or "#"
        return f"[{num}, {lab}]", f"Reproduced from [{num}]."
    if style == "BibTeX" or citations.is_latex(style):
        key = citations.bibtex_key(p)
        if citations.LATEX.get(style) == "els-harv":         # author-year: (Smith et al., 2019, Fig. 3)
            return f"\\citep[{lab.replace(' ', '~')}]{{{key}}}", f"Reproduced from \\citet{{{key}}}."
        return f"\\cite[{lab.replace(' ', '~')}]{{{key}}}", f"Reproduced from \\cite{{{key}}}."
    base = citations.in_text(p, style)
    m = re.match(r"\((.*), ([^,]+)\)$", base)
    who, year = (m.group(1), m.group(2)) if m else (base.strip("()"), "")
    if style == "Harvard":
        return f"({who}, {year}, {lab})", f"Source: {who} ({year}, {lab})."
    return f"({who}, {year}, {lab})", "Note. Adapted from " + citations.reference(p, style)


def vision_up():
    """True when the running answer model can look at images (started with its vision file)."""
    import requests
    try:
        props = requests.get(f"http://127.0.0.1:{config.CHAT_PORT}/props", timeout=2).json()
        return bool((props.get("modalities") or {}).get("vision"))
    except Exception:
        return False


# ---------------------------------------------------------------- the AI looks at a figure
FIG_PROMPT = """You are a patient research tutor. You are shown ONE {what} from a research paper as an image, with its caption and the passages of the paper that discuss it. Help an engineering researcher properly UNDERSTAND it.

The researcher's work: {RESEARCH}

Write under these four headings:
### What it shows
{shows}
### Main result
What it shows: trends, comparisons, peaks, shifts, the best and worst cases. Give the key values.
### What this means
Plain language for someone new to this exact topic: define every term, symbol and unit you use; explain the physics or intuition behind the result.
### Why it matters
Why the authors show it and its limits and caveats. Then - only where it genuinely fits - how it could help the researcher's work described above, naming the one proposal objective it fits best. If it doesn't really relate to their work, say in one sentence that it is background and stop: never force a link.

Rules:
- Describe only what is in the image, the caption and the passages. Never invent values, labels or results. If something is too small to read, say so.
- A value you read off the image by eye starts with "≈" (e.g. ≈575 MHz) and carries NO page label. A value written in the caption, the table cells or the passages carries its page label, e.g. (p. 8).
- Never work out new numbers yourself: no unit conversions, no dB-to-ratio or percentage calculations, no differences or averages.
- Explanations from your own general knowledge carry no page label.
- Link it to the researcher's work only when the figure is about the same topic as one of their projects. Never draw analogies between unrelated topics (e.g. load forecasting is not "like" sensor power management).
- Use LaTeX ($...$) for symbols."""

SHOWS = {
    "figure": "The kind of figure (graph, schematic, photo, map, flowchart...). For a graph: each axis with its quantity, unit and range, and what each line, marker, colour or panel represents. For a schematic or photo: the parts and how they connect.",
    "table": "What the table compares: what the rows and the columns are, with their units.",
}

READ_PROMPT = ("Transcribe this table exactly as a Markdown table. Keep every value, unit, sign and symbol exactly as "
               "printed (write subscripts and Greek letters as plain text, e.g. eps_r, tan d, S11). Leave empty cells "
               "empty. Use the first printed row(s) as the header row. Output only the table.")


def context_passages(p, f, chunks, max_chars=3600):
    """Passages of the paper that mention this figure/table (and the caption's own page), with page labels."""
    from rag import page_label
    word = r"(?i:tab(?:le)?s?)" if f["kind"] == "table" else r"(?i:fig(?:ure)?s?)"
    pat = re.compile(r"\b" + word + r"\.?\s*" + re.escape(f["num"]) + r"\b")
    out, used = [], 0
    for page, text in chunks:
        m = pat.search(text or "")
        if not m:
            continue
        a = max(0, m.start() - 500)
        piece = text[a:a + 1200].strip()
        out.append(f"({page_label(p, page)}) " + ("… " if a else "") + piece)
        used += len(piece)
        if used > max_chars:
            break
    return out


def explain_messages(p, f, chunks, png, with_proposal=True):
    """with_proposal=False leaves the proposal brief out (for papers on other topics), so the small model doesn't force
    a link to the proposal's objectives."""
    import rag
    system = FIG_PROMPT.replace("{what}", "table" if f["kind"] == "table" else "figure") \
                       .replace("{shows}", SHOWS[f["kind"]]) \
                       .replace("{RESEARCH}", rag.research_context(with_proposal)) + rag.reference_notes()
    from rag import page_label
    parts = [f"Paper: {p.get('title', '')} ({rag.short_authors(p)}, {p.get('year', '')})",
             f"{f['label']} on {page_label(p, f['page'])}. Caption: {f['caption'] or '(none)'}"]
    if f.get("rows"):
        parts.append(f"The table's cells as text ({page_label(p, f['page'])}"
                     + (", read by the AI from the image" if f.get("rows_src") == "ai" else "") + "):\n" + md_table(f["rows"]))
    if f.get("inner"):
        parts.append("Text printed inside the figure (from the PDF, may be jumbled): " + f["inner"][:1200])
    ctx = context_passages(p, f, chunks)
    if ctx:
        parts.append("Passages of the paper that discuss it:\n" + "\n\n".join(ctx))
    parts.append(f"Explain this {f['kind']}. Start every value you read off the image by eye with ≈ (e.g. ≈4.5 GHz). "
                 "Put the page label, e.g. (p. 8), after every value taken from the caption, the table cells or the passages. "
                 "Don't calculate anything. In 'Why it matters', if this is not about the same topic as one of the "
                 "researcher's projects, say in one sentence which of their interests it is background for, and stop."
                 + ("" if with_proposal else " This paper is NOT on the topic of the researcher's PhD proposal or their "
                    "sensor work: don't mention the proposal, its objectives or sensors - relate it only to the one "
                    "interest listed above that it matches, in one sentence."))
    return [{"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": "\n\n".join(parts)},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                                                                             base64.b64encode(png).decode()}}]}]


ASK_PROMPT = """You are a patient research tutor. You are shown ONE {what} from a research paper as an image, with its caption and the passages of the paper that discuss it. Answer the researcher's question about it clearly and concisely.

The researcher's work: {RESEARCH}

Rules:
- Use only what is in the image, the caption and the passages, plus general knowledge for explanations. Never invent values, labels or results. If something is too small to read, or the figure doesn't show it, say so.
- A value you read off the image by eye starts with "≈" (e.g. ≈575 MHz) and carries NO page label. A value written in the caption, the table cells or the passages carries its page label, e.g. (p. 8).
- Use LaTeX ($...$) for symbols."""


def ask_messages(p, f, chunks, png, question, with_proposal=True):
    import rag
    msgs = explain_messages(p, f, chunks, png, with_proposal)
    msgs[0]["content"] = ASK_PROMPT.replace("{what}", "table" if f["kind"] == "table" else "figure") \
                                   .replace("{RESEARCH}", rag.research_context(with_proposal)) + rag.reference_notes()
    text = msgs[1]["content"][0]["text"]
    msgs[1]["content"][0]["text"] = text.rsplit("\n\nExplain this", 1)[0] + f"\n\nQuestion: {question}"
    return msgs


def read_messages(f, png):
    return [{"role": "user", "content": [{"type": "text", "text": f"Caption: {f['caption']}\n\n{READ_PROMPT}"},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                                                                             base64.b64encode(png).decode()}}]}]


_EYE = re.compile(r"(?:≈|~|∼|\\approx|\\sim|approx(?:imately|\.)?|about|around|roughly|nearly|close to)\s*\$?\s*"
                  r"(?:\\?[-−+]|\\pm)?\s*$", re.I)


def check_explanation(text, f, chunks_all):
    """Every number in an explanation is looked for in the whole paper (text, caption, table cells, text inside the
    figure). Returns ([(start, end, tag)], checked, n_eye): tag 'eye' = not in the paper and marked as read off the
    image by eye (≈ / approximately ...), 'none' = not in the paper and not marked - check it on the figure."""
    import rag
    srcs = [t for _, t in chunks_all] + [f["caption"] or "", md_table(f["rows"]) if f.get("rows") else "",
                                          f.get("inner") or ""]
    have = set().union(*(rag.numbers_in(s) for s in srcs))
    blob = " ".join(("%.6f" % v).rstrip("0").rstrip(".") for v in have) or "-"
    bad, checked = rag.check_numbers(text, lambda seg: [blob])
    out, n_eye = [], 0
    for a, b, _ in bad:
        eye = bool(_EYE.search(text[max(0, a - 16):a]))
        n_eye += eye
        out.append((a, b, "eye" if eye else "none"))
    return out, checked, n_eye
