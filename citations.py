"""Reference-list and in-text citation formatting: IEEE, Harvard (Cite Them Right), APA 7, BibTeX."""
import re, unicodedata

STYLES = ["IEEE", "Harvard", "APA 7", "BibTeX"]


def _initials(first, spaced=True):
    out = []
    for part in (first or "").split():
        if "-" in part:
            out.append("-".join(p[0].upper() + "." for p in part.split("-") if p))
        else:
            out.extend(p[0].upper() + "." for p in part.split(".") if p)
    return (" " if spaced else "").join(out)


def _ieee_name(a):
    ini = _initials(a["first"])
    return f"{ini} {a['last']}".strip()


def _harvard_name(a):
    ini = _initials(a["first"], spaced=False)
    return f"{a['last']}, {ini}" if ini else a["last"]


def _apa_name(a):
    ini = _initials(a["first"])
    return f"{a['last']}, {ini}" if ini else a["last"]


def _pages(p):
    p = (p or "").strip().replace("--", "–").replace("-", "–")
    return p


def _article_no(pg):
    """Journals like Elsevier/MDPI/IEEE Access use article numbers (e.g. 103366, e0215634) instead of page ranges."""
    return bool(pg) and "–" not in pg and (len(re.sub(r"\D", "", pg)) >= 5 or pg[:1].isalpha())


def _doi_url(p):
    return f"https://doi.org/{p['doi']}" if p.get("doi") else (p.get("url") or "")


def _is_conf(p):
    return p.get("item_type") == "conferencePaper"


def _ieee(p, n=None):
    names = [_ieee_name(a) for a in p.get("authors", [])]
    if len(names) > 6:
        auth = names[0] + " et al."
    elif len(names) > 2:
        auth = ", ".join(names[:-1]) + ", and " + names[-1]
    else:
        auth = " and ".join(names)
    parts = [f'{auth}, "{p.get("title", "").rstrip(".")},"'] if auth else [f'"{p.get("title", "").rstrip(".")},"']
    venue = p.get("journal_abbr") or p.get("venue") or ""
    pg = _pages(p.get("pages"))
    pgs = f"Art. no. {pg}" if _article_no(pg) else (("pp. " if "–" in pg else "p. ") + pg) if pg else ""
    if _is_conf(p):
        bits = [f"in Proc. {venue}" if venue and not venue.lower().startswith("proc") else f"in {venue}" if venue else "",
                p.get("year", ""), pgs]
    elif p.get("item_type") == "preprint":
        bits = [venue or "Preprint", p.get("year", "")]
    else:
        bits = [venue, f"vol. {p['volume']}" if p.get("volume") else "", f"no. {p['issue']}" if p.get("issue") else "",
                pgs, p.get("year", "")]
    tail = ", ".join(b for b in bits if b)
    s = " ".join(parts) + (" " + tail if tail else "")
    if p.get("doi"):
        s += f", doi: {p['doi']}."
    elif p.get("url"):
        s += f". [Online]. Available: {p['url']}"
    else:
        s += "."
    return (f"[{n}] " if n else "") + s


def _harvard(p, n=None):
    names = [_harvard_name(a) for a in p.get("authors", [])]
    if len(names) >= 4:
        auth = names[0] + " et al."
    elif len(names) >= 2:
        auth = ", ".join(names[:-1]) + " and " + names[-1]
    else:
        auth = names[0] if names else (p.get("venue") or "Anon.")
    year = p.get("year") or "no date"
    venue = p.get("venue") or ""
    s = f"{auth} ({year}) '{p.get('title', '').rstrip('.')}'"
    if _is_conf(p):
        s += f", in {venue}." if venue else "."
        if p.get("pages"):
            s = s.rstrip(".") + f", pp. {_pages(p['pages'])}."
    else:
        if venue:
            s += f", {venue}"
        if p.get("volume"):
            s += f", {p['volume']}" + (f"({p['issue']})" if p.get("issue") else "")
        if p.get("pages"):
            pg = _pages(p["pages"])
            s += f", {pg}" if _article_no(pg) else f", {'pp' if '–' in pg else 'p'}. {pg}"
        s += "."
    link = _doi_url(p)
    if link:
        s += f" Available at: {link}."
    return s


