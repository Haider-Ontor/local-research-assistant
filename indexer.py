"""Builds / updates the searchable index of your papers. Run directly to update: python indexer.py"""
import os, re, sys, json, time, hashlib, sqlite3, unicodedata
import numpy as np
import pymupdf
from openai import OpenAI
import config, figures, library

SCHEMA = figures.SCHEMA + """
CREATE TABLE IF NOT EXISTS papers(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, mtime REAL, size INTEGER, sha1 TEXT, source TEXT,
  zotero_key TEXT, zotero_att_key TEXT, meta TEXT, n_pages INTEGER, first_printed_page INTEGER,
  dup_of INTEGER, status TEXT DEFAULT 'ok', meta_checked REAL, indexed_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, paper_id INTEGER, page INTEGER, is_ref INTEGER, text TEXT, emb BLOB);
CREATE INDEX IF NOT EXISTS chunks_paper ON chunks(paper_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, title, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS summaries(paper_id INTEGER PRIMARY KEY, text TEXT);
CREATE TABLE IF NOT EXISTS guides(paper_id INTEGER PRIMARY KEY, text TEXT, created REAL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""
META_KEYS = ["title", "authors", "year", "item_type", "venue", "journal_abbr", "volume", "issue", "pages", "doi",
             "url", "publisher", "place", "abstract", "meta_source", "collections", "zotero_title"]
REF_HEAD = re.compile(r"^\W*(?:\d+\.?|[IVX]+\.?)?\s*(references|bibliography|literature cited|reference list|works cited)\b\W*$", re.I)
FLAGS = pymupdf.TEXT_DEHYPHENATE | pymupdf.TEXT_MEDIABOX_CLIP
RETRY_LOOKUP_AFTER = 3 * 86400


def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.executescript(SCHEMA)
    return con


def client():
    return OpenAI(base_url=config.EMBED_URL, api_key="local", timeout=300)


def embed(cl, texts, batch=32):
    out = []
    for i in range(0, len(texts), batch):
        r = cl.embeddings.create(model=config.EMBED_MODEL, input=texts[i:i + batch])
        out.extend(d.embedding for d in sorted(r.data, key=lambda d: d.index))
    v = np.asarray(out, dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    return v


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def norm_text(t):
    t = unicodedata.normalize("NFKC", t).replace(chr(0xFFFD), "")   # drop unreadable glyphs (e.g. publisher badges)
    return re.sub(r"\s+", " ", t).strip()


def page_segments(doc):
    """[(page_no, is_ref, text)] in reading order; text after a late 'References' heading is flagged."""
    n, in_refs, out = len(doc), False, []
    for i, page in enumerate(doc):
        cur, cur_ref = [], in_refs
        for b in page.get_text("blocks", flags=FLAGS):
            if b[6] != 0:
                continue
            first_line = b[4].strip().split("\n")[0].strip()
            if not in_refs and (i >= n * 0.4 or n <= 3) and REF_HEAD.match(first_line):
                if cur:
                    out.append((i + 1, cur_ref, norm_text(" ".join(cur))))
                cur, in_refs, cur_ref = [], True, True
            t = re.sub(r"(\w)[-\u00ad]\s*\n\s*([a-z])", r"\1\2", b[4]).strip()   # join words split across lines
            if t:
                cur.append(t)
        if cur:
            out.append((i + 1, cur_ref, norm_text(" ".join(cur))))
    return out


def chunk(text, size=config.CHUNK_WORDS, overlap=config.CHUNK_OVERLAP):
    words = text.split()
    if len(words) < 8:
        return []
    if len(words) <= size * 1.3:
        return [" ".join(words)]
    out, start, step = [], 0, size - overlap
    while start < len(words):
        end = start + size
        if len(words) - end < 60:
            end = len(words)
        out.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += step
    return out


# ---------------------------------------------------------------- layout-aware Markdown reading (pymupdf4llm)
INDEX_VERSION = 2      # 2 = Markdown text: real tables, section headings, correct two-column reading order
_PIC = re.compile(r"<!-- Start of picture text -->.*?<!-- End of picture text -->", re.S)


def md_clean(t):
    """Tidy pymupdf4llm Markdown: drop figure-label noise and HTML bits, keep headings and tables."""
    t = unicodedata.normalize("NFKC", t or "").replace(chr(0xFFFD), "")
    t = _PIC.sub(" ", t)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"<br\s*/?>", " ", t, flags=re.I)
    t = re.sub(r"<sup>(.*?)</sup>", r"^\1", t, flags=re.I | re.S)
    t = re.sub(r"<sub>(.*?)</sub>", r"_\1", t, flags=re.I | re.S)
    t = re.sub(r"</?[a-zA-Z][^>]*>", "", t)
    t = re.sub(r"(?m)^.*intentionally omitted.*$", "", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _size(t):
    """Passage size in 'words' (tables have few spaces, so count characters too)."""
    return max(len(t.split()), len(t) / 6.5)


def _is_table(block):
    lines = [l for l in block.split("\n") if l.strip()]
    return len(lines) >= 2 and sum(l.lstrip().startswith("|") for l in lines) >= 0.6 * len(lines)


def _split_table(block, size):
    """Split a huge table into parts of whole rows, repeating its header rows."""
    lines = block.split("\n")
    head = lines[:2] if len(lines) > 2 and set(lines[1].replace("|", "").strip()) <= set("-: ") else lines[:1]
    parts, cur = [], []
    for row in lines[len(head):]:
        if cur and _size("\n".join(head + cur + [row])) > size:
            parts.append("\n".join(head + cur))
            cur = []
        cur.append(row)
    if cur:
        parts.append("\n".join(head + cur))
    return parts


def md_segments(doc):
    """[(page_no, is_ref, section, block)] in reading order; text after a late References heading is flagged."""
    import pymupdf4llm
    pages = pymupdf4llm.to_markdown(doc, page_chunks=True)
    n, out, in_refs, section = len(pages), [], False, ""
    page_blocks = [[b.strip() for b in re.split(r"\n\s*\n", md_clean(pg.get("text", ""))) if b.strip()] for pg in pages]
    # running headers/footers (journal name, author line, page numbers) repeat on most pages: drop them
    key = lambda b: re.sub(r"[^a-z]+", "", b.lower())
    counts = {}
    for blocks in page_blocks:
        for k in {key(b) for b in blocks if len(b) < 120}:
            counts[k] = counts.get(k, 0) + 1
    running = {k for k, c in counts.items() if k and c >= max(3, 0.4 * n)}
    for i, pg in enumerate(pages):
        pno = int((pg.get("metadata") or {}).get("page_number") or i + 1)
        late = i >= n * 0.4 or n <= 3
        for block in page_blocks[i]:
            if len(block) < 120 and key(block) in running:
                continue
            first = block.split("\n")[0]
            plain = re.sub(r"^[#\s*_]+|[*_\s:]+$", "", first).strip()
            if not in_refs and late and REF_HEAD.match(plain) and (first.lstrip().startswith(("#", "*")) or plain == first.strip()):
                in_refs = True
                rest = block.split("\n", 1)[1] if "\n" in block else ""
                if rest.strip():
                    out.append((pno, True, plain, rest.strip()))
                continue
            if first.lstrip().startswith("#"):
                section = plain[:90]
            out.append((pno, in_refs, section, block))
    return out


def md_chunks(segs, size=config.CHUNK_WORDS):
    """Pack blocks into passages of about `size` words, never across pages; tables stay whole (split by rows if
    huge). A passage that starts mid-section begins with '§ <section heading>' for context."""
    out, cur, cur_n, key, sec = [], [], 0, None, ""

    def flush():
        nonlocal cur, cur_n
        if cur:
            text = "\n\n".join(cur)
            if sec and not text.lstrip().startswith("#"):
                text = f"§ {sec}\n{text}"
            out.append((key[0], key[1], text))
        cur, cur_n = [], 0

    for pno, ref, section, block in segs:
        n = _size(block)
        if (pno, ref) != key or cur_n + n > size * 1.3:
            flush()
            key, sec = (pno, ref), section
        if _is_table(block) and n > size * 3:
            for part in _split_table(block, size * 2):
                flush()
                key, sec = (pno, ref), section
                cur, cur_n = [part], _size(part)
            flush()
            continue
        if n > size * 1.6 and not _is_table(block):
            words = block.split()
            for j in range(0, len(words), size):
                if cur:
                    flush()
                    key, sec = (pno, ref), section
                cur, cur_n = [" ".join(words[j:j + size])], min(size, len(words) - j)
            continue
        cur.append(block)
        cur_n += n
    flush()
    return [(p, r, t) for p, r, t in out if _size(t) >= 8]


def extract_pieces(doc):
    """[(page, is_ref, passage)] for a PDF: layout-aware Markdown, falling back to plain text blocks."""
    try:
        pieces = md_chunks(md_segments(doc))
        if sum(_size(t) for _, _, t in pieces) >= 80:
            return pieces
    except Exception:
        pass
    return [(pg, ref, c) for pg, ref, text in page_segments(doc) for c in chunk(text)]


def store_pieces(con, cl, pid, pieces, title):
    if not pieces:
        return
    vecs = embed(cl, [f"search_document: {title}\n{c}"[:2400] for _, _, c in pieces])
    for (pg, ref, c), v in zip(pieces, vecs):
        cid = con.execute("INSERT INTO chunks(paper_id, page, is_ref, text, emb) VALUES(?,?,?,?,?)",
                          (pid, pg, int(ref), c, v.tobytes())).lastrowid
        con.execute("INSERT INTO chunks_fts(rowid, text, title) VALUES(?,?,?)", (cid, c, title))


def rechunk(con, cl, pid, path, title):
    """Re-read one paper's text with the current reader, keeping its id, metadata and study guide."""
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            return 0
        pieces = extract_pieces(doc)
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE paper_id=?", (pid,))]
    con.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
    con.execute("DELETE FROM chunks WHERE paper_id=?", (pid,))
    store_pieces(con, cl, pid, pieces, title)
    words = sum(len(c.split()) for _, _, c in pieces)
    con.execute("UPDATE papers SET status=?, updated_at=? WHERE id=?", ("ok" if words >= 80 else "no-text", time.time(), pid))
    con.commit()
    return len(pieces)


