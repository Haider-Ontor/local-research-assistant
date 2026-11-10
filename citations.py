"""Reference-list and in-text citation formatting: IEEE, Harvard (Cite Them Right), APA 7, BibTeX - and LaTeX for the
IEEE (IEEEtran) and Elsevier (elsarticle: numbered or author-year) templates: \\cite commands, BibTeX entries tuned to
each template's .bst, and ready-made \\bibitem entries for a manual thebibliography."""
import re, unicodedata

TEXT_STYLES = ["IEEE", "Harvard", "APA 7", "BibTeX"]
LATEX = {"LaTeX · IEEE (IEEEtran)": "ieee",
         "LaTeX · Elsevier numbered (elsarticle)": "els-num",
         "LaTeX · Elsevier author–year (elsarticle)": "els-harv"}
STYLES = TEXT_STYLES + list(LATEX)
BST = {"ieee": "IEEEtran", "els-num": "elsarticle-num", "els-harv": "elsarticle-harv"}


def is_latex(style):
    return style in LATEX


def numbered(style):
    """Numbered styles keep the bibliography in the order you add papers; the others sort by author."""
    return style == "IEEE" or LATEX.get(style) in ("ieee", "els-num")


# ---------------------------------------------------------------- shared helpers
def _initials(first, spaced=True):
    out = []
    for part in (first or "").split():
        if "-" in part:
            out.append("-".join(p[0].upper() + "." for p in part.split("-") if p))
            continue
        for piece in part.split("."):
            if not piece:
                continue
            if piece.isalpha() and piece.isupper() and 2 <= len(piece) <= 3:      # "SA" = S. A.
                out.extend(c + "." for c in piece)
            else:
                out.append(piece[0].upper() + ".")
    return (" " if spaced else "").join(out)


def _ieee_name(a):
    ini = _initials(a.get("first"))
    return f"{ini} {a['last']}".strip()


def _harvard_name(a):
    ini = _initials(a.get("first"), spaced=False)
    return f"{a['last']}, {ini}" if ini else a["last"]


def _apa_name(a):
    ini = _initials(a.get("first"))
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


# ---------------------------------------------------------------- plain-text styles
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


# ---------------------------------------------------------------- citation keys
_TRANSLIT = str.maketrans({"ı": "i", "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ß": "ss",
                           "æ": "ae", "Æ": "AE", "œ": "oe", "þ": "th"})
_KEYS = {}


def _base_key(p):
    last = (p.get("authors") or [{"last": "anon"}])[0].get("last") or "anon"
    last = last.split()[-1] if len(last.split()) > 3 else last       # "World Health Organization" -> organization
    word = next((w for w in re.findall(r"[A-Za-z]+", p.get("title", "")) if len(w) > 3 and w.lower() not in
                 {"with", "from", "that", "this", "using", "based", "into", "over", "under", "their"}), "paper")
    raw = f"{last}{p.get('year', '')}{word}".translate(_TRANSLIT)
    return re.sub(r"[^A-Za-z0-9]", "", unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()).lower()


def assign_keys(papers):
    """Give every paper in the library its own citation key (smith2019microwave, then ...a, ...b if two clash).
    Keys are stable: the paper added first keeps the plain key."""
    _KEYS.clear()
    used = {}
    for p in sorted(papers, key=lambda p: (p.get("id") or 0, p.get("path") or "")):
        base = _base_key(p)
        n = used.get(base, 0)
        used[base] = n + 1
        _KEYS[p.get("path") or id(p)] = base if n == 0 else base + "abcdefghijklmnopqrstuvwxyz"[min(n - 1, 25)]


def bibtex_key(p):
    return _KEYS.get(p.get("path") or id(p)) or _base_key(p)


# ---------------------------------------------------------------- LaTeX text
_ACCENT = {0x0300: "`", 0x0301: "'", 0x0302: "^", 0x0303: "~", 0x0304: "=", 0x0306: "u", 0x0307: ".", 0x0308: '"',
           0x030A: "r", 0x030B: "H", 0x030C: "v", 0x0323: "d", 0x0326: "c", 0x0327: "c", 0x0328: "k", 0x0331: "b"}
_LETTERS = {"ı": "{\\i}", "ȷ": "{\\j}", "ø": "{\\o}", "Ø": "{\\O}", "ł": "{\\l}", "Ł": "{\\L}", "ß": "{\\ss}",
            "æ": "{\\ae}", "Æ": "{\\AE}", "œ": "{\\oe}", "Œ": "{\\OE}", "đ": "d", "Đ": "D", "ð": "d", "þ": "th"}
