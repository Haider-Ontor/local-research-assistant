"""Finds PDFs (Zotero + drop folder) and works out their bibliographic metadata."""
import re, shutil, sqlite3, difflib, html, unicodedata, xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote
import requests
import config

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
ARXIV_RE = re.compile(r"arXiv:\s?(\d{4}\.\d{4,5})(v\d+)?", re.I)
UA = {"User-Agent": "ResearchAssistant/1.0 (local research tool"
      + (f"; mailto:{config.CROSSREF_MAILTO})" if config.CROSSREF_MAILTO else ")")}


def year_of(s):
    m = re.search(r"\b(1[6-9]\d\d|20\d\d)\b", s or "")
    return m.group(1) if m else ""


def clean_doi(d):
    d = d.strip().rstrip(".,;:)]}>'\"")
    d = re.sub(r"(?i)(\.pdf|/full|/abstract|/epdf|/pdf)$", "", d)
    return d.lower()


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def similar(a, b):
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


# ---------------------------------------------------------------- Zotero
def _base_attachment_path():
    for prefs in (Path.home() / "AppData/Roaming/Zotero/Zotero/Profiles").glob("*/prefs.js"):
        m = re.search(r'"extensions\.zotero\.baseAttachmentPath",\s*"(.*?)"\)', prefs.read_text("utf-8", "ignore"))
        if m:
            return Path(m.group(1).encode().decode("unicode_escape"))
    return None


def zotero_records():
    """One record per PDF attachment in Zotero, carrying the parent item's metadata."""
    src = config.ZOTERO_DIR / "zotero.sqlite"
    if not (config.USE_ZOTERO and src.exists()):
        return []
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.DATA_DIR / "zotero_copy.sqlite"
    shutil.copy2(src, tmp)  # Zotero locks its database while running, so read a copy
    for ext in ("-wal", "-journal"):
        side = Path(str(src) + ext)
        (shutil.copy2(side, str(tmp) + ext) if side.exists() else Path(str(tmp) + ext).unlink(missing_ok=True))
    con = sqlite3.connect(tmp)
    q = lambda s: con.execute(s).fetchall()
    atts = q("""SELECT a.itemID, ia.key, a.parentItemID, a.path, a.linkMode, p.key, t.typeName
                FROM itemAttachments a JOIN items ia ON ia.itemID = a.itemID
                LEFT JOIN items p ON p.itemID = a.parentItemID
                LEFT JOIN itemTypes t ON t.itemTypeID = p.itemTypeID
                WHERE a.contentType = 'application/pdf'
                  AND a.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND (a.parentItemID IS NULL OR a.parentItemID NOT IN (SELECT itemID FROM deletedItems))""")
    fields = {}
    for iid, name, val in q("""SELECT d.itemID, f.fieldName, v.value FROM itemData d
                               JOIN fields f ON f.fieldID = d.fieldID JOIN itemDataValues v ON v.valueID = d.valueID"""):
        fields.setdefault(iid, {})[name] = val
    creators = {}
    for iid, first, last, mode, ctype in q("""SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode, ct.creatorType
                                             FROM itemCreators ic JOIN creators c ON c.creatorID = ic.creatorID
                                             JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
                                             ORDER BY ic.itemID, ic.orderIndex"""):
        creators.setdefault(iid, []).append({"first": "" if mode == 1 else (first or ""), "last": last or "", "type": ctype})
    try:
        crow = q("""SELECT ci.itemID, c.collectionName FROM collectionItems ci JOIN collections c ON c.collectionID = ci.collectionID
                    WHERE c.collectionID NOT IN (SELECT collectionID FROM deletedCollections)""")
    except sqlite3.OperationalError:
        crow = q("SELECT ci.itemID, c.collectionName FROM collectionItems ci JOIN collections c ON c.collectionID = ci.collectionID")
    colls = {}
    for iid, name in crow:
        colls.setdefault(iid, []).append(name)
    con.close()

    base = None
    out = []
    for att_id, att_key, parent_id, path, link_mode, parent_key, typ in atts:
        if not path:
            continue
        if path.startswith("storage:"):
            fpath = config.ZOTERO_DIR / "storage" / att_key / path[len("storage:"):]
        elif path.startswith("attachments:"):
            base = base or _base_attachment_path()
            if not base:
                continue
            fpath = base / path[len("attachments:"):]
        else:
            fpath = Path(path)
        rec = {"path": str(fpath), "source": "zotero", "zotero_att_key": att_key, "zotero_key": parent_key or "",
               "collections": sorted(set(colls.get(parent_id, []) + colls.get(att_id, [])))}
        if parent_id:
            f = fields.get(parent_id, {})
            people = creators.get(parent_id, [])
            authors = [c for c in people if c["type"] in ("author", "inventor", "presenter", "contributor")] or people
            rec.update({
                "title": f.get("title", ""), "authors": [{"first": a["first"], "last": a["last"]} for a in authors],
                "year": year_of(f.get("date", "")), "item_type": typ or "",
                "venue": f.get("publicationTitle") or f.get("proceedingsTitle") or f.get("conferenceName")
                         or f.get("repository") or f.get("bookTitle") or f.get("university") or "",
                "journal_abbr": f.get("journalAbbreviation", ""), "volume": f.get("volume", ""),
                "issue": f.get("issue", ""), "pages": f.get("pages", ""), "doi": clean_doi(f.get("DOI", "")) if f.get("DOI") else "",
                "url": f.get("url", ""), "publisher": f.get("publisher", ""), "place": f.get("place", ""),
                "abstract": f.get("abstractNote", ""), "meta_source": "zotero"})
            if not rec["doi"]:
                m = DOI_RE.search(f.get("extra", "") or "")
                rec["doi"] = clean_doi(m.group(1)) if m else ""
        else:
            rec["meta_source"] = None   # stand-alone PDF in Zotero: detect metadata from the file
        out.append(rec)
    return out