def sync_proposal(con, cl, log=print, force=False):
    """Index your PhD proposal draft as a special 'paper' (source='proposal') whenever the file changes."""
    import proposal
    src = proposal.find_file()
    row = con.execute("SELECT id FROM papers WHERE source='proposal'").fetchone()
    if not src:
        if row:
            delete_paper(con, row[0])
            con.commit()
        return False
    st = src.stat()
    info = json.dumps({"path": str(src), "mtime": round(st.st_mtime, 1), "size": st.st_size})
    if row and not force and kv_get(con, "proposal_src") == info and proposal.PDF.exists():
        return False
    log(f"Reading your PhD proposal: {src.name}")
    pdf = proposal.to_pdf(src, log=log)
    with pymupdf.open(pdf) as doc:
        pieces = extract_pieces(doc)
        n_pages = len(doc)
    head = next((t for _, _, t in pieces if t.lstrip().startswith("#")), "")
    title = re.sub(r"[#*_]", "", head.split("\n")[0]).strip() or src.stem
    meta = _meta_from({"title": title, "venue": "Your PhD proposal (draft)", "year": "", "item_type": "document",
                       "meta_source": "proposal"})
    if row:
        delete_paper(con, row[0])
    now, pst = time.time(), pdf.stat()
    pid = con.execute("INSERT INTO papers(path, mtime, size, sha1, source, meta, n_pages, status, meta_checked, indexed_at, "
                      "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (str(pdf), pst.st_mtime, pst.st_size, None, "proposal", json.dumps(meta), n_pages, "ok", now, now,
                       now)).lastrowid
    store_pieces(con, cl, pid, pieces, title)
    kv_set(con, "proposal_src", info)
    con.commit()
    log(f"Proposal indexed: {title[:80]} ({n_pages} pages, {len(pieces)} passages)")
    return True