_SPECIAL = {"\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_", "{": "\\{",
            "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}", "<": "\\textless{}",
            ">": "\\textgreater{}", "|": "\\textbar{}", "‘": "`", "’": "'", "‚": ",", "“": "``", "”": "''",
            "„": ",,", "–": "--", "—": "---", "‐": "-", "‑": "-", "…": "\\ldots{}", chr(0xA0): "~",
            "•": "\\textbullet{}", "§": "\\S{}", "©": "\\copyright{}", "®": "\\textregistered{}",
            "™": "\\texttrademark{}", "′": "'", "″": "''"}
_MATH = {"α": "\\alpha", "β": "\\beta", "γ": "\\gamma", "δ": "\\delta", "ε": "\\varepsilon", "ϵ": "\\epsilon",
         "ζ": "\\zeta", "η": "\\eta", "θ": "\\theta", "ι": "\\iota", "κ": "\\kappa", "λ": "\\lambda", "μ": "\\mu",
         "µ": "\\mu", "ν": "\\nu", "ξ": "\\xi", "π": "\\pi", "ρ": "\\rho", "σ": "\\sigma", "ς": "\\varsigma",
         "τ": "\\tau", "υ": "\\upsilon", "φ": "\\phi", "ϕ": "\\phi", "χ": "\\chi", "ψ": "\\psi", "ω": "\\omega",
         "Γ": "\\Gamma", "Δ": "\\Delta", "Θ": "\\Theta", "Λ": "\\Lambda", "Ξ": "\\Xi", "Π": "\\Pi", "Σ": "\\Sigma",
         "Φ": "\\Phi", "Ψ": "\\Psi", "Ω": "\\Omega", chr(0x2126): "\\Omega", "±": "\\pm", "×": "\\times",
         "÷": "\\div", "−": "-", "≤": "\\leq", "≥": "\\geq", "≈": "\\approx", "≠": "\\neq", "∞": "\\infty",
         "→": "\\rightarrow", "←": "\\leftarrow", "↔": "\\leftrightarrow", "°": "^{\\circ}", "·": "\\cdot",
         "∼": "\\sim", "∝": "\\propto", "∂": "\\partial", "√": "\\surd", "∑": "\\sum", "∫": "\\int"}
_SUP = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ", "0123456789+-n"))
_SUB = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-"))
_RUN = re.compile("([" + "".join(_SUP) + "]+)|([" + "".join(_SUB) + "]+)|(.)", re.S)


def _accented(ch):
    d = unicodedata.normalize("NFD", ch)
    base, marks = d[0], d[1:]
    if not marks or not base.isascii() or not all(ord(m) in _ACCENT for m in marks):
        return None
    res = {"i": "\\i", "j": "\\j"}.get(base, base) if base in "ij" else base
    for m in marks:
        res = "\\" + _ACCENT[ord(m)] + "{" + res + "}"
    return "{" + res + "}"


def tex(s):
    """Plain text -> LaTeX: special characters escaped, accents as {\\'e}, Greek and symbols as $\\mu$ (works in
    pdfLaTeX and in BibTeX, which can then sort and case-convert names correctly)."""
    pieces = []                                    # (is_math, text)
    for m in _RUN.finditer(unicodedata.normalize("NFC", s or "")):
        if m.group(1):
            pieces.append((True, "^{" + "".join(_SUP[c] for c in m.group(1)) + "}"))
            continue
        if m.group(2):
            pieces.append((True, "_{" + "".join(_SUB[c] for c in m.group(2)) + "}"))
            continue
        ch = m.group(3)
        if ch in _SPECIAL:
            pieces.append((False, _SPECIAL[ch]))
        elif ch in _MATH:
            pieces.append((True, _MATH[ch]))
        elif ch in _LETTERS:
            pieces.append((False, _LETTERS[ch]))
        elif ch.isascii():
            pieces.append((False, ch if ch.isprintable() else " "))
        elif unicodedata.category(ch).startswith("Z"):
            pieces.append((False, " "))
        else:
            pieces.append((False, _accented(ch) or ch))
    out, math = [], []
    for is_math, t in pieces + [(False, "")]:
        if is_math:
            math.append(t)
            continue
        if math:                                   # one $...$ per run, so "$\alpha$$\beta$" never appears
            out.append("$" + " ".join(math) + "$")
            math = []
        out.append(t)
    return "".join(out)