def folder_files():
    d = config.PAPERS_DIR
    if not d.exists():
        return []
    return sorted(str(p) for p in d.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


# ---------------------------------------------------------------- metadata lookup for loose PDFs
def _get(url, **kw):
    try:
        r = requests.get(url, headers=UA, timeout=12, **kw)
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


def _from_crossref(m):
    dp = (m.get("published-print") or m.get("published-online") or m.get("issued") or {}).get("date-parts") or [[None]]
    typ = {"journal-article": "journalArticle", "proceedings-article": "conferencePaper", "posted-content": "preprint",
           "book-chapter": "bookSection", "book": "book", "dissertation": "thesis", "report": "report"}.get(m.get("type"), "journalArticle")
    return {
        "title": strip_tags((m.get("title") or [""])[0]),
        "authors": [{"first": a.get("given", ""), "last": a.get("family") or a.get("name", "")} for a in m.get("author", [])],
        "year": str(dp[0][0] or ""), "item_type": typ,
        "venue": strip_tags((m.get("container-title") or [""])[0]) or (m.get("institution") or [{}])[0].get("name", ""),
        "journal_abbr": strip_tags((m.get("short-container-title") or [""])[0]),
        "volume": m.get("volume", ""), "issue": m.get("issue", ""), "pages": m.get("page", ""),
        "doi": clean_doi(m.get("DOI", "")), "url": m.get("URL", ""), "publisher": m.get("publisher", ""), "place": "",
        "abstract": strip_tags(m.get("abstract", "")), "meta_source": "crossref"}


def crossref_by_doi(doi):
    r = _get("https://api.crossref.org/works/" + quote(doi, safe="/:"))
    return _from_crossref(r.json()["message"]) if r else None


def crossref_by_title(title):
    r = _get("https://api.crossref.org/works", params={"query.bibliographic": title, "rows": 5})
    if not r:
        return None
    def score(it):
        t = strip_tags((it.get("title") or [""])[0])
        s = similar(t, title)
        if len(t) > len(title) >= 40:            # candidate may be a truncated title (e.g. from a file name)
            s = max(s, similar(t[:len(title)], title))
        return s
    best = max(r.json()["message"].get("items", []), key=score, default=None)
    if best and score(best) >= 0.88:
        return _from_crossref(best)
    return None


def title_from_filename(path):
    """Zotero names files 'Author et al. - 2020 - Title.pdf'; use the title part, or the whole stem."""
    stem = Path(path).stem
    m = re.match(r"^.{2,80}? - (\d{4}) - (.+)$", stem)
    t = m.group(2) if m else stem
    t = re.sub(r"[_]+", " ", t).strip()
    return t if len(t) >= 15 and not re.fullmatch(r"[\d\s.\-]+", t) else ""


def arxiv_by_id(aid):
    r = _get("https://export.arxiv.org/api/query", params={"id_list": aid})
    if not r:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    e = ET.fromstring(r.text).find("a:entry", ns)
    if e is None or e.find("a:title", ns) is None:
        return None
    names = [n.findtext("a:name", "", ns) for n in e.findall("a:author", ns)]
    split = lambda n: {"first": " ".join(n.split()[:-1]), "last": n.split()[-1] if n.split() else n}
    return {"title": " ".join(e.findtext("a:title", "", ns).split()), "authors": [split(n) for n in names],
            "year": e.findtext("a:published", "", ns)[:4], "item_type": "preprint", "venue": "arXiv",
            "journal_abbr": "", "volume": "", "issue": "", "pages": "", "doi": f"10.48550/arxiv.{aid}",
            "url": f"https://arxiv.org/abs/{aid}", "publisher": "", "place": "",
            "abstract": " ".join(e.findtext("a:summary", "", ns).split()), "meta_source": "arxiv"}


def dois_in(text):
    out = []
    for m in DOI_RE.finditer(text or ""):
        d = clean_doi(m.group(1))
        if d not in out:
            out.append(d)
    return out


def title_on_pages(title, text):
    """True if most of the title's meaningful words appear in the given page text."""
    norm = lambda s: re.sub(r"[^a-z0-9 ]", " ", unicodedata.normalize("NFKD", s or "").lower())
    words = [w for w in norm(title).split() if len(w) > 3]
    if not words:
        return True
    page = set(norm(text).split())
    return sum(w in page for w in words) / len(words) >= 0.7


def guess_title(doc):
    """Largest-font text near the top of page 1 is usually the title."""
    try:
        page = doc[0]
        spans = [(round(s["size"], 1), s["text"].strip(), l["bbox"][1])
                 for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]
                 if s["text"].strip() and l["bbox"][1] < page.rect.height * 0.6]
        if not spans:
            return ""
        top = max(s[0] for s in spans if len(s[1]) > 3) if any(len(s[1]) > 3 for s in spans) else 0
        title = " ".join(t for size, t, y in spans if abs(size - top) < 0.6)
        title = re.sub(r"\s+", " ", title).strip()
        return title if 10 <= len(title) <= 300 else ""
    except Exception:
        return ""