def kv_get(con, key):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def kv_set(con, key, value):
    con.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?,?)", (key, str(value)))


def printed_offset(meta, n_pages):
    m = re.match(r"^\s*(\d+)\s*[-–—]+\s*(\d+)\s*$", meta.get("pages") or "")
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b - a + 1 == n_pages:
            return a
    return None


def delete_paper(con, pid):
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE paper_id=?", (pid,))]
    con.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
    con.execute("DELETE FROM chunks WHERE paper_id=?", (pid,))
    con.execute("DELETE FROM summaries WHERE paper_id=?", (pid,))
    con.execute("DELETE FROM guides WHERE paper_id=?", (pid,))
    con.execute("DELETE FROM figures WHERE paper_id=?", (pid,))
    con.execute("DELETE FROM figure_runs WHERE paper_id=?", (pid,))
    con.execute("DELETE FROM papers WHERE id=?", (pid,))


def set_title_in_fts(con, pid, title):
    rows = con.execute("SELECT id, text FROM chunks WHERE paper_id=?", (pid,)).fetchall()
    con.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(r[0],) for r in rows])
    con.executemany("INSERT INTO chunks_fts(rowid, text, title) VALUES(?,?,?)", [(r[0], r[1], title) for r in rows])


def _meta_from(rec):
    m = {k: rec.get(k, [] if k in ("authors", "collections") else "") for k in META_KEYS}
    return m


