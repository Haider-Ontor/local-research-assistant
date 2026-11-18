"""One readable Markdown file per paper (data/papers_md/), written from the index, plus INDEX.md listing them all.

Each file holds the paper's details, its full text page by page ([Page 4] markers use the page numbers printed on the
paper), its tables and figures (captions, and table cells where they were read) and its reference list. Any AI tool -
or you - can read them directly. They are rewritten only when a paper changes, and removed when it leaves the library.
"""
import json, re, sqlite3, time
from pathlib import Path
import config

OUT = config.DATA_DIR / "papers_md"


def _name(pid, p):
    first = ((p.get("authors") or [{}])[0].get("last") or "Unknown").strip()
    title = re.sub(r"[^\w\s-]", "", p.get("title") or Path(p["path"]).stem)[:70].strip()
    return re.sub(r"\s+", " ", f"{pid:04d} {first} {p.get('year') or 'n.d.'} - {title}") + ".md"


def _authors(p):
    return "; ".join(" ".join(x for x in (a.get("first"), a.get("last")) if x) for a in p.get("authors") or []) or "?"


def _page(p, page):
    import rag
    try:
        return rag.page_label(p, page)
    except Exception:
        return str(page)


def paper_markdown(con, pid, p):
    import rag
    lines = [f"# {p.get('title') or Path(p['path']).name}", ""]
    for label, val in (("Authors", _authors(p)), ("Year", p.get("year")), ("Journal", p.get("venue")),
                       ("Volume/issue/pages", " / ".join(str(x) for x in (p.get("volume"), p.get("issue"),
                                                                          p.get("pages")) if x)),
                       ("DOI", p.get("doi")), ("PDF", p["path"]),
                       ("Collections", ", ".join(p.get("collections") or []))):
        if val:
            lines.append(f"- **{label}:** {val}")
    if p.get("abstract"):
        lines += ["", "## Abstract", "", p["abstract"].strip()]
    body, refs, cur, text = [], [], None, ""
    for page, is_ref, t in con.execute("SELECT page, is_ref, text FROM chunks WHERE paper_id=? ORDER BY id", (pid,)):
        if is_ref:
            refs.append(t)
            continue
        if page != cur:
            if text:
                body.append((cur, text))
            cur, text = page, t
        else:
            text = rag._stitch(text, t)          # passages overlap a little: join them without repeats
    if text:
        body.append((cur, text))
    lines += ["", "## Full text", ""]
    for page, t in body:
        lab = _page(p, page)
        lines += [f"[{lab}]" if lab.lower().startswith(("p", "pdf")) else f"[p. {lab}]", "", t.strip(), ""]
    figs = con.execute("SELECT kind, num, label, page, caption, rows FROM figures WHERE paper_id=? "
                       "ORDER BY kind, CAST(num AS INTEGER), num", (pid,)).fetchall()
    if figs:
        lines += ["## Tables and figures", ""]
        for kind, num, label, page, caption, rows in figs:
            lines += [f"### {label or (kind.title() + ' ' + str(num))} (page {_page(p, page)})", "",
                      (caption or "").strip(), ""]
            try:
                cells = json.loads(rows) if rows else None
            except Exception:
                cells = None
            if cells and isinstance(cells, list) and cells and isinstance(cells[0], list):
                w = max(len(r) for r in cells)
                fix = lambda r: "| " + " | ".join(str(c or "").replace("|", "/").replace("\n", " ")
                                                  for c in list(r) + [""] * (w - len(r))) + " |"
                lines += [fix(cells[0]), "|" + " --- |" * w] + [fix(r) for r in cells[1:]] + [""]
    if refs:
        text = refs[0]
        for t in refs[1:]:
            text = rag._stitch(text, t)
        lines += ["## References (as printed in the paper)", "", text.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def export(log=print, db_path=config.DB_PATH):
    """Bring data/papers_md/ up to date with the index. Returns the number of files written."""
    if not Path(db_path).exists():
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    papers = {}
    for pid, path, meta, upd, idx_at in con.execute(
            "SELECT id, path, meta, updated_at, indexed_at FROM papers WHERE dup_of IS NULL"):
        p = json.loads(meta or "{}")
        p.update(id=pid, path=path)
        papers[pid] = (p, max(upd or 0, idx_at or 0))
    try:
        fig_time = dict(con.execute("SELECT paper_id, at FROM figure_runs").fetchall())
    except sqlite3.OperationalError:
        fig_time = {}
    keep, written = set(), 0
    for pid, (p, changed) in papers.items():
        f = OUT / _name(pid, p)
        keep.add(f.name)
        if f.exists() and f.stat().st_mtime >= max(changed, fig_time.get(pid) or 0):
            continue
        try:
            f.write_text(paper_markdown(con, pid, p), "utf-8")
            written += 1
        except Exception as e:
            log(f"Couldn't write {f.name}: {e}")
    for f in OUT.glob("[0-9][0-9][0-9][0-9] *.md"):        # papers that left the library, or were renamed
        if f.name not in keep:
            f.unlink()
    rows = sorted(papers.values(), key=lambda x: (-(int(str(x[0].get("year") or 0)[:4] or 0)), x[0].get("title") or ""))
    idx = ["# Your library", "", f"{len(papers)} papers, one file each in this folder. Updated "
           f"{time.strftime('%Y-%m-%d %H:%M')}.", "", "| File | Year | Authors | Title | Journal | DOI |",
           "| --- | --- | --- | --- | --- | --- |"]
    for p, _ in rows:
        cell = lambda s: str(s or "").replace("|", "/")
        idx.append(f"| {_name(p['id'], p)} | {cell(p.get('year'))} | {cell(_authors(p))[:80]} | "
                   f"{cell(p.get('title'))} | {cell(p.get('venue'))} | {cell(p.get('doi'))} |")
    (OUT / "INDEX.md").write_text("\n".join(idx) + "\n", "utf-8")
    con.close()
    if written:
        log(f"Wrote {written} paper file{'s' * (written > 1)} to {OUT}")
    return written
