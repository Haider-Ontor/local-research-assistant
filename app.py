"""Research Assistant - chat with your papers, understand them, find passages instantly, cite in one click."""
import html, json, os, re, sqlite3, threading, time
import pandas as pd
import streamlit as st
import config, citations, figures, indexer, models, rag, services
from rag import page_label, printed_page, short_authors

st.set_page_config(page_title="Research Assistant", page_icon="📚", layout="wide")
st.markdown("""<style>
mark{background:#ffe08a;color:inherit;padding:0 2px;border-radius:3px}
.snip{font-size:0.92rem;line-height:1.45;margin:0 0 .35rem 0;opacity:.92}
.pg{font-size:.8rem;font-weight:600;opacity:.7;margin-right:.4rem}
.bgtag{font-size:.68rem;font-weight:600;letter-spacing:.02em;padding:1px 6px;border-radius:8px;margin-left:3px;
       background:rgba(128,128,128,.18);color:inherit;opacity:.75;white-space:nowrap;vertical-align:1px}
div[data-testid="stCodeBlock"] pre{white-space:pre-wrap}
.unv{text-decoration:underline wavy #e07b00;text-underline-offset:3px;text-decoration-thickness:1.5px;cursor:help}
.unv2{text-decoration:underline dotted #2f6fdf;text-underline-offset:3px;text-decoration-thickness:2px;cursor:help}
.eye{text-decoration:underline dotted #8a8a8a;text-underline-offset:3px;text-decoration-thickness:2px;cursor:help}
.figcap{font-size:.82rem;line-height:1.35;opacity:.85;margin:.2rem 0 .4rem 0}
div[data-testid="stImage"] img{background:#fff}
</style>""", unsafe_allow_html=True)
UNV_TIP = "Not found as written in the cited source - check it on the page (it may be a unit conversion or a slip)"
WRONG_SRC_TIP = "Not in the source this sentence cites, but it is in source {} - the citation number is probably off"

EXPLAIN_LEGEND = ("ℹ️ Sentences with a number like [2] come from your papers - tick *Show the page* under that source "
                  "to see it. Sentences without one are the model's own explanation (general knowledge): a helpful guide, "
                  "but check anything you'll rely on. *reference sheet* = taken from the correct-facts list in config.py.")
GUIDE_LEGEND = ("Facts from the paper carry page numbers like (p. 4) - look at the page below to check them. Text without "
                "a page number is the model's own explanation (general knowledge).")
FIG_LEGEND = ("Values the AI reads off an image are approximate. Explanations without numbers from the paper are the "
              "model's own (general knowledge) - check anything you'll rely on against the figure and the page.")
EYE_TIP = "Approximate - read off the figure by eye (or estimated by the AI). Check it on the figure."
FIG_UNV_TIP = "Not written anywhere in this paper - read off the image, or a slip. Check it on the figure."
NO_VISION = ("The current AI model can't look at images. Pick a model marked 👁 (with vision switched on) under "
             "**🧠 AI model** in the sidebar.")

SOURCE_KEYS = {"Zotero": "zotero", "Research Papers folder": "folder"}

ANSWER_STYLES = {                     # tuned for Qwen 3.5 9B: explains well without thinking, so Explain starts fast
    "📘 Explain (starts in ~2 s, ~30 s)": dict(explain=True, think=False),
    "🔬 Careful - thinks first (~45 s)": dict(explain=False, think=True),
    "⚡ Quick answer (~10 s)": dict(explain=False, think=False),
}


# ---------------------------------------------------------------- lifecycle
def shutdown():
    """Stop the AI engine and this app (start.py then closes its window)."""
    for pid in services.engine_pids():
        try:
            os.kill(pid, 9)
        except OSError:
            pass
    services.stop_all()
    threading.Timer(1.5, lambda: os._exit(0)).start()


@st.cache_resource
def idle_watchdog():
    """Quit by itself AUTO_QUIT_MINUTES after the last browser tab is closed, so nothing lingers on the GPU."""
    def loop():
        from streamlit.runtime import Runtime
        idle_since = None
        while True:
            time.sleep(30)
            try:
                n = Runtime.instance()._session_mgr.num_active_sessions()
            except Exception:
                continue
            if n:
                idle_since = None
            elif idle_since is None:
                idle_since = time.time()
            elif time.time() - idle_since > config.AUTO_QUIT_MINUTES * 60:
                shutdown()
                return
    threading.Thread(target=loop, daemon=True).start()
    return True


idle_watchdog()


@st.cache_data(max_entries=48, show_spinner=False)
def page_png(path, page, passages):
    return rag.page_images(path, page, list(passages))


def render_answer(text, unverified=(), fig=False):
    """Model text -> markdown. Numbers that weren't found in the cited source are underlined in orange.
    fig=True (figure explanations): grey dotted = read off the image by eye, orange = not written in the paper."""
    text = text or ""
    maths = [m.span() for m in rag._MATH.finditer(text)]
    edits = {}                                   # position -> (end, replacement); applied from the end
    for item in unverified:
        a, b = item[0], item[1]
        elsewhere = item[2] if len(item) > 2 else []
        if fig:
            cls, tip = ("eye", EYE_TIP) if elsewhere == "eye" else ("unv", FIG_UNV_TIP)
        else:
            cls, tip = ("unv2", WRONG_SRC_TIP.format(", ".join(f"[{x}]" for x in elsewhere))) if elsewhere else ("unv", UNV_TIP)
        box = next(((x, y) for x, y in maths if x <= a and b <= y), None)
        if box:                                  # can't underline inside an equation: flag it right after
            edits[box[1]] = (box[1], f" <span class='{cls}' title='{tip}'>⚠ check numbers</span>")
        else:
            edits[a] = (b, f"<span class='{cls}' title='{tip}'>{text[a:b]}</span>")
    for a in sorted(edits, reverse=True):
        b, rep = edits[a]
        text = text[:a] + rep + text[b:]
    text = re.sub(r"\[\s*reference facts?\s*\]", "<span class='bgtag'>reference sheet</span>", text, flags=re.I)
    return re.sub(r"\s*\[\s*bg\s*\]\.?", " <span class='bgtag'>background</span>", text, flags=re.I)


def fig_note(bad, checked, n_eye):
    if not checked:
        return FIG_LEGEND
    n_none = len(bad) - n_eye
    parts = [f"🔎 {checked} numbers checked against the whole paper: {checked - len(bad)} are written in it "
             "(caption, table or text)"]
    if n_eye:
        parts.append(f"{n_eye} read off the figure by eye (dotted grey - approximate)")
    if n_none:
        parts.append(f"{n_none} not written anywhere in the paper (orange) - check them on the figure")
    return "; ".join(parts) + ". " + FIG_LEGEND


def check_note(bad, checked, what="the cited passages"):
    if not checked:
        return ""
    if not bad:
        return f"🔎 All {checked} numbers checked against {what} were found there."
    moved = sum(1 for x in bad if len(x) > 2 and x[2])
    missing = len(bad) - moved
    parts = []
    if missing:
        parts.append(f"{missing} not found in any source - underlined in orange")
    if moved:
        parts.append(f"{moved} found in a different source than the one cited - dotted blue (hover to see which; "
                     "cite that one's page)")
    return f"🔎 {checked} numbers checked against {what}: " + "; ".join(parts) + "."