def update_index(log=print, progress=None):
    """Incremental: new or changed PDFs are indexed, removed ones dropped, Zotero metadata refreshed."""
    con, cl = connect(), client()
    try:
        embed(cl, ["search_document: ping"])
    except Exception as e:
        raise RuntimeError(f"Can't reach the search model ({e}). Start the AI engine first.")

    zrecs = library.zotero_records()
    frecs = [{"path": p, "source": "folder", "meta_source": None, "collections": []} for p in library.folder_files()]
    log(f"Found {len(zrecs)} PDFs in Zotero and {len(frecs)} in {config.PAPERS_DIR}")
    cands = {}
    for r in zrecs + frecs:
        if os.path.exists(r["path"]):
            cands.setdefault(os.path.normcase(os.path.abspath(r["path"])), r)

    # 1) drop papers whose files are gone (and anything marked as their duplicate)
    removed = 0
    for pid, path in con.execute("SELECT id, path FROM papers WHERE source != 'proposal'").fetchall():
        if os.path.normcase(os.path.abspath(path)) not in cands:
            for (d,) in con.execute("SELECT id FROM papers WHERE dup_of=?", (pid,)).fetchall():
                delete_paper(con, d)
            delete_paper(con, pid)
            removed += 1
    con.commit()

    # 2) work out what's new / changed; refresh metadata on unchanged Zotero items
    existing = {os.path.normcase(os.path.abspath(r[1])): r for r in
                con.execute("SELECT id, path, mtime, size, meta, meta_checked, source FROM papers").fetchall()}
    todo, refreshed = [], 0
    now = time.time()
    for key, r in cands.items():
        st = os.stat(r["path"])
        row = existing.get(key)
        if row and abs(row[2] - st.st_mtime) < 2 and row[3] == st.st_size:
            old = json.loads(row[4] or "{}")
            # a PDF found to be attached to the wrong Zotero item keeps its own details until Zotero agrees again
            if r.get("meta_source") == "zotero" and (old.get("meta_source") != "pdf-doi" or
                                                     library.similar(r.get("title", ""), old.get("title", "")) > 0.9):
                new = _meta_from(r)
                if new != old:
                    con.execute("UPDATE papers SET meta=?, zotero_key=?, updated_at=? WHERE id=?",
                                (json.dumps(new), r.get("zotero_key", ""), now, row[0]))
                    if new.get("title") != old.get("title"):
                        set_title_in_fts(con, row[0], new.get("title", ""))
                    refreshed += 1
            elif old and sorted(old.get("collections", [])) != sorted(r.get("collections", [])):
                old["collections"] = r.get("collections", [])
                con.execute("UPDATE papers SET meta=?, updated_at=? WHERE id=?", (json.dumps(old), now, row[0]))
                refreshed += 1
            if old.get("meta_source") == "file" and now - (row[5] or 0) > RETRY_LOOKUP_AFTER:
                try:
                    with pymupdf.open(r["path"]) as doc:
                        meta = library.detect_metadata(doc, r["path"])
                    if meta["meta_source"] != "file":
                        meta["collections"] = old.get("collections", [])
                        con.execute("UPDATE papers SET meta=?, updated_at=? WHERE id=?", (json.dumps(meta), now, row[0]))
                        set_title_in_fts(con, row[0], meta.get("title", ""))
                        refreshed += 1
                    con.execute("UPDATE papers SET meta_checked=? WHERE id=?", (now, row[0]))
                except Exception:
                    pass
            continue
        todo.append((r, st, row))
    con.commit()

    # 2b) one-time re-read of every paper when the text reader has improved (keeps ids, metadata, study guides)
    if kv_get(con, "index_version") != str(INDEX_VERSION):
        rows = [r for r in con.execute("SELECT id, path, meta FROM papers WHERE dup_of IS NULL AND status != 'encrypted'")
                if os.path.exists(r[1])]
        skip = {os.path.normcase(os.path.abspath(r["path"])) for r, _, _ in todo}
        rows = [r for r in rows if os.path.normcase(os.path.abspath(r[1])) not in skip]
        log(f"Re-reading {len(rows)} papers with the improved PDF reader (one time only, a few minutes)…")
        for i, (pid, path, meta) in enumerate(rows, 1):
            if progress:
                progress(i, len(rows), os.path.basename(path))
            title = json.loads(meta or "{}").get("title", "")
            try:
                n = rechunk(con, cl, pid, path, title)
                log(f"[{i}/{len(rows)}] {title[:80]}  ({n} passages)")
            except Exception as e:
                con.rollback()
                log(f"[{i}/{len(rows)}] FAILED {os.path.basename(path)}: {e}")
        kv_set(con, "index_version", INDEX_VERSION)
        con.commit()

    # 3) index new / changed files
    doi_map = {}
    for pid, meta, dup in con.execute("SELECT id, meta, dup_of FROM papers").fetchall():
        d = json.loads(meta or "{}").get("doi")
        if d and dup is None:
            doi_map[d] = pid
    added = dups = failed = 0
    for i, (r, st, row) in enumerate(todo, 1):
        name = os.path.basename(r["path"])
        if progress:
            progress(i, len(todo), name)
        try:
            res = index_one(con, cl, r, st, row, doi_map)
            if res == "dup":
                dups += 1
                log(f"[{i}/{len(todo)}] duplicate, skipped: {name}")
            else:
                added += 1
                log(f"[{i}/{len(todo)}] {res}")
        except Exception as e:
            failed += 1
            con.rollback()
            log(f"[{i}/{len(todo)}] FAILED {name}: {e}")

    # 4) your PhD proposal draft (re-read only when the file has changed)
    try:
        sync_proposal(con, cl, log=log)
    except Exception as e:
        con.rollback()
        log(f"Couldn't read your proposal: {e}")

    # 5) tables and figures (found once per paper, a few seconds each; kept until the PDF changes)
    try:
        figures.sync(con, log=log, progress=progress)
    except Exception as e:
        con.rollback()
        log(f"Couldn't look for tables and figures: {e}")
    summary = {"indexed": added, "duplicates": dups, "removed": removed, "refreshed": refreshed, "failed": failed,
               "total": con.execute("SELECT count(*) FROM papers WHERE dup_of IS NULL AND source != 'proposal'").fetchone()[0]}
    con.close()
    log(f"Done: {added} indexed, {dups} duplicates skipped, {removed} removed, {refreshed} metadata updates, "
        f"{failed} failed. Library now has {summary['total']} papers.")
    return summary