# ---------------------------------------------------------------- BibTeX entries
_BIBTYPE = {"journalArticle": "article", "conferencePaper": "inproceedings", "preprint": "misc", "book": "book",
            "bookSection": "incollection", "thesis": "phdthesis", "report": "techreport", "patent": "patent"}
_SERVERS = {"techrxiv": "TechRxiv", "arxiv": "arXiv", "biorxiv": "bioRxiv", "medrxiv": "medRxiv", "ssrn": "SSRN",
            "researchsquare": "Research Square", "preprints.org": "Preprints", "chemrxiv": "ChemRxiv"}


def _server(p):
    hay = (p.get("url") or "") + " " + (p.get("doi") or "") + " " + (p.get("venue") or "")
    hay = hay.lower().replace("10.36227", "techrxiv").replace("10.48550", "arxiv").replace("10.1101", "biorxiv")
    return next((v for k, v in _SERVERS.items() if k in hay), p.get("venue") or "Preprint")


def _bib_first(first):
    """Given names as BibTeX needs them to abbreviate correctly: "SA" -> "S. A.", "S.Sujitha" -> "S. Sujitha"."""
    first = re.sub(r"\.(?=\S)", ". ", first or "")
    return " ".join(" ".join(c + "." for c in w) if w.isalpha() and w.isupper() and 2 <= len(w) <= 3 else w
                    for w in first.split())


def _bib_names(p):
    out = []
    for a in p.get("authors", []):
        last, first = (a.get("last") or "").strip(), _bib_first((a.get("first") or "").strip())
        if not last:
            continue
        if not first and len(last.split()) > 1:               # an organisation: keep it as one name
            out.append("{" + tex(last) + "}")
        else:
            out.append(f"{tex(last)}, {tex(first)}".rstrip(", "))
    return " and ".join(out)


def _bib_pages(pg):
    return tex(_pages(pg))                                    # en dash -> --


def _venue(p, abbr_ok=True):
    """Journal name: the abbreviation only when it looks like a real ISO abbreviation (has full stops)."""
    ab = p.get("journal_abbr") or ""
    if abbr_ok and "." in ab and ab != p.get("venue"):
        return ab
    return p.get("venue") or ab


def _proc(venue):
    return venue if not venue or re.match(r"(?i)proc|in:", venue) else "Proc. " + venue


def bibtex_entry(p, flavour="generic"):
    """One BibTeX entry. flavour: 'generic', 'ieee' (IEEEtran.bst) or 'els' (elsarticle-num / -harv .bst).
    Titles are double-braced: every .bst here lower-cases titles, which would turn "LSTM", "pH" or "Bangladesh"
    into "lstm", "ph", "bangladesh" - the braces keep the capitalisation exactly as in Zotero."""
    typ = _BIBTYPE.get(p.get("item_type"), "misc")
    if typ == "patent" and flavour != "ieee":
        typ = "misc"
    pg = _pages(p.get("pages"))
    artno = _article_no(pg)
    venue = _venue(p, abbr_ok=flavour != "generic")
    f = [("author", _bib_names(p)), ("title", "{" + tex(p.get("title", "").rstrip(".")) + "}")]
    if typ == "article":
        f.append(("journal", tex(venue)))
    elif typ in ("inproceedings", "incollection"):
        f.append(("booktitle", tex(_proc(venue) if flavour == "ieee" and typ == "inproceedings" else venue)))
    elif typ == "phdthesis":
        f.append(("school", tex(p.get("publisher") or venue)))
    elif typ == "techreport":
        f.append(("institution", tex(p.get("publisher") or venue or "Unknown")))
    elif typ == "misc" and p.get("item_type") == "preprint":
        f.append(("howpublished", tex(_server(p))))
    elif typ == "misc" and venue:
        f.append(("howpublished", tex(venue)))
    f += [("year", p.get("year", "")), ("volume", tex(p.get("volume", ""))), ("number", tex(p.get("issue", "")))]
    if pg and not (artno and flavour == "ieee"):
        f.append(("pages", _bib_pages(pg)))
    if typ in ("book", "incollection", "inproceedings", "techreport", "misc") or flavour == "generic":
        f.append(("publisher", tex(p.get("publisher", "")) if typ not in ("phdthesis", "techreport") else ""))
    if typ in ("book", "inproceedings", "incollection", "phdthesis", "techreport"):
        f.append(("address", tex(p.get("place", ""))))
    if flavour == "ieee":                                     # IEEEtran.bst has no DOI field: IEEE puts it in a note
        note = ", ".join(x for x in [f"{{Art.}} no. {tex(pg)}" if artno else "",
                                      f"doi: {tex(p['doi'])}" if p.get("doi") else ""] if x)
        f += [("note", note), ("doi", p.get("doi", "")),
              ("url", p.get("url", "") if not p.get("doi") else "")]
    else:
        f += [("doi", p.get("doi", "")), ("url", p.get("url", "") if not p.get("doi") else "")]
    if not p.get("authors"):                                  # lets author-year styles sort and label it
        f.append(("key", tex(" ".join(re.findall(r"\w+", p.get("title", ""))[:3]) or "Anon")))
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in f if v)
    return f"@{typ}{{{bibtex_key(p)},\n{body}\n}}"