# ---------------------------------------------------------------- data
def index_stamp():
    if not config.DB_PATH.exists():
        return None
    con = sqlite3.connect(config.DB_PATH)
    try:
        return con.execute("SELECT (SELECT count(*) FROM chunks), (SELECT coalesce(max(id),0) FROM chunks), "
                           "(SELECT count(*) FROM papers), (SELECT coalesce(max(updated_at),0) FROM papers)").fetchone()
    finally:
        con.close()


@st.cache_resource(show_spinner="Loading your library…", max_entries=1)
def get_index(stamp):
    return rag.Index(config.DB_PATH)


@st.cache_data(ttl=10, show_spinner=False)
def server_ok():
    return rag.server_status()[0]


@st.cache_data(ttl=900, show_spinner=False)
def gpu_problem():
    return rag.gpu_problem()


def load_bib():
    try:
        return json.loads(config.BIB_PATH.read_text("utf-8"))
    except Exception:
        return []


def save_bib():
    config.BIB_PATH.write_text(json.dumps(st.session_state.bib, indent=1), "utf-8")


ss = st.session_state
ss.setdefault("messages", [])
ss.setdefault("bib", load_bib())          # list of file paths, in the order added
ss.setdefault("style", config.DEFAULT_STYLE)
ss.setdefault("focus_papers", [])

@st.cache_resource
def ensure_schema():
    indexer.connect().close()       # adds new tables (e.g. study guides) to an existing index
    return True


ensure_schema()
idx = get_index(index_stamp())
papers = idx.papers
by_path = {p["path"]: p for p in papers.values()}
citations.assign_keys(papers.values())          # one \cite key per paper, unique across the whole library


@st.cache_data(show_spinner=False, max_entries=2)
def all_figures(stamp):
    return figures.load_all()


@st.cache_data(show_spinner=False, max_entries=400)
def fig_png(path, page, rect, dpi):
    return figures.render(path, page, list(rect), dpi)


@st.cache_data(ttl=20, show_spinner=False)
def vision_ok():
    return figures.vision_up()


figs = {pid: items for pid, items in all_figures(figures.stamp()).items() if pid in papers}


@st.cache_data(ttl=600, show_spinner="Looking for AI models…")
def model_list():
    return models.scan()


@st.cache_data(ttl=10, show_spinner=False)
def running_model():
    return models.running()


def sync_with_engine():
    """Follow whatever model the engine is actually running (it may have been started from start.py)."""
    run = running_model()
    if run and run.get("path"):
        if not models.same_file(run["path"], config.CHAT_MODEL_FILE):
            models.apply(run["path"], run["vision"])
        if run.get("ctx"):
            config.CONTEXT_LENGTH = int(run["ctx"])
    return run


engine_now = sync_with_engine()


def md(s):
    """Escape text so markdown/LaTeX characters in paper text show literally."""
    return re.sub(r"([\\`*_{}\[\]<>#|~$])", r"\\\1", s or "")


def bib_number(p):
    return ss.bib.index(p["path"]) + 1 if p["path"] in ss.bib else None


# ---------------------------------------------------------------- callbacks
def cb_open(p, page):
    try:
        rag.open_pdf(p, page)
        st.toast(f"Opening {p.get('title', '')[:50]}… at {page_label(p, page)}")
    except Exception as e:
        st.toast(f"Couldn't open the PDF: {e}")


def cb_zotero(p):
    try:
        rag.show_in_zotero(p)
    except Exception as e:
        st.toast(f"Couldn't open Zotero: {e}")


def cb_add_bib(path):
    if path not in ss.bib:
        ss.bib.append(path)
        save_bib()
        st.toast("Added to your bibliography")
    else:
        st.toast("Already in your bibliography")


def cb_remove_bib(path):
    if path in ss.bib:
        ss.bib.remove(path)
        save_bib()


def cb_move_up(path):
    i = ss.bib.index(path)
    if i > 0:
        ss.bib[i - 1], ss.bib[i] = ss.bib[i], ss.bib[i - 1]
        save_bib()


def cb_focus(pid):
    ss.focus_papers = [pid]
    st.toast("Filter set to this paper - ask your question in the Ask tab")


def cb_clear_chat():
    ss.messages = []


def cb_add_cited():
    for m in reversed(ss.messages):
        if m["role"] == "assistant" and m.get("cited"):
            for s in m["sources"]:
                p = papers.get(s["paper_id"])
                if s["n"] in m["cited"] and p and p["path"] not in ss.bib:
                    ss.bib.append(p["path"])
            save_bib()
            return


# ---------------------------------------------------------------- small UI pieces
def latex_block(p, style, page=None, show_cite=True):
    """LaTeX for one paper: the \\cite command, its BibTeX entry, and a \\bibitem for a hand-written bibliography."""
    fl = citations.LATEX[style]
    pg = printed_page(p, page) if page else None
    if show_cite:
        st.caption(f"In your text · {style}")
        if fl == "els-harv":
            st.code(f"{citations.latex_cite(p, style, page=pg)}   % (Author et al., Year)\n"
                    f"\\citet{{{citations.bibtex_key(p)}}}   % Author et al. (Year)", language="latex")
        else:
            st.code(citations.latex_cite(p, style, page=pg), language="latex")
    st.caption(f"BibTeX entry - paste into your references.bib (\\bibliographystyle{{{citations.BST[fl]}}})")
    st.code(citations.bibtex_entry(p, "ieee" if fl == "ieee" else "els"), language="latex", wrap_lines=True)
    st.caption("…or without BibTeX: this \\bibitem goes inside \\begin{thebibliography}")
    st.code(citations.bibitem(p, style), language="latex", wrap_lines=True)


def cite_block(p, page=None):
    style = ss.style
    n = bib_number(p)
    if citations.is_latex(style):
        latex_block(p, style, page)
    else:
        st.caption(f"Reference · {style}")
        st.code(citations.reference(p, style, n), language=None, wrap_lines=True)
        if style != "BibTeX":
            st.caption("In-text" + ("" if style != "IEEE" or n else " (add to bibliography to get its number)"))
            st.code(citations.in_text(p, style, page=printed_page(p, page) if page else None, n=n), language=None)
    if p.get("meta_source") == "file":
        st.warning("Details were guessed from the file. Fix them in the Library tab by pasting the DOI.", icon="⚠️")
    elif p.get("meta_source") == "pdf-doi":
        st.info(f"In Zotero this PDF is attached to “{p.get('zotero_title', '')[:80]}”, but it is a different paper, "
                "so this citation uses the DOI printed on the PDF.", icon="ℹ️")