def _norm_title(t):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", t or "").lower())


def same_paper(con, meta, r, doi_map):
    """id of an already-indexed copy of the same paper, else None.
    Matches on DOI, or on title + year. PDFs attached to the same Zotero item (e.g. a paper and its
    supplementary material) are never treated as duplicates of each other."""
    zkey = r.get("zotero_key") or ""
    cands = []
    if meta.get("doi") and meta["doi"] in doi_map:
        cands.append(doi_map[meta["doi"]])
    nt = _norm_title(meta.get("title"))
    if len(nt) >= 25 and meta.get("meta_source") != "file":
        for pid, m in con.execute("SELECT id, meta FROM papers WHERE dup_of IS NULL").fetchall():
            om = json.loads(m or "{}")
            if _norm_title(om.get("title")) == nt and str(om.get("year", "")) == str(meta.get("year", "")):
                cands.append(pid)
    for pid in cands:
        row = con.execute("SELECT zotero_key FROM papers WHERE id=?", (pid,)).fetchone()
        if row and not (zkey and row[0] == zkey):
            return pid
    return None


def index_one(con, cl, r, st, row, doi_map):
    path, now = r["path"], time.time()
    sha1 = sha1_of(path)
    keep_manual = None
    if row:
        old = json.loads(row[4] or "{}")
        if old.get("meta_source") == "manual-doi":
            keep_manual = old
        delete_paper(con, row[0])
    base = dict(path=path, mtime=st.st_mtime, size=st.st_size, sha1=sha1, source=r["source"],
                zotero_key=r.get("zotero_key", ""), zotero_att_key=r.get("zotero_att_key", ""), indexed_at=now, updated_at=now)

    def insert(**kw):
        d = {**base, **kw}
        cols = ",".join(d)
        cur = con.execute(f"INSERT INTO papers({cols}) VALUES({','.join('?' * len(d))})", list(d.values()))
        return cur.lastrowid

    hit = con.execute("SELECT id FROM papers WHERE sha1=? AND dup_of IS NULL", (sha1,)).fetchone()
    if hit:                                   # byte-identical file already indexed (Zotero or folder)
        insert(dup_of=hit[0], meta="{}", status="duplicate")
        con.commit()
        return "dup"
    doc = pymupdf.open(path)
    if doc.needs_pass:
        insert(meta=json.dumps(_meta_from({"title": os.path.basename(path)})), status="encrypted", n_pages=0)
        con.commit()
        return f"password-protected, can't read: {os.path.basename(path)}"
    if r.get("meta_source") == "zotero":
        meta = _meta_from(r)
        wrong = library.mismatched_attachment(doc, meta)      # PDF attached to the wrong Zotero item?
        if wrong:
            wrong["collections"] = meta.get("collections", [])
            meta = _meta_from(wrong)
    else:
        meta = keep_manual or library.detect_metadata(doc, path)
        meta["collections"] = r.get("collections", [])
        meta = _meta_from(meta)
    same = same_paper(con, meta, r, doi_map)
    if same:
        insert(dup_of=same, meta=json.dumps(meta), status="duplicate")
        con.commit()
        doc.close()
        return "dup"
    pieces = extract_pieces(doc)
    n_pages = len(doc)
    doc.close()
    words = sum(len(c.split()) for _, _, c in pieces)
    status = "ok" if words >= 80 else "no-text"
    pid = insert(meta=json.dumps(meta), n_pages=n_pages, first_printed_page=printed_offset(meta, n_pages),
                 status=status, meta_checked=now)
    store_pieces(con, cl, pid, pieces, meta.get("title", ""))
    if meta.get("doi"):
        doi_map[meta["doi"]] = pid
    con.commit()
    note = "" if status == "ok" else "  ⚠ no selectable text (scanned PDF?)"
    return f"{meta.get('title', '')[:90]}  ({n_pages} pages, {len(pieces)} passages){note}"


def set_manual_doi(paper_id, doi):
    """Replace a paper's details with Crossref's record for the given DOI (for loose PDFs)."""
    meta = library.crossref_by_doi(library.clean_doi(doi))
    if not meta:
        return None
    con = connect()
    old = json.loads(con.execute("SELECT meta FROM papers WHERE id=?", (paper_id,)).fetchone()[0] or "{}")
    meta["collections"] = old.get("collections", [])
    meta["meta_source"] = "manual-doi"
    meta = _meta_from(meta)
    n = con.execute("SELECT n_pages FROM papers WHERE id=?", (paper_id,)).fetchone()[0] or 0
    con.execute("UPDATE papers SET meta=?, first_printed_page=?, updated_at=? WHERE id=?",
                (json.dumps(meta), printed_offset(meta, n), time.time(), paper_id))
    set_title_in_fts(con, paper_id, meta.get("title", ""))
    con.commit()
    con.close()
    return meta


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    update_index()