def _apa(p, n=None):
    names = [_apa_name(a) for a in p.get("authors", [])]
    if len(names) > 20:
        auth = ", ".join(names[:19]) + ", . . . " + names[-1]
    elif len(names) >= 2:
        auth = ", ".join(names[:-1]) + ", & " + names[-1]
    else:
        auth = names[0] if names else ""
    year = p.get("year") or "n.d."
    title = p.get("title", "").rstrip(".")
    venue = p.get("venue") or ""
    s = (f"{auth} ({year}). " if auth else f"{title}. ({year}). ") + (f"{title}." if auth else "")
    if _is_conf(p):
        s += f" In {venue}" + (f" (pp. {_pages(p['pages'])})" if p.get("pages") else "") + "." if venue else ""
    elif venue:
        s += f" {venue}"
        if p.get("volume"):
            s += f", {p['volume']}" + (f"({p['issue']})" if p.get("issue") else "")
        if p.get("pages"):
            s += f", {_pages(p['pages'])}"
        s += "."
    link = _doi_url(p)
    if link:
        s += f" {link}"
    return s


_TRANSLIT = str.maketrans({"ı": "i", "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss",
                           "æ": "ae", "Æ": "AE", "œ": "oe", "þ": "th"})


def bibtex_key(p):
    last = (p.get("authors") or [{"last": "anon"}])[0]["last"] or "anon"
    word = next((w for w in re.findall(r"[A-Za-z]+", p.get("title", "")) if len(w) > 3 and w.lower() not in
                 {"with", "from", "that", "this", "using", "based", "into", "over", "under", "their"}), "paper")
    raw = f"{last}{p.get('year', '')}{word}".translate(_TRANSLIT)
    return re.sub(r"[^A-Za-z0-9]", "", unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()).lower()


def _bib_escape(s):
    return re.sub(r"([&%#_])", r"\\\1", s or "")


def _bibtex(p, n=None):
    typ = {"journalArticle": "article", "conferencePaper": "inproceedings", "preprint": "misc", "book": "book",
           "bookSection": "incollection", "thesis": "phdthesis", "report": "techreport"}.get(p.get("item_type"), "misc")
    authors = " and ".join(f"{a['last']}, {a['first']}".strip(", ") for a in p.get("authors", []))
    venue_field = {"article": "journal", "inproceedings": "booktitle", "incollection": "booktitle"}.get(typ, "howpublished")
    fields = [("author", authors), ("title", "{" + p.get("title", "") + "}"), (venue_field, p.get("venue", "")),
              ("year", p.get("year", "")), ("volume", p.get("volume", "")), ("number", p.get("issue", "")),
              ("pages", (p.get("pages") or "").replace("–", "--").replace("-", "--").replace("----", "--")),
              ("publisher", p.get("publisher", "") if typ in ("book", "incollection", "inproceedings") else ""),
              ("doi", p.get("doi", "")), ("url", p.get("url", "") if not p.get("doi") else "")]
    body = ",\n".join(f"  {k} = {{{_bib_escape(v) if k not in ('title', 'url', 'doi') else v}}}" for k, v in fields if v)
    return f"@{typ}{{{bibtex_key(p)},\n{body}\n}}"


def reference(p, style, n=None):
    return {"IEEE": _ieee, "Harvard": _harvard, "APA 7": _apa, "BibTeX": _bibtex}[style](p, n)


def in_text(p, style, page=None, n=None):
    """In-text citation, e.g. (Smith et al., 2021, p. 5) or [3]."""
    if style == "IEEE":
        return f"[{n}]" if n else "[#]"
    if style == "BibTeX":
        return f"\\cite{{{bibtex_key(p)}}}"
    lasts = [a["last"] for a in p.get("authors", []) if a.get("last")] or [p.get("venue") or "Anon."]
    amp = " & " if style == "APA 7" else " and "
    who = lasts[0] if len(lasts) == 1 else (lasts[0] + amp + lasts[1] if len(lasts) == 2 else lasts[0] + " et al.")
    year = p.get("year") or ("n.d." if style == "APA 7" else "no date")
    return f"({who}, {year}" + (f", p. {page}" if page else "") + ")"


def reference_list(papers, style):
    """Full list: IEEE numbered in the order given; Harvard/APA alphabetical; BibTeX entries."""
    if style == "IEEE":
        return "\n".join(reference(p, style, i + 1) for i, p in enumerate(papers))
    if style == "BibTeX":
        return "\n\n".join(reference(p, style) for p in papers)
    key = lambda p: ((p.get("authors") or [{"last": p.get("title", "")}])[0]["last"].lower(), p.get("year", ""))
    return "\n\n".join(reference(p, style) for p in sorted(papers, key=key))