def highlight(text, query, width=430):
    text = re.sub(r"\s*" + chr(0xFFFD) + "[" + chr(0xFFFD) + r"\s]*", " ", text or "").strip()   # unreadable glyphs
    terms = [t for t in re.findall(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]", query.lower()) if t not in rag.STOP and len(t) > 2]
    low = text.lower()
    hits = [low.find(t) for t in terms if low.find(t) >= 0]
    start = max(0, (min(hits) if hits else 0) - width // 3)
    end = min(len(text), start + width)
    frag = html.escape(text[start:end])
    if terms:
        pat = r"\b(" + "|".join(re.escape(html.escape(t)) for t in sorted(terms, key=len, reverse=True)) + r")\w*"
        frag = re.sub(pat, lambda m: f"<mark>{m.group(0)}</mark>", frag, flags=re.I)
    frag = frag.replace("$", "&#36;")
    return ("… " if start > 0 else "") + frag + (" …" if end < len(text) else "")


def paper_line(p):
    bits = [short_authors(p), p.get("year", ""), p.get("venue", "")]
    return " · ".join(b for b in bits if b)


def page_viewer(p, page, passage, key):
    """Toggle that shows the real PDF page inside the app, with the passage highlighted."""
    if not st.toggle("🔍 Show the page", key=f"pv_{key}",
                     help="See the actual page - figures, tables and equations included - with this passage highlighted"):
        return
    try:
        full, crop = page_png(p["path"], int(page), (passage or "",))
    except Exception as e:
        st.warning(f"Couldn't show this page: {e}")
        return
    if full is None:
        st.warning("That page isn't in the PDF.")
        return
    if crop:
        st.caption(f"{page_label(p, page)} · close-up of the highlighted passage")
        st.image(crop, width="stretch")
        if st.toggle("Whole page", key=f"pvf_{key}"):
            st.image(full, width="stretch")
    else:
        st.caption(f"{page_label(p, page)} · highlighted: the passage used")
        st.image(full, width="stretch")


# ---------------------------------------------------------------- tables and figures
def _fname(p, f):
    who = (p.get("authors") or [{}])[0].get("last", "") or "paper"
    return re.sub(r"[^A-Za-z0-9_-]+", "_", f"{who}_{p.get('year', '')}_{f['label']}").strip("_")


def _unique_cols(head):
    out, seen = [], {}
    for j, c in enumerate(head):
        c = c or f"col {j + 1}"
        seen[c] = seen.get(c, 0) + 1
        out.append(c if seen[c] == 1 else f"{c} ({seen[c]})")
    return out


def figure_thumb(p, f, key, query="", show_paper=False):
    """Small card: picture, label, start of the caption, and a button that opens the full view."""
    with st.container(border=True):
        icon = "📊" if f["kind"] == "table" else "🖼"
        head = f"{icon} **{html.escape(f['label'])}** <span class='pg'>· {page_label(p, f['page'])}</span>"
        if f.get("explained"):
            head += " <span class='bgtag'>explained</span>"
        st.markdown(head, unsafe_allow_html=True)
        if show_paper:
            st.caption(f"{(p.get('title') or '')[:90]} · {paper_line(p)[:60]}")
        if f["rect"]:
            st.image(fig_png(p["path"], f["page"], tuple(f["rect"]), 100), width="stretch")
        else:
            st.caption("No picture found next to this caption - open it to see the whole page.")
        cap = f["caption"] or ""
        snippet = highlight(cap, query, 170) if query else html.escape(cap[:170] + ("…" if len(cap) > 170 else ""))
        st.markdown(f"<div class='figcap'>{snippet}</div>", unsafe_allow_html=True)
        if st.button("🔍 Open", key=f"fig_{key}", width="stretch",
                     help="Full size, download, cite, and have the AI explain it"):
            figure_dialog(f["id"])


def _explain_into(box, msgs, max_tokens=1400):
    text = ""
    for piece in rag.stream_chat(msgs, False, max_tokens=max_tokens):
        text += piece
        box.markdown(render_answer(text) + " ▌", unsafe_allow_html=True)
    return text.strip()


@st.dialog("Figure / table", width="large")
def figure_dialog(fid):
    f = figures.get(fid)
    p = papers.get(f["paper_id"]) if f else None
    if not f or not p:
        st.warning("This figure is no longer in your library.")
        return
    kind = f["kind"]
    st.markdown(f"### {'📊' if kind == 'table' else '🖼'} {html.escape(f['label'])} "
                f"<span class='pg'>· {page_label(p, f['page'])}</span>", unsafe_allow_html=True)
    st.caption(f"{p.get('title', '')} · {paper_line(p)}")
    png = fig_png(p["path"], f["page"], tuple(f["rect"]), 170) if f["rect"] else None
    if kind == "table" and f.get("rows"):
        t_img, t_cells = st.tabs(["Image", "Cells"])
        with t_img:
            if png:
                st.image(png, width="stretch")
        with t_cells:
            rows = f["rows"]
            st.dataframe(pd.DataFrame(rows[1:], columns=_unique_cols(rows[0])), hide_index=True, width="stretch")
            if f.get("rows_src") == "ai":
                st.caption("⚠️ These cells were read by the AI from the image - check them against the image.")
    elif png:
        st.image(png, width="stretch")
    else:
        st.info("No picture was found next to this caption, so here is the whole page.")
        full, _ = page_png(p["path"], int(f["page"]), ("",))
        if full:
            st.image(full, width="stretch")
    st.markdown(f"**Caption:** {md(f['caption'])}")

    c1, c2, c3, c4 = st.columns(4)
    if f["rect"]:
        c1.download_button("⬇ PNG (300 dpi)", data=lambda: fig_png(p["path"], f["page"], tuple(f["rect"]), 300),
                           file_name=_fname(p, f) + ".png", mime="image/png", key=f"dlp_{fid}", on_click="ignore",
                           width="stretch")
    if f.get("rows"):
        c2.download_button("⬇ Cells (.csv)", data=figures.csv_text(f["rows"]), file_name=_fname(p, f) + ".csv",
                           mime="text/csv", key=f"dlc_{fid}", on_click="ignore", width="stretch",
                           help="Opens in Excel")
    c3.button("📖 Open the PDF here", key=f"fop_{fid}", on_click=cb_open, args=(p, f["page"]), width="stretch")
    with c4.popover("❝ Cite", width="stretch"):
        n = bib_number(p)
        intext, credit = figures.cite_lines(p, f, ss.style, n)
        st.caption(f"In-text, pointing at this {kind} · {ss.style}")
        st.code(intext, language=None)
        st.caption("Credit line under your own copy of it")
        st.code(credit, language=None, wrap_lines=True)
        if citations.is_latex(ss.style):
            latex_block(p, ss.style, show_cite=False)
        else:
            st.caption("Reference")
            st.code(citations.reference(p, ss.style, n), language=None, wrap_lines=True)
        st.caption("Reusing a published figure in a thesis or paper may need the publisher's permission.")

    st.divider()
    can = vision_ok()
    if not can:
        st.caption(NO_VISION)
    b1, b2 = st.columns(2)
    explain_now = b1.button("🧠 " + ("Explain it again" if f.get("explained") else f"Explain this {kind}"),
                            key=f"fx_{fid}", disabled=not can or not f["rect"], width="stretch",
                            type="secondary" if f.get("explained") else "primary",
                            help="The AI looks at the image, reads the caption and the passages that discuss it, "
                                 "and explains what it shows and what it means (about 10-20 s)")
    read_now = False
    if kind == "table" and f["rect"] and f.get("rows_src") != "pdf":
        read_now = b2.button("📋 Read the cells with AI", key=f"fr_{fid}", disabled=not can, width="stretch",
                             help="This table is printed as an image, so its cells can't be copied from the PDF. "
                                  "The AI reads them from the picture (check the result).")
    chunks = idx.chunks_of(f["paper_id"], include_refs=True)
    vpng = None
    if (explain_now or read_now) and f["rect"]:
        vpng = figures.render(p["path"], f["page"], f["rect"], figures.vision_dpi(f["rect"]))
    if read_now:
        with st.spinner("Reading the table…"):
            try:
                rows = figures.md_table_rows(rag.complete(figures.read_messages(f, vpng), max_tokens=2500))
            except Exception as e:
                rows = None
                st.error(f"The AI couldn't read it: {e}")
        if rows:
            figures.save(fid, rows=rows, rows_src="ai")
            all_figures.clear()
            st.rerun(scope="fragment")
        elif rows is None:
            st.warning("The AI didn't return a table it could use - try again.")
    text = f.get("explained") or ""
    if explain_now:
        box = st.empty()
        try:
            text = _explain_into(box, figures.explain_messages(p, f, idx.chunks_of(f["paper_id"]), vpng))
            if text:
                figures.save(fid, explained=text, explained_at=time.time())
                all_figures.clear()
        except Exception as e:
            st.error(f"The local model returned an error: {e}")
        box.empty()
    if text:
        bad, checked, n_eye = figures.check_explanation(text, f, chunks)
        st.markdown(render_answer(text, bad, fig=True), unsafe_allow_html=True)
        st.caption(fig_note(bad, checked, n_eye))

    st.divider()
    qf = st.text_input(f"Ask about this {kind}", key=f"fq_{fid}", disabled=not can or not f["rect"],
                       placeholder="e.g. Which sample shifts the resonance most?  ·  What does the dashed line mean?")
    if qf and can and f["rect"]:
        vpng = vpng or figures.render(p["path"], f["page"], f["rect"], figures.vision_dpi(f["rect"]))
        box = st.empty()
        try:
            ans = _explain_into(box, figures.ask_messages(p, f, idx.chunks_of(f["paper_id"]), vpng, qf),
                                max_tokens=900)
            bad, checked, n_eye = figures.check_explanation(ans, f, chunks)
            box.markdown(render_answer(ans, bad, fig=True), unsafe_allow_html=True)
            st.caption(fig_note(bad, checked, n_eye))
        except Exception as e:
            st.error(f"The local model returned an error: {e}")


def figure_grid(p_of, items, key, query="", show_paper=False, ncols=3):
    for r0 in range(0, len(items), ncols):
        cols = st.columns(ncols)
        for c, f in zip(cols, items[r0:r0 + ncols]):
            with c:
                figure_thumb(p_of(f), f, f"{key}_{f['id']}", query=query, show_paper=show_paper)


def render_sources(m, key):
    sources, cited = m.get("sources", []), m.get("cited", [])
    if not sources:
        return
    order = [s for s in sources if s["n"] in cited] + [s for s in sources if s["n"] not in cited]
    label = f"📑 Sources · {len(cited)} cited" if cited else "📑 Passages the answer was based on"
    with st.expander(label, expanded=bool(cited)):
        for s in order:
            p = papers.get(s["paper_id"])
            if not p:
                continue
            c1, c2, c3, c4 = st.columns([7, 1.3, 1.3, 1.3], vertical_alignment="center")
            mark = "**" if s["n"] in cited else ""
            c1.markdown(f"{mark}[{s['n']}]{mark} {md(p.get('title', ''))}  \n"
                        f"<span class='pg'>{page_label(p, s['page'])}</span>{html.escape(paper_line(p))}",
                        unsafe_allow_html=True)
            c2.button("📖 Open", key=f"o_{key}_{s['n']}", on_click=cb_open, args=(p, s["page"]),
                      help="Open the PDF at this page")
            with c3.popover("❝ Cite"):
                cite_block(p, s["page"])
            c4.button("➕ Bib", key=f"b_{key}_{s['n']}", on_click=cb_add_bib, args=(p["path"],),
                      help="Add to your bibliography")
            core = s.get("core") or s["text"]
            st.markdown(f"<div class='snip'>{highlight(core, m.get('q', ''), 520)}</div>", unsafe_allow_html=True)
            page_viewer(p, s["page"], core, f"{key}_{s['n']}")
            named = figures.find_mentioned(figs.get(s["paper_id"], []), s["text"])
            if named and st.toggle(f"🖼 Show {', '.join(f['label'] for f in named[:4])}" + (" …" if len(named) > 4 else ""),
                                   key=f"fm_{key}_{s['n']}", help="The figures and tables this passage refers to"):
                figure_grid(lambda f: p, named[:6], f"ask_{key}_{s['n']}", ncols=min(3, len(named)))


# ---------------------------------------------------------------- choosing the AI model
def model_picker(up):
    """Sidebar: every local model found, how it would run on this GPU, and a button to switch to it."""
    found = model_list()
    cur = models.find(found, engine_now["path"] if engine_now else config.CHAT_MODEL_FILE)
    with st.expander(f"🧠 AI model · {models.short(cur) if cur else config.CHAT_MODEL}", expanded=False):
        if not found:
            st.warning("No AI models found. Put a GGUF chat model in the models folder (or set MODEL_DIRS in "
                       "local_settings.py), or download one in LM Studio, then press Look again.", icon="🔍")
        else:
            idx_ = found.index(cur) if cur in found else 0
            i = st.selectbox("Answers, study guides and figure explanations use", range(len(found)), index=idx_,
                             format_func=lambda j: models.label(found[j]) + ("  ✓ in use" if found[j] is cur else ""),
                             key="model_pick")
            m = found[i]
            running_vision = bool(engine_now and engine_now.get("vision"))
            vision = st.toggle("👁 Vision (lets it look at figures)", disabled=not m.get("mmproj"),
                               value=bool(m.get("mmproj")) and (running_vision if m is cur else config.VISION),
                               key=f"model_vision_{i}",
                               help="Needs the model's vision file. Turning it off frees GPU memory for answers.")
            p = models.plan(m, vision)
            for line in models.explain(m, p):
                st.caption(line)
            in_use = up and m is cur and running_vision == p["vision"]
            if st.button("✓ In use" if in_use else f"Use {models.short(m)[:40]}", key="model_use", type="primary",
                         disabled=in_use, width="stretch"):
                ok = False
                with st.status(f"Loading {models.short(m)} (up to a minute)…", expanded=True) as status:
                    try:
                        services.switch_chat(m["path"], vision, log=st.write)
                        status.update(label=f"Now using {models.short(m)}", state="complete")
                        ok = True
                    except Exception as e:
                        status.update(label="Couldn't switch", state="error")
                        st.error(str(e))
                running_model.clear()
                server_ok.clear()
                vision_ok.clear()
                if ok:
                    st.rerun()
        if st.button("↻ Look again", key="model_rescan", width="stretch",
                     help="After you download or move a model. Searched: the models folder, LM Studio's model "
                          "folder and MODEL_DIRS in local_settings.py."):
            models.scan(refresh=True)
            model_list.clear()
            st.rerun()
        st.caption("Searching your papers always uses nomic-embed, so your index stays valid whichever model you "
                   "pick. Your choice is remembered for next time.")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("📚 Research Assistant")
    up = server_ok()
    n_papers = len(papers)
    n_figs = sum(len(v) for v in figs.values())
    st.caption(f"{n_papers} papers · {len(idx):,} passages · "
               f"{n_figs:,} tables & figures")
    if up:
        st.success(f"Local AI ready · {models.short({'path': str(config.CHAT_MODEL_FILE)})}", icon="🟢")
    else:
        st.error("The AI engine isn't running. Search still works; answers need the engine.", icon="🔴")
        if st.button("Start the AI engine"):
            with st.spinner("Loading the AI models (about 10 s)…"):
                try:
                    services.ensure_llm(log=lambda *_: None)
                    server_ok.clear()
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    model_picker(up)
    gp = gpu_problem()
    if gp:
        with st.expander("⚠️ Running on CPU (slow)", expanded=False):
            st.write(gp + " Answers take about a minute instead of a few seconds. "
                     "Restart the computer; if it persists, reinstall the latest NVIDIA driver (from nvidia.com or "
                     "your laptop maker's support app). The app will use the GPU automatically once Windows sees it.")

    st.selectbox("Citation style", citations.STYLES, key="style")
    st.divider()
    st.subheader("Search in")
    focus = st.multiselect("Specific papers", options=sorted(papers, key=lambda i: papers[i].get("title", "").lower()),
                           format_func=lambda i: (papers[i].get("title") or "?")[:70], key="focus_papers",
                           placeholder="All papers")
    cols = sorted({c for p in papers.values() for c in p.get("collections", [])})
    sel_cols = st.multiselect("Zotero collections", cols, placeholder="All collections") if cols else []
    sel_src = st.multiselect("Source", list(SOURCE_KEYS), placeholder="All")
    years = sorted({int(p["year"]) for p in papers.values() if str(p.get("year", "")).isdigit()})
    yr = st.slider("Years", years[0], years[-1], (years[0], years[-1])) if len(years) > 1 else None
    include_refs = st.checkbox("Also search reference lists", value=False,
                               help="Normally the bibliography pages at the end of each paper are skipped.")
    st.divider()
    answer_style = st.radio(
        "Answer style", list(ANSWER_STYLES), key="answer_style",
        help="**Explain**: the answer from your papers, then what it means in plain language, how the authors got it, "
             "and why it matters for your work. General knowledge is tagged *background*.  \n"
             "**Careful**: a short answer; the model thinks first, so numbers stay with the right paper and method.  \n"
             "**Quick**: fastest, for simple look-ups. It can slip on details - check the cited passage.")
    explain, think = ANSWER_STYLES[answer_style]["explain"], ANSWER_STYLES[answer_style]["think"]
    k = st.slider("Passages per answer", 3, 10, config.DEFAULT_EXCERPTS,
                  help="Each passage is given with the text around it. More passages = broader answers, a bit slower.")
    st.divider()
    if st.button("🔄 Check for new papers", width="stretch"):
        with st.status("Updating the index…", expanded=True) as status:
            box, bar, lines = st.empty(), st.progress(0.0), []

            def log(msg):
                lines.append(msg)
                box.code("\n".join(lines[-10:]), language=None)

            try:
                res = indexer.update_index(log=log, progress=lambda i, n, name: bar.progress(i / n, text=f"{i}/{n}"))
                status.update(label=f"Done · {res['indexed']} new/changed · {res['total']} papers", state="complete")
            except Exception as e:
                status.update(label=f"Index update failed: {e}", state="error")
        st.rerun()
    if st.button("📂 Open Research Papers folder", width="stretch"):
        config.PAPERS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(config.PAPERS_DIR)
    st.button("🧹 Clear chat", width="stretch", on_click=cb_clear_chat)
    st.divider()
    if st.button("⏻ Quit Research Assistant", width="stretch",
                 help="Stops the app and the AI engine, freeing the GPU. Nothing keeps running in the background."):
        st.success("Closed. The AI engine has stopped - you can close this tab.")
        shutdown()
        st.stop()
    st.caption(f"Also stops by itself {config.AUTO_QUIT_MINUTES} min after you close this tab.")


def allowed_ids():
    ids, filtered = set(papers), False
    if focus:
        ids &= set(focus); filtered = True
    if sel_cols:
        ids = {i for i in ids if set(papers[i].get("collections", [])) & set(sel_cols)}; filtered = True
    if sel_src and len(sel_src) < len(SOURCE_KEYS):
        want = {SOURCE_KEYS[s] for s in sel_src}
        ids = {i for i in ids if papers[i]["source"] in want}; filtered = True
    if yr and (yr[0] > years[0] or yr[1] < years[-1]):
        ids = {i for i in ids if str(papers[i].get("year", "")).isdigit() and yr[0] <= int(papers[i]["year"]) <= yr[1]}
        filtered = True
    return ids if filtered else None


allowed = allowed_ids()
scope_note = "" if allowed is None else f" · searching {len(allowed)} of {n_papers} papers"

if not papers:
    st.info("Your library is still being indexed, or no PDFs were found yet. Click **🔄 Check for new papers** "
            f"in the sidebar once indexing has finished. New PDFs go in: `{config.PAPERS_DIR}`")

tab_ask, tab_find, tab_lib, tab_bib = st.tabs(["💬 Ask", "🔎 Find", "📚 Library", f"🧾 Bibliography ({len(ss.bib)})"])

# ---------------------------------------------------------------- Ask
with tab_ask:
    chat_area = st.container()
    q = st.chat_input("Ask your papers…")
with tab_ask, chat_area:
    if not ss.messages:
        st.markdown("#### Ask anything about your papers")
        st.caption("Facts come from your PDFs, each tied to a paper and page. In Explain mode the model also explains "
                   "terms, physics and why it matters - those sentences carry no citation number. "
                   "Examples: *Why does permittivity differ between calcium oxalate and struvite stones?* · "
                   "*How does an open-ended coaxial probe measure permittivity?* · "
                   "*Which of my papers used a split-ring resonator, and what did they measure?* · "
                   "*Compare the deep-learning models used for short-term load forecasting.*" + scope_note)
    for mi, m in enumerate(ss.messages):
        with st.chat_message(m["role"]):
            st.markdown(render_answer(m["content"], m.get("unverified", ())) if m["role"] == "assistant" else m["content"],
                        unsafe_allow_html=m["role"] == "assistant")
            if m["role"] == "assistant":
                if m.get("checked"):
                    st.caption(check_note(m.get("unverified", []), m["checked"]))
                if m.get("explain"):
                    st.caption(EXPLAIN_LEGEND)
                if m.get("meta"):
                    st.caption(m["meta"])
                if m.get("reasoning"):
                    with st.expander("🧠 Model's reasoning"):
                        st.text(m["reasoning"])
                render_sources(m, mi)

    if q:
        hist = [{"role": m["role"], "content": m["content"]} for m in ss.messages]
        with st.chat_message("user"):
            st.markdown(q)
        ss.messages.append({"role": "user", "content": q})
        last_q = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
        rq = f"{last_q} {q}" if last_q and len(q.split()) < 8 else q
        single = allowed is not None and len(allowed) == 1
        hits = idx.search(rq, k=k, allowed=allowed, include_refs=include_refs, per_paper=k if single else 3,
                          use_vectors=up)
        if config.WIDE_CONTEXT:
            hits = rag.expand_hits(hits)
        answer_tokens = config.MAX_EXPLAIN_TOKENS if explain else config.MAX_ANSWER_TOKENS
        budget = config.CONTEXT_LENGTH - answer_tokens - (3000 if think else 0) - 300
        hits = rag.fit_hits(hits, papers, sum(min(len(h["content"]), 700) for h in hist[-4:]), budget, explain=explain)
        with st.chat_message("assistant"):
            msg = {"role": "assistant", "content": "", "sources": [], "cited": [], "q": q}
            if not hits:
                msg["content"] = "I couldn't find any passages about that in your papers" + (
                    " (check the filters in the sidebar)." if allowed is not None else ".")
                st.markdown(msg["content"])
            elif not up:
                msg["content"] = ("The AI engine isn't running, so I can't write an answer - but here are the "
                                  "most relevant passages. Use **Start the AI engine** in the sidebar.")
                msg["sources"] = [{**h, "n": i} for i, h in enumerate(hits, 1)]
                st.markdown(msg["content"])
            else:
                stats = {}
                note = st.empty()
                note.caption(f"Reading {len(hits)} passages" + (" and thinking it through…" if think else "…"))
                box, answer = st.empty(), ""
                try:
                    for piece in rag.stream_chat(rag.answer_messages(q, hits, papers, hist, explain=explain), think,
                                                 max_tokens=answer_tokens, stats=stats):
                        if not answer:
                            note.empty()
                        answer += piece
                        box.markdown(render_answer(answer) + " ▌", unsafe_allow_html=True)
                    msg["unverified"], msg["checked"] = rag.check_numbers(
                        answer, rag.answer_sources_for(hits), others=[(n, h["text"]) for n, h in enumerate(hits, 1)])
                    box.markdown(render_answer(answer, msg["unverified"]), unsafe_allow_html=True)
                except Exception as e:
                    answer = f"The local model returned an error: {e}"
                    st.error(answer)
                note.empty()
                msg["content"] = answer or "_(no answer was produced - try again, or pick ⚡ Quick answer)_"
                msg["sources"] = [{**h, "n": i} for i, h in enumerate(hits, 1)]
                msg["explain"] = explain
                msg["cited"] = rag.parse_citations(answer or "", len(hits))
                msg["reasoning"] = stats.get("reasoning", "").strip()
                el = stats.get("elapsed", 0)
                msg["meta"] = f"{len(hits)} passages · {el:.0f} s" + (
                    f" · {stats['tokens'] / max(el - (stats.get('first') or 0), 0.1):.1f} tokens/s" if stats.get("tokens") else "")
            ss.messages.append(msg)
            if msg.get("checked"):
                st.caption(check_note(msg.get("unverified", []), msg["checked"]))
            if msg.get("explain"):
                st.caption(EXPLAIN_LEGEND)
            if msg.get("meta"):
                st.caption(msg["meta"])
            render_sources(msg, len(ss.messages) - 1)

# ---------------------------------------------------------------- Find
with tab_find:
    find_mode = st.segmented_control("Look in", ["📄 Text", "🖼 Tables & figures"], default="📄 Text", key="find_mode",
                                     label_visibility="collapsed") or "📄 Text"
    fig_mode = find_mode.startswith("🖼")
    fq = st.text_input("Search the captions of every table and figure (instant, no AI)" if fig_mode else
                       "Search every page of every paper (instant, no AI)", key="find_q",
                       placeholder="e.g.  Cole-Cole fit   ·   S21 resonance frequency   ·   load forecast error" if fig_mode else
                       "e.g.  open-ended coaxial probe calibration   ·   Debye relaxation   ·   2.45 GHz")
    if fig_mode:
        if fq:
            t0 = time.time()
            found = figures.search(figs, fq, allowed=allowed, k=30)
            st.caption(f"{len(found)} tables and figures · {(time.time() - t0) * 1000:.0f} ms{scope_note} · "
                       "searches captions, text inside the figures, and AI explanations")
            figure_grid(lambda f: papers[f["paper_id"]], found, "find", query=fq, show_paper=True)
        else:
            st.caption(f"{sum(len(v) for v in figs.values()):,} tables and figures in your library. Type a few words from "
                       "a caption - or open a paper in the Library tab to see all of its tables and figures.")
    elif fq:
        t0 = time.time()
        hits = idx.search(fq, k=40, allowed=allowed, include_refs=include_refs, per_paper=4, use_vectors=up)
        groups = {}
        for h in hits:
            groups.setdefault(h["paper_id"], []).append(h)
        st.caption(f"{len(hits)} passages in {len(groups)} papers · {(time.time() - t0) * 1000:.0f} ms{scope_note}")
        for gi, (pid, hs) in enumerate(groups.items()):
            p = papers[pid]
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([7, 1.3, 1.3, 1.3], vertical_alignment="center")
                c1.markdown(f"**{md(p.get('title', ''))}**  \n{md(paper_line(p))}")
                c2.button("📖 Open", key=f"fo_{gi}", on_click=cb_open, args=(p, hs[0]["page"]))
                with c3.popover("❝ Cite"):
                    cite_block(p, hs[0]["page"])
                c4.button("➕ Bib", key=f"fb_{gi}", on_click=cb_add_bib, args=(p["path"],))
                for hi, h in enumerate(hs):
                    a, b = st.columns([11, 1.3], vertical_alignment="center")
                    a.markdown(f"<div class='snip'><span class='pg'>{page_label(p, h['page'])}</span>"
                               f"{highlight(h['text'], fq)}</div>", unsafe_allow_html=True)
                    b.button("↗ Page", key=f"fp_{gi}_{hi}", on_click=cb_open, args=(p, h["page"]),
                             help=f"Open at {page_label(p, h['page'])} in Zotero / your PDF reader")
                    page_viewer(p, h["page"], h["text"], f"find_{h['chunk_id']}")

# ---------------------------------------------------------------- Library
def make_guide(pid, p):
    """Write and save a study guide. Long papers are read in parts (notes first), then the guide is written."""
    mode, head, parts = rag.guide_plan(p, idx.chunks_of(pid))
    t0 = time.time()
    try:
        if mode == "notes":
            notes = []
            with st.status(f"Reading the paper in {len(parts)} parts…", expanded=True) as status:
                for i, part in enumerate(parts, 1):
                    status.update(label=f"Reading part {i} of {len(parts)}…")
                    notes.append(rag.complete(rag.notes_messages(head, part, i, len(parts)), max_tokens=900))
                status.update(label=f"Read all {len(parts)} parts", state="complete", expanded=False)
            msgs = rag.guide_messages(head, "\n\n".join(notes), from_notes=True)
        else:
            msgs = rag.guide_messages(head, parts[0])
        note = st.empty()
        note.caption("Thinking it through, then writing the guide…")
        box, text = st.empty(), ""
        for piece in rag.stream_chat(msgs, True, max_tokens=3500):
            if not text:
                note.empty()
            text += piece
            box.markdown(render_answer(text) + " ▌", unsafe_allow_html=True)
        if not text.strip():
            st.error("The model didn't produce a guide - please try again.")
            return
        con = sqlite3.connect(config.DB_PATH)
        con.execute("INSERT OR REPLACE INTO guides(paper_id, text, created) VALUES(?,?,?)", (pid, text, time.time()))
        con.commit()
        con.close()
        st.toast(f"Study guide saved ({time.time() - t0:.0f} s)")
    except Exception as e:
        st.error(f"Study guide failed: {e}")
        return
    st.rerun()


with tab_lib:
    lf = st.text_input("Filter the library", placeholder="title, author, journal, year, DOI…", key="lib_filter")
    rows = []
    for pid, p in papers.items():
        rows.append({"pid": pid, "Title": p.get("title", ""), "Authors": short_authors(p), "Year": p.get("year", ""),
                     "Venue": p.get("venue", ""), "Collections": ", ".join(p.get("collections", [])),
                     "Source": {"zotero": "Zotero", "folder": "Folder"}.get(p["source"], p["source"]),
                     "Pages": p.get("n_pages") or 0,
                     "Status": {"ok": "", "no-text": "⚠ scanned", "encrypted": "🔒 locked"}.get(p.get("status"), p.get("status")),
                     "DOI": p.get("doi", "")})
    df = pd.DataFrame(rows, columns=["pid", "Title", "Authors", "Year", "Venue", "Collections", "Source", "Pages", "Status", "DOI"])
    if lf and len(df):
        needle = lf.lower()
        df = df[df.apply(lambda r: needle in " ".join(str(v) for v in r.values).lower(), axis=1)]
    df = df.sort_values(["Year", "Title"], ascending=[False, True]).reset_index(drop=True)
    st.caption(f"{len(df)} of {len(papers)} items · click a row for citations, a study guide and the pages")
    ev = st.dataframe(df, hide_index=True, on_select="rerun", selection_mode="single-row", key="lib_table",
                      width="stretch", height=380,
                      column_config={"pid": None, "Title": st.column_config.TextColumn(width="large"),
                                     "Pages": st.column_config.NumberColumn(width="small")})
    sel = ev.selection.rows if ev and ev.selection else []
    if sel and sel[0] < len(df):
        pid = int(df.iloc[sel[0]]["pid"])
        p = papers[pid]
        st.divider()
        st.markdown(f"### {md(p.get('title', ''))}")
        names = ", ".join(f"{a.get('first', '')} {a.get('last', '')}".strip() for a in p.get("authors", []))
        st.markdown(md(names) or "_Authors unknown_")
        info = [p.get("venue", ""), f"vol. {p['volume']}" if p.get("volume") else "", f"no. {p['issue']}" if p.get("issue") else "",
                f"pp. {p['pages']}" if p.get("pages") else "", p.get("year", "")]
        st.caption(" · ".join(i for i in info if i) + (f" · [doi:{p['doi']}](https://doi.org/{p['doi']})" if p.get("doi") else ""))
        if p.get("meta_source") == "pdf-doi":
            st.warning(f"In Zotero, this PDF is attached to the item “{p.get('zotero_title', '')}”, but the PDF is "
                       "actually the paper above. Citations here use the PDF's own DOI. To tidy up Zotero, drag the PDF "
                       "out of that item and use *Retrieve Metadata for PDF*.", icon="⚠️")
        if p.get("status") == "no-text":
            st.warning("This PDF has no selectable text (it's an image of the pages), so it can't be searched inside. "
                       "Its citation still works. Replacing it with the publisher's PDF fixes this.", icon="⚠️")
        b1, b2, b3, b4 = st.columns(4)
        b1.button("📖 Open PDF", key="lib_open", on_click=cb_open, args=(p, 1), width="stretch")
        if p.get("zotero_key"):
            b2.button("🗂 Show in Zotero", key="lib_z", on_click=cb_zotero, args=(p,), width="stretch")
        b3.button("💬 Ask about this paper", key="lib_focus", on_click=cb_focus, args=(pid,), width="stretch")
        b4.button("➕ Add to bibliography", key="lib_bib", on_click=cb_add_bib, args=(p["path"],), width="stretch")

        left, right = st.columns([1, 1])
        with left:
            st.markdown("**Citations**")
            n = bib_number(p)
            for style in citations.TEXT_STYLES:
                st.caption(style)
                st.code(citations.reference(p, style, n), language=None, wrap_lines=True)
            with st.expander("LaTeX · IEEE and Elsevier templates"):
                for tab, style in zip(st.tabs(["IEEE (IEEEtran)", "Elsevier numbered", "Elsevier author–year"]),
                                      citations.LATEX):
                    with tab:
                        latex_block(p, style)
            if p.get("meta_source") in ("file", "crossref", "arxiv", "manual-doi") and p["source"] != "zotero" or \
                    (p["source"] == "zotero" and not p.get("zotero_key")):
                with st.expander("Wrong details? Fix them with the DOI"):
                    doi = st.text_input("DOI", value=p.get("doi", ""), key=f"doi_{pid}", placeholder="10.1109/…")
                    if st.button("Look up and save", key=f"doisave_{pid}"):
                        res = indexer.set_manual_doi(pid, doi) if doi.strip() else None
                        if res:
                            st.success(f"Updated: {res['title'][:80]}")
                            st.rerun()
                        else:
                            st.error("Crossref didn't recognise that DOI (or you're offline).")
            elif p["source"] == "zotero" and p.get("meta_source") != "pdf-doi":
                st.caption("Details come from Zotero - edit them there and they update here on the next check.")
        with right:
            if p.get("abstract"):
                with st.expander("Abstract", expanded=False):
                    st.write(p["abstract"])
            rel = idx.related(pid, 5)
            if rel:
                st.markdown("**Closest papers in your library**")
                for rid, _ in rel:
                    st.markdown(f"- {md(papers[rid].get('title', ''))}  \n  <span class='pg'>{html.escape(paper_line(papers[rid]))}</span>",
                                unsafe_allow_html=True)

        st.divider()
        st.markdown("### 📘 Study guide")
        con = sqlite3.connect(config.DB_PATH)
        row = con.execute("SELECT text, created FROM guides WHERE paper_id=?", (pid,)).fetchone()
        con.close()
        if row:
            bad, checked = rag.check_numbers(row[0], rag.guide_sources_for(p, idx.chunks_of(pid, include_refs=True)))
            st.caption(f"Saved {time.strftime('%d %b %Y', time.localtime(row[1] or 0))} · {GUIDE_LEGEND}")
            if checked:
                st.caption(check_note(bad, checked, "the pages they cite"))
            st.markdown(render_answer(row[0], bad), unsafe_allow_html=True)
        else:
            st.caption("Helps you properly understand this paper: the problem, the method step by step, the equations "
                       "and models with every symbol explained, results with numbers, limitations, a glossary, and how "
                       "it could help your work. Takes 1-2 minutes and is saved for next time.")
        if st.button("📘 " + ("Rewrite the study guide" if row else "Create a study guide"), key=f"guide_{pid}",
                     disabled=not up or p.get("status") != "ok"):
            make_guide(pid, p)

        st.divider()
        items = figs.get(pid, [])
        nf = sum(f["kind"] == "figure" for f in items)
        st.markdown(f"### 🖼 Tables & figures · {nf} figures, {len(items) - nf} tables")
        if not items:
            st.caption("No captioned tables or figures were found in this PDF. They are looked for when the app starts "
                       "and with *Check for new papers*; use the page viewer below to look at any page.")
        else:
            fc1, fc2 = st.columns([2, 2], vertical_alignment="center")
            show = fc1.segmented_control("Show", ["All", "Figures", "Tables"], default="All", key=f"fk_{pid}",
                                         label_visibility="collapsed") or "All"
            todo = [f for f in items if f["rect"] and not f.get("explained") and "(cont.)" not in f["label"]]
            if todo and fc2.button(f"🧠 Explain all {len(todo)} not yet explained (about {len(todo) * 15 // 60 + 1} min)",
                                   key=f"fxall_{pid}", disabled=not vision_ok(), width="stretch",
                                   help="The AI looks at each one and saves its explanation - open any card to read it."
                                   + ("" if vision_ok() else " " + NO_VISION)):
                chunks_ = idx.chunks_of(pid)
                with st.status(f"Explaining {len(todo)} tables and figures…", expanded=True) as status:
                    for i, f in enumerate(todo, 1):
                        status.update(label=f"Explaining {f['label']} ({i} of {len(todo)})…")
                        try:
                            png = figures.render(p["path"], f["page"], f["rect"], figures.vision_dpi(f["rect"]))
                            text = rag.complete(figures.explain_messages(p, f, chunks_, png), max_tokens=1400).strip()
                            if text:
                                figures.save(f["id"], explained=text, explained_at=time.time())
                                st.write(f"✓ {f['label']}")
                        except Exception as e:
                            st.write(f"✗ {f['label']}: {e}")
                    status.update(label="Done - open any card to read its explanation", state="complete")
                all_figures.clear()
                st.rerun()
            shown = [f for f in items if show == "All" or (show == "Figures") == (f["kind"] == "figure")]
            figure_grid(lambda f: p, shown, f"lib_{pid}")

        st.divider()
        n_pages = max(int(p.get("n_pages") or 1), 1)
        pc1, pc2 = st.columns([1, 3], vertical_alignment="bottom")
        pg = pc1.number_input("Look at page (PDF page number)", 1, n_pages, 1, key=f"lpg_{pid}")
        pc2.caption(f"of {n_pages} · equations, figures and tables are clearest on the page itself")
        if st.toggle("🔍 Show this page", key=f"lpv_{pid}"):
            full, _ = page_png(p["path"], int(pg), ("",))
            if full:
                st.image(full, width="stretch")

# ---------------------------------------------------------------- Bibliography
with tab_bib:
    items = [by_path[pth] for pth in ss.bib if pth in by_path]
    if not items:
        st.info("Click **➕ Bib** next to any source, search result or library entry to collect references here. "
                "Your list is saved between sessions.")
    else:
        st.caption(f"{len(items)} references · {ss.style}" + (" · numbered in the order added; use ⬆ to reorder"
                                                              if citations.numbered(ss.style) else " · sorted alphabetically"))
        if citations.is_latex(ss.style):
            fl = citations.LATEX[ss.style]
            bst = citations.BST[fl]
            st.markdown(f"Use **references.bib** with BibTeX and `\\bibliographystyle{{{bst}}}` (recommended - the "
                        "template formats everything), or paste the ready-made **thebibliography** block instead. "
                        "Pick another template in the sidebar under *Citation style*.")
            cite_all = ("\\citep" if fl == "els-harv" else "\\cite") + "{" + ",".join(citations.bibtex_key(p) for p in items) + "}"
            st.caption("Cite them all at once")
            st.code(cite_all, language="latex", wrap_lines=True)
            t_bib, t_items, t_use = st.tabs(["references.bib", "thebibliography (no BibTeX)", "How to use it"])
            with t_bib:
                bib_text = citations.bib_file(items, ss.style)
                st.code(bib_text, language="latex")
                st.download_button("⬇ references.bib", bib_text, file_name="references.bib", width="stretch")
            with t_items:
                tb = citations.thebibliography(items, ss.style)
                st.code(tb, language="latex", wrap_lines=True)
                st.download_button("⬇ references.tex (\\input{references})", tb, file_name="references.tex",
                                   width="stretch")
            with t_use:
                st.caption("With references.bib (recommended)")
                st.code(citations.usage(ss.style), language="latex")
                st.caption("With the thebibliography block")
                st.code(citations.usage(ss.style, bibtex=False), language="latex")
                st.caption("Titles keep their capitalisation from Zotero (they are protected with braces, so the "
                           "template can't lower-case acronyms like RF, LSTM or pH). Accents and Greek letters are "
                           "written as LaTeX commands, so pdfLaTeX, XeLaTeX and Overleaf all accept them.")
            st.button("➕ Add papers cited in last answer", on_click=cb_add_cited)
        else:
            ref_text = citations.reference_list(items, ss.style)
            st.code(ref_text, language=None, wrap_lines=True)
            d1, d2, d3 = st.columns(3)
            d1.download_button("⬇ Reference list (.txt)", ref_text, file_name=f"references_{ss.style.replace(' ', '')}.txt", width="stretch")
            d2.download_button("⬇ BibTeX (.bib)", citations.reference_list(items, "BibTeX"), file_name="references.bib", width="stretch")
            d3.button("➕ Add papers cited in last answer", on_click=cb_add_cited, width="stretch")
        st.divider()
        for i, p in enumerate(items, 1):
            a, b, c = st.columns([10, 1, 1], vertical_alignment="center")
            a.markdown(f"**{i}.** {md(p.get('title', ''))} — {md(paper_line(p))}")
            b.button("⬆", key=f"up_{i}", on_click=cb_move_up, args=(p["path"],), help="Move up", disabled=i == 1)
            c.button("✕", key=f"rm_{i}", on_click=cb_remove_bib, args=(p["path"],), help="Remove")
        if st.button("Clear bibliography"):
            ss.bib = []
            save_bib()
            st.rerun()