def _bibtex(p, n=None):
    return bibtex_entry(p, "generic")


# ---------------------------------------------------------------- \bibitem entries (manual thebibliography)
def _tq(title):
    """IEEE puts the comma inside the quotes: ``Title,'' - but not after ? or !."""
    t = tex(title.rstrip("."))
    return f"``{t}''" if t[-1:] in "?!" else f"``{t},''"


def _url(u):
    return "\\url{" + u.replace("%", "\\%").replace("#", "\\#") + "}"


def _pages_ieee(pg):
    if not pg:
        return ""
    return "Art. no.~" + tex(pg) if _article_no(pg) else ("pp.~" if "–" in pg else "p.~") + tex(pg)


def _bibitem_ieee(p):
    names = []
    for a in p.get("authors", []):
        ini = "~".join(tex(_initials(a.get("first"))).split())
        names.append(f"{ini} {tex(a['last'])}".strip() if a.get("last") else "")
    names = [x for x in names if x]
    if len(names) > 6:
        auth = names[0] + " \\emph{et~al.}"
    elif len(names) > 2:
        auth = ", ".join(names[:-1]) + ", and " + names[-1]
    else:
        auth = " and ".join(names)
    t, year = p.get("item_type"), p.get("year", "")
    venue, pg, place, pub = tex(_venue(p)), _pages(p.get("pages")), tex(p.get("place", "")), tex(p.get("publisher", ""))
    title = p.get("title", "")
    if t == "book":
        head = [f"\\emph{{{tex(title.rstrip('.'))}}}."]
        tail = [(f"{place}: " if place else "") + (pub or ""), year]
    elif t == "thesis":
        head = [_tq(title)]
        tail = ["Ph.D. dissertation", tex(p.get("publisher") or p.get("venue") or ""), place, year]
    elif t == "conferencePaper":
        head = [_tq(title)]
        tail = [f"in \\emph{{{tex(_proc(_venue(p)))}}}" if venue else "", place, year, _pages_ieee(pg)]
    elif t == "preprint":
        head = [_tq(title)]
        tail = [f"\\emph{{{tex(_server(p))}}}", year]
    elif t == "journalArticle" or venue:
        head = [_tq(title)]
        art = _article_no(pg)                         # IEEE: "..., 2019, Art. no. 103366" - pages go before the year
        tail = [f"\\emph{{{venue}}}" if venue else "", f"vol.~{tex(p['volume'])}" if p.get("volume") else "",
                f"no.~{tex(p['issue'])}" if p.get("issue") else "", "" if art else _pages_ieee(pg), year,
                _pages_ieee(pg) if art else ""]
    else:
        head = [_tq(title)]
        tail = [pub, year]
    tail = ", ".join(x for x in tail if x)
    lead = (auth + ", " if auth else "") + " ".join(head)
    s = lead + (" " + tail if tail else "")
    s = s.rstrip(",")
    if p.get("doi"):
        s += f", doi: {tex(p['doi'])}."
    elif p.get("url"):
        s += ". [Online]. Available: " + _url(p["url"])
    else:
        s += "."
    return s.replace(",. ", ". ").replace(",,", ",")


def _els_name(a, harv=False):
    ini = tex(_initials(a.get("first"), spaced=False))
    last = tex(a.get("last") or "")
    if not ini:
        return last
    return f"{last}, {ini}" if harv else f"{ini} {last}"


def _els_link(p):
    if p.get("doi"):
        return " " + _url("https://doi.org/" + p["doi"]) + "."
    if p.get("url"):
        return " " + _url(p["url"]) + "."
    return ""


