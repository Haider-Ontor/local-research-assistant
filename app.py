"""Research Assistant - chat with your papers, understand them, find passages instantly, cite in one click."""
import html, json, os, re, sqlite3, threading, time
import pandas as pd
import streamlit as st
import config, citations, indexer, proposal, rag, services
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
</style>""", unsafe_allow_html=True)
UNV_TIP = "Not found as written in the cited source - check it on the page (it may be a unit conversion or a slip)"
WRONG_SRC_TIP = "Not in the source this sentence cites, but it is in source {} - the citation number is probably off"

EXPLAIN_LEGEND = ("ℹ️ Sentences with a number like [2] come from your papers - tick *Show the page* under that source "
                  "to see it. Sentences without one are the model's own explanation (general knowledge): a helpful guide, "
                  "but check anything you'll rely on. *reference sheet* = taken from the correct-facts list in config.py.")
GUIDE_LEGEND = ("Facts from the paper carry page numbers like (p. 4) - look at the page below to check them. Text without "
                "a page number is the model's own explanation (general knowledge).")

SOURCE_KEYS = {"Zotero": "zotero", "Research Papers folder": "folder", "My PhD proposal": "proposal"}

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


def render_answer(text, unverified=()):
    """Model text -> markdown. Numbers that weren't found in the cited source are underlined in orange."""
    text = text or ""
    maths = [m.span() for m in rag._MATH.finditer(text)]
    edits = {}                                   # position -> (end, replacement); applied from the end
    for item in unverified:
        a, b = item[0], item[1]
        elsewhere = item[2] if len(item) > 2 else []
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
    if by_path.get(path, {}).get("source") == "proposal":
        st.toast("That's your own proposal draft - nothing to cite")
        return
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
def cite_block(p, page=None):
    if p.get("source") == "proposal":
        st.info("This is your own PhD proposal draft - nothing to cite.", icon="📝")
        return
    style = ss.style
    n = bib_number(p)
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
            own = "📝 *Your proposal draft:* " if p["source"] == "proposal" else ""
            c1.markdown(f"{mark}[{s['n']}]{mark} {own}{md(p.get('title', ''))}  \n"
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


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("📚 Research Assistant")
    up = server_ok()
    n_papers = sum(1 for p in papers.values() if p["source"] != "proposal")
    has_prop = n_papers < len(papers)
    st.caption(f"{n_papers} papers{' + your PhD proposal' if has_prop else ''} · {len(idx):,} passages indexed")
    if up:
        st.success(f"Local AI ready · {config.CHAT_MODEL}", icon="🟢")
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
    gp = gpu_problem()
    if gp:
        with st.expander("⚠️ Running on CPU (slow)", expanded=False):
            st.write(gp + " Answers take about a minute instead of a few seconds. "
                     "Restart the laptop; if it persists, reinstall the latest NVIDIA driver (GIGABYTE Control "
                     "Center or nvidia.com). The app will use the GPU automatically once Windows sees it.")

    with st.expander("📝 Your PhD proposal - what the AI knows", expanded=False):
        prop_src, prop_brief = proposal.find_file(), proposal.load_brief()
        if not prop_src:
            st.caption(f"No file with 'proposal' in its name found in {config.PROPOSAL_DIR}." if config.PROPOSAL_DIR
                       else "No proposal folder set - add PROPOSAL_DIR to local_settings.py to build your draft in.")
        else:
            st.caption(f"Reading **{prop_src.name}** (the newest file with 'proposal' in its name in that folder). "
                       "The AI keeps the brief below in mind for every answer and study guide, and the full draft is "
                       "searchable as *your draft* (never cited as evidence). A new version is picked up automatically "
                       "when the app starts.")
            if proposal.brief_is_stale():
                st.warning("Your draft has changed since this brief was written - press Re-read below.", icon="🔄")
            if prop_brief:
                nf = prop_brief.get("not_found") or []
                st.caption(f"Brief written {time.strftime('%d %b %Y, %H:%M', time.localtime(prop_brief['created']))} · "
                           f"{prop_brief.get('checked', 0)} numbers checked against your draft · "
                           + (f"not found: {', '.join(nf)}" if nf else "all found"))
                st.markdown(prop_brief["text"].replace("\n", "  \n"))
            cov = proposal.coverage_for_index(idx)
            if cov:
                missing = [(n, ref) for n, ref, pid_ in cov if not pid_]
                st.markdown(f"**Your draft's references:** {len(cov) - len(missing)} of {len(cov)} are in your library.")
                if missing:
                    st.caption("Not in your library yet - add the ones you can to Zotero so the AI can read them:")
                    for n, ref in missing:
                        dm = re.search(r"10\.\d{4,9}/[^\s,;]+", ref)
                        link = f" · [doi](https://doi.org/{dm.group(0).rstrip('.')})" if dm else ""
                        st.markdown(f"- [{n}] {md(ref[:110])}…{link}")
            if st.button("🔄 Re-read my proposal now", width="stretch", disabled=not up,
                         help="Re-reads the newest draft and writes a new brief (about a minute)."):
                ok = False
                with st.spinner("Reading your draft and writing a new brief (about a minute)…"):
                    try:
                        _con = indexer.connect()
                        indexer.sync_proposal(_con, indexer.client(), log=lambda *_: None, force=True)
                        _con.close()
                        proposal.make_brief()
                        ok = True
                    except Exception as e:
                        st.error(f"Couldn't update: {e}")
                if ok:
                    st.rerun()

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
                if up and proposal.brief_is_stale():
                    log("Your proposal changed - writing a new brief for the AI (about a minute)…")
                    proposal.make_brief()
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
                   "*Which of my papers could support objective O2 of my proposal?* · "
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
    fq = st.text_input("Search every page of every paper (instant, no AI)", key="find_q",
                       placeholder="e.g.  open-ended coaxial probe calibration   ·   Debye relaxation   ·   2.45 GHz")
    if fq:
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
                     "Source": {"zotero": "Zotero", "folder": "Folder", "proposal": "📝 My proposal"}.get(p["source"], p["source"]),
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
          if p["source"] == "proposal":
            st.info("This is your own PhD proposal draft. The AI keeps a brief of it in mind (sidebar → 📝 Your PhD "
                    "proposal) and can search the full text. Its study guide below can help you see how it reads.",
                    icon="📝")
          else:
            st.markdown("**Citations**")
            n = bib_number(p)
            for style in citations.STYLES:
                st.caption(style)
                st.code(citations.reference(p, style, n), language=None, wrap_lines=True)
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
        st.caption(f"{len(items)} references · {ss.style}" + (" · numbered in the order added; use ⬆ to reorder" if ss.style == "IEEE" else " · sorted alphabetically"))
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