def mismatched_attachment(doc, zmeta):
    """If a PDF attached to a Zotero item is really a different paper (wrong file attached), return that paper's
    details from the DOI printed on it - only when that DOI's title is itself printed on the first pages."""
    text = "".join(doc[i].get_text("text") + "\n" for i in range(min(2, len(doc))))
    if len(text.strip()) < 200 or not zmeta.get("title") or title_on_pages(zmeta["title"], text):
        return None
    for doi in dois_in(text)[:5]:
        if doi == zmeta.get("doi"):
            return None
        meta = crossref_by_doi(doi)
        if meta and meta["title"] and title_on_pages(meta["title"], text):
            meta["meta_source"] = "pdf-doi"
            meta["zotero_title"] = zmeta["title"]
            return meta
    return None


def detect_metadata(doc, path):
    """Best-effort metadata for a PDF that didn't come with any: DOI → Crossref, arXiv id, title search, file info."""
    text = ""
    for i in range(min(2, len(doc))):
        text += doc[i].get_text("text") + "\n"
    pdfmeta = doc.metadata or {}
    blob = " ".join(str(v) for v in pdfmeta.values() if v) + "\n" + text
    # Page 1-2 often also carry DOIs of *cited* papers, so only accept a DOI whose title is printed on those pages.
    scanned = len(text.strip()) < 200
    unchecked = []                       # DOIs Crossref couldn't be asked about (e.g. offline)
    for doi in dois_in(blob)[:5]:
        meta = crossref_by_doi(doi)
        if meta and meta["title"] and (scanned or title_on_pages(meta["title"], text)):
            return meta
        if not meta:
            unchecked.append(doi)
    m = ARXIV_RE.search(blob)
    if m:
        meta = arxiv_by_id(m.group(1))
        if meta:
            return meta
    guess = guess_title(doc)
    meta_title = (pdfmeta.get("title") or "").strip()
    if len(meta_title) < 10 or meta_title.lower().endswith((".pdf", ".doc", ".docx")) or "microsoft word" in meta_title.lower():
        meta_title = ""
    from_name = title_from_filename(path)
    seen = set()
    for cand in (guess, meta_title, from_name):
        if cand and cand.lower() not in seen:
            seen.add(cand.lower())
            meta = crossref_by_title(cand)
            if meta:
                return meta
    authors = [{"first": "", "last": a.strip()} for a in re.split(r";|,| and ", pdfmeta.get("author") or "") if a.strip()][:8]
    ym = re.match(r"^.{2,80}? - (\d{4}) - ", Path(path).stem)
    return {"title": guess or meta_title or from_name or Path(path).stem, "authors": authors,
            "year": ym.group(1) if ym else "", "item_type": "document",
            "venue": "", "journal_abbr": "", "volume": "", "issue": "", "pages": "",
            "doi": unchecked[0] if unchecked else "", "url": "", "publisher": "", "place": "",
            "abstract": "", "meta_source": "file"}