def _bibitem_els_num(p):
    """Elsevier numbered style, e.g. J. van der Geer, J.A.J. Hanraads, Title, J. Sci. Commun. 163 (2010) 51--59."""
    auth = ", ".join(_els_name(a) for a in p.get("authors", []) if a.get("last"))
    t, year = p.get("item_type"), p.get("year", "")
    title, venue, pg = tex(p.get("title", "").rstrip(".")), tex(_venue(p)), _pages(p.get("pages"))
    place, pub = tex(p.get("place", "")), tex(p.get("publisher", ""))
    lead = (auth + ", " if auth else "")
    if t == "book":
        s = lead + ", ".join(x for x in [title, pub, place, year] if x) + "."
    elif t == "thesis":
        s = lead + ", ".join(x for x in [title, "Ph.D. thesis", tex(p.get("publisher") or p.get("venue") or ""), year] if x) + "."
    elif t == "conferencePaper":
        s = lead + title + ", " + ", ".join(x for x in [f"in: {venue}" if venue else "", pub, place, year,
                                                         f"pp. {tex(pg)}" if pg else ""] if x) + "."
    elif t == "preprint":
        s = lead + f"{title}, {tex(_server(p))} ({year})."
    elif t == "journalArticle" or venue:
        s = lead + f"{title}, {venue}" + (f" {tex(p['volume'])}" if p.get("volume") else "") + \
            (f" ({year})" if year else "") + (f" {tex(pg)}" if pg else "") + "."
    else:
        s = lead + ", ".join(x for x in [title, pub, year] if x) + "."
    return s + _els_link(p)


def _harv_short(p):
    lasts = [tex(a["last"]) for a in p.get("authors", []) if a.get("last")] or \
            [tex(" ".join(re.findall(r"\w+", p.get("title", ""))[:3]) or "Anon.")]
    if len(lasts) == 1:
        return lasts[0]
    if len(lasts) == 2:
        return f"{lasts[0]} and {lasts[1]}"
    return f"{lasts[0]} et~al."


def _bibitem_els_harv(p, year=None):
    """Elsevier author-year (Harvard) style, e.g. Van der Geer, J., Hanraads, J.A.J., 2010. Title. J. Sci. Commun. 163, 51--59."""
    year = year or p.get("year") or "n.d."
    names = [_els_name(a, harv=True) for a in p.get("authors", []) if a.get("last")]
    auth = ", ".join(names) + ", " if names else ""
    t = p.get("item_type")
    title, venue, pg = tex(p.get("title", "").rstrip(".")), tex(_venue(p)), _pages(p.get("pages"))
    place, pub = tex(p.get("place", "")), tex(p.get("publisher", ""))
    s = f"{auth}{year}. {title}."
    if t == "book":
        s += " " + ", ".join(x for x in [pub, place] if x) + "." if (pub or place) else ""
    elif t == "thesis":
        s += " Ph.D. thesis. " + (tex(p.get("publisher") or p.get("venue") or "") + "." if (p.get("publisher") or p.get("venue")) else "")
    elif t == "conferencePaper":
        s = s[:-1] + ", " + ", ".join(x for x in [f"in: {venue}" if venue else "", pub, place,
                                                   f"pp. {tex(pg)}" if pg else ""] if x) + "."
    elif t == "preprint":
        s += f" {tex(_server(p))}."
    elif venue:
        s += f" {venue}" + (f" {tex(p['volume'])}" if p.get("volume") else "") + (f", {tex(pg)}" if pg else "") + "."
    return (s + _els_link(p)).replace("..", ".").strip()


def bibitem(p, style, year=None):
    """The \\bibitem line for one paper (for a thebibliography you write by hand)."""
    fl = LATEX[style]
    key = bibtex_key(p)
    if fl == "ieee":
        return f"\\bibitem{{{key}}} " + _bibitem_ieee(p)
    if fl == "els-num":
        return f"\\bibitem{{{key}}} " + _bibitem_els_num(p)
    y = year or p.get("year") or "n.d."
    return f"\\bibitem[{{{_harv_short(p)}({y})}}]{{{key}}} " + _bibitem_els_harv(p, y)


def latex_cite(p, style, page=None, figure=None):
    """\\cite{key} (numbered) or \\citep{key} (author-year), optionally pointing at a page or a figure."""
    loc = f"Fig.~{figure}" if figure else (f"p.~{page}" if page else "")
    cmd = "\\citep" if LATEX.get(style) == "els-harv" else "\\cite"
    return f"{cmd}[{loc}]{{{bibtex_key(p)}}}" if loc else f"{cmd}{{{bibtex_key(p)}}}"


def bib_file(papers, style):
    """A complete .bib file for the chosen template."""
    fl = "ieee" if LATEX.get(style) == "ieee" else "els" if is_latex(style) else "generic"
    head = f"% references.bib - for \\bibliographystyle{{{BST[LATEX[style]]}}}\n\n" if is_latex(style) else ""
    return head + "\n\n".join(bibtex_entry(p, fl) for p in papers) + "\n"


def _harv_sorted(papers):
    """Alphabetical by first author, with 2019a / 2019b when the same short name and year appear twice."""
    key = lambda p: (unicodedata.normalize("NFKD", (p.get("authors") or [{"last": p.get("title", "")}])[0]
                                           .get("last", "")).encode("ascii", "ignore").decode().lower(), p.get("year", ""),
                     p.get("title", "").lower())
    items = sorted(papers, key=key)
    groups = {}
    for p in items:
        groups.setdefault((_harv_short(p), p.get("year")), []).append(p)
    years = {}
    for (_, y), ps in groups.items():
        for i, p in enumerate(ps):
            years[id(p)] = (y or "n.d.") + ("abcdefghijklmnopqrstuvwxyz"[i] if len(ps) > 1 else "")
    return items, years


def thebibliography(papers, style):
    """A complete thebibliography block (numbered: in the order given; author-year: alphabetical)."""
    fl = LATEX[style]
    pre = ["\\providecommand{\\url}[1]{\\texttt{#1}}"]
    if fl == "els-harv":
        items, years = _harv_sorted(papers)
        body = [bibitem(p, style, years[id(p)]) for p in items]
        return "\n".join(pre + ["\\begin{thebibliography}{" + str(len(items)) + "}"] + body + ["\\end{thebibliography}"])
    width = "9" * len(str(max(len(papers), 1)))
    body = [bibitem(p, style) for p in papers]
    return "\n".join(pre + ["\\begin{thebibliography}{" + width + "}"] + body + ["\\end{thebibliography}"])


PREAMBLE = {"ieee": "\\documentclass[conference]{IEEEtran}   % or [journal]\n\\usepackage[T1]{fontenc}\n"
                    "\\usepackage{cite}\n\\usepackage{url}",
            "els-num": "\\documentclass[preprint,12pt]{elsarticle}\n\\usepackage[T1]{fontenc}\n\\usepackage{hyperref}",
            "els-harv": "\\documentclass[preprint,12pt,authoryear]{elsarticle}\n\\usepackage[T1]{fontenc}\n"
                        "\\usepackage{hyperref}"}


def usage(style, bibtex=True):
    """What to put in the .tex file (bibtex=False: for a pasted thebibliography instead of a .bib file)."""
    fl = LATEX[style]
    cite = ("\\citet{key} showed ...   % Author et al. (2019)\n... as shown before \\citep{key}.   % (Author et al., 2019)"
            if fl == "els-harv" else "... as shown in \\cite{key}.   % [1]")
    end = (f"\\bibliographystyle{{{BST[fl]}}}\n\\bibliography{{references}}   % your references.bib file" if bibtex
           else "% paste the thebibliography block here")
    return f"{PREAMBLE[fl]}\n\\begin{{document}}\n{cite}\n...\n{end}\n\\end{{document}}"


# ---------------------------------------------------------------- the public interface used by the app
def reference(p, style, n=None):
    if is_latex(style):
        return bibitem(p, style)
    return {"IEEE": _ieee, "Harvard": _harvard, "APA 7": _apa, "BibTeX": _bibtex}[style](p, n)


def in_text(p, style, page=None, n=None):
    """In-text citation, e.g. (Smith et al., 2021, p. 5), [3] or \\cite{smith2021sensor}."""
    if is_latex(style):
        return latex_cite(p, style, page=page)
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
    """Full list: IEEE numbered in the order given; Harvard/APA alphabetical; BibTeX entries; LaTeX: thebibliography."""
    if is_latex(style):
        return thebibliography(papers, style)
    if style == "IEEE":
        return "\n".join(reference(p, style, i + 1) for i, p in enumerate(papers))
    if style == "BibTeX":
        return "\n\n".join(reference(p, style) for p in papers)
    key = lambda p: ((p.get("authors") or [{"last": p.get("title", "")}])[0]["last"].lower(), p.get("year", ""))
    return "\n\n".join(reference(p, style) for p in sorted(papers, key=key))
