"""The ✍️ Rewrite tab: rewrite any text in your own voice, and check texts for signs of AI writing."""
from pathlib import Path
import streamlit as st
import writing

ss = st.session_state
MODES = {"In my voice": "voice", "Only the AI-sounding parts": "light"}
CSS = """<style>
.rwtext{font-size:1rem;line-height:1.6;white-space:normal}
.aitell{text-decoration:underline wavy #d9480f;text-underline-offset:3px;text-decoration-thickness:1.5px;cursor:help}
.aitell2{text-decoration:underline dotted #e8a33d;text-underline-offset:3px;text-decoration-thickness:2px;cursor:help}
.rwtext del{background:rgba(250,82,82,.18);text-decoration:line-through;text-decoration-color:rgba(200,40,40,.7)}
.rwtext ins{background:rgba(64,192,87,.22);text-decoration:none}
.rwsnip{font-size:.85rem;opacity:.75}
</style>"""
LEGEND = ("Wavy orange underline: a clear sign of AI writing; dotted: a weaker one (it counts because another sign is "
          "nearby). Hover over it to see why. These are hints, not proof - and nothing you write yourself in your "
          "samples is ever flagged.")


def _words(t):
    return len(writing.words(t))


# ---------------------------------------------------------------- the tab
def rewrite_tab(up):
    st.markdown(CSS, unsafe_allow_html=True)
    samples = writing.load_samples()
    st.markdown("#### Rewrite in your voice")
    st.caption("Paste any text - an AI draft, rough notes, a paragraph that doesn't sound like you - and the local AI "
               "rewrites it the way you write, learned from samples of your own writing. Then it checks the result "
               "for the usual signs of AI writing. Everything stays on this computer.")
    if not samples:
        st.info("First add two or three pieces of your own writing under **📝 Your writing samples** below.", icon="📝")
    src = st.text_area("Text to rewrite", key="rw_src", height=230, placeholder="Paste the text here…")
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    mode = c1.radio("How much to change", list(MODES), key="rw_mode", horizontal=True,
                    help="**In my voice**: rewritten the way you'd write it (sentences may be merged, split or "
                         "reordered).  \n**Only the AI-sounding parts**: keeps everything else word for word.")
    fix = c2.toggle("Correct grammar and spelling", value=True, key="rw_fix",
                    help="On: your voice, without slips. Off: also keeps your habits of grammar and phrasing.")
    b1, b2, _ = st.columns([1, 1, 2])
    go = b1.button("✍️ Rewrite", type="primary", width="stretch", disabled=not (src.strip() and samples and up))
    chk = b2.button("🔍 Check for AI signs", width="stretch", disabled=not src.strip())
    if not up:
        st.caption("Rewriting needs the AI engine (start it in the sidebar). Checking works without it.")
    if chk:
        ss["rw_check"] = src
        ss.pop("rw_result", None)
    if go:
        _run_rewrite(src, MODES[mode], fix, samples)
    _show_result(samples, up)
    _show_check(samples)
    st.divider()
    _samples_section(samples)
    _style_section(samples, up)


def _ensure_profile(samples):
    prof = writing.profile(samples)
    if prof is not None:
        return prof
    with st.status("Learning your style from your samples (once, about a minute)…", expanded=False) as s:
        try:
            prof = writing.learn_profile(samples)
            s.update(label="Learned your style", state="complete")
        except Exception as e:
            s.update(label=f"Couldn't learn your style ({e}) - using the measured habits only", state="error")
            prof = ""
    return prof


def _stream_into(gen):
    box, note, acc, mark = st.empty(), st.empty(), "", 0
    for piece in gen:
        if piece == writing.MARK:
            mark = len(acc)
            continue
        if piece == writing.RESET:            # that part drifted from your text: it's written again
            acc = acc[:mark]
            note.caption("That part didn't stay true to your text - writing it again more carefully…")
            continue
        if piece == writing.POLISH:           # a second pass over what still sounds like AI
            acc = acc[:mark]
            note.caption("Smoothing out what still sounds like AI…")
            continue
        acc += piece
        box.markdown(writing.plain_html(acc + " ▌"), unsafe_allow_html=True)
    box.empty()
    note.empty()
    return acc.strip()


def _problems_note(stats):
    if stats.get("kept"):
        st.warning(f"In {stats['kept']} part{'s' * (stats['kept'] > 1)} the model kept drifting from your text "
                   f"({'; '.join(sorted(set(stats['problems'])))}), so that part is left as it was.", icon="⚠️")
    elif stats.get("fallback"):
        st.caption("The model couldn't rewrite part of it in your voice without drifting "
                   f"({stats['problems'][0]}), so there it only fixed the AI-sounding sentences.")
    elif stats.get("problems"):
        st.caption(f"Written twice: the first try {stats['problems'][0]}, so the app asked again.")


def _run_rewrite(src, mode, fix, samples):
    prof = _ensure_profile(samples)
    stats = {}
    st.caption("Writing…" + (" (a long text goes a few paragraphs at a time)" if _words(src) > writing.CHUNK_WORDS
                             else ""))
    try:
        out = _stream_into(writing.rewrite(src, samples, prof, mode, fix, stats))
    except Exception as e:
        st.error(f"The rewrite stopped: {e}")
        return
    if not out:
        st.error("The model returned nothing - try again.")
        return
    ss["rw_result"] = {"src": src, "out": out, "fix": fix, "secs": stats.get("elapsed"), "rounds": 1,
                       "stats": {k: stats.get(k) for k in ("problems", "kept", "fallback")}}
    ss.pop("rw_check", None)


def _run_revise(r, samples):
    prof = writing.profile(samples) or ""
    stats = {}
    try:
        out = _stream_into(writing.revise(r["out"], samples, prof, r["fix"], stats))
    except Exception as e:
        st.error(f"The revision stopped: {e}")
        return
    if out:
        r.update(prev=r["out"], out=out, secs=stats.get("elapsed"), rounds=r.get("rounds", 1) + 1,
                 stats={k: stats.get(k) for k in ("problems", "kept", "fallback")})


def _style_line(text, samples):
    mine = writing.style_stats([s["text"] for s in samples])
    this = writing.style_stats([text])
    if not (mine and this):
        return ""
    return f"sentences {this['avg']:.0f} words on average (yours: {mine['avg']:.0f})"


def _show_result(samples, up):
    r = ss.get("rw_result")
    if not r:
        return
    if ss.pop("rw_do_revise", False):
        _run_revise(r, samples)
    out = r["out"]
    _problems_note(r.get("stats") or {})
    hits = writing.find_tells(out, samples)
    before = writing.find_tells(r["src"], samples)
    st.markdown("##### Your version")
    with st.container(border=True):
        st.markdown(writing.highlight_html(out, hits), unsafe_allow_html=True)
    bits = [f"AI signs: {len(before)} in the original → {len(hits)} now", _style_line(out, samples),
            f"{_words(out)} words (original {_words(r['src'])})"]
    if r.get("secs"):
        bits.append(f"{r['secs']:.0f} s")
    st.caption(" · ".join(b for b in bits if b))
    if hits:
        st.caption(LEGEND)
    missing, added = writing.number_check(r["src"], out)
    if missing:
        st.warning("These numbers from the original aren't in the rewrite - check nothing was lost: "
                   + ", ".join(missing), icon="🔢")
    if added:
        st.warning("These numbers are new in the rewrite - check they're right: " + ", ".join(added), icon="🔢")
    if not missing and not added and writing.numbers_in_text(r["src"]):
        st.caption(f"🔢 All {len(writing.numbers_in_text(r['src']))} numbers from the original are kept.")
    st.code(out, language=None, wrap_lines=True)
    c1, c2, c3 = st.columns(3)
    c1.download_button("⬇ Download .txt", out, file_name="rewrite.txt", width="stretch")
    if hits and c2.button(f"✍️ Fix the {len(hits)} remaining sign{'s' * (len(hits) > 1)}", width="stretch",
                          disabled=not up, help="Rewrites only the sentences with an underlined part."):
        ss["rw_do_revise"] = True
        st.rerun()
    if c3.button("Clear", width="stretch"):
        ss.pop("rw_result", None)
        st.rerun()
    with st.expander("Show what changed"):
        st.markdown(writing.diff_html(r.get("prev") if r.get("rounds", 1) > 1 and st.toggle(
            "Only the last fix", key="rw_diff_last") else r["src"], out), unsafe_allow_html=True)
    if hits:
        with st.expander(f"What still sounds like AI ({len(hits)})"):
            _hit_table(hits)


def _hit_table(hits):
    for h in hits:
        st.markdown(f"- {'**' + h['label'] + '**' if h['strong'] else h['label']}: “{h['text']}” - {h['advice']}")


def _show_check(samples):
    t = ss.get("rw_check")
    if not t:
        return
    hits = writing.find_tells(t, samples)
    st.markdown("##### Signs of AI writing in your text")
    if not hits:
        st.success("None of the usual signs of AI writing were found.", icon="✅")
        return
    strong = sum(h["strong"] for h in hits)
    st.caption(f"{len(hits)} found ({strong} clear, {len(hits) - strong} weaker) · "
               f"{writing.tell_score(t, hits):.1f} per 100 words. {LEGEND}")
    with st.container(border=True):
        st.markdown(writing.highlight_html(t, hits), unsafe_allow_html=True)
    with st.expander("List"):
        _hit_table(hits)


# ---------------------------------------------------------------- your samples and your style
def _add_samples():
    added, errors = 0, []
    if ss.get("rw_new_text", "").strip():
        try:
            writing.add_sample(ss.get("rw_new_title", ""), ss["rw_new_text"])
            added += 1
        except ValueError as e:
            errors.append(str(e))
    for f in ss.get(f"rw_files_{ss.get('rw_files_n', 0)}") or []:
        try:
            parts = writing.split_samples(writing.read_file(f.name, f.getvalue()))
            for title, text in parts:
                writing.add_sample(title or Path(f.name).stem, text)
                added += 1
        except Exception as e:
            errors.append(f"{f.name}: {e}")
    ss["rw_new_text"], ss["rw_new_title"] = "", ""
    ss["rw_files_n"] = ss.get("rw_files_n", 0) + 1
    ss["rw_added"] = (added, errors)


def _samples_section(samples):
    total = sum(_words(s["text"]) for s in samples)
    with st.expander(f"📝 Your writing samples · {len(samples)} pieces, {total:,} words", expanded=not samples):
        st.caption("Pieces you wrote yourself - the app copies their voice. The more, the closer it gets: aim for "
                   "1,500 words or more, in the kind of writing you'll rewrite (reports, essays, emails…). They're "
                   "kept in this app's data folder and never leave this computer.")
        added = ss.pop("rw_added", None)
        if added:
            if added[0]:
                st.success(f"Added {added[0]} sample{'s' * (added[0] > 1)}.")
            for e in added[1]:
                st.error(e)
        for i, s in enumerate(samples):
            c1, c2 = st.columns([7, 1], vertical_alignment="center")
            snippet = writing._esc(s["text"][:170].replace("\n", " ")) + ("…" if len(s["text"]) > 170 else "")
            c1.markdown(f"**{writing._esc(s['title'])}** · {_words(s['text'])} words  \n"
                        f"<span class='rwsnip'>{snippet}</span>", unsafe_allow_html=True)
            c2.button("Remove", key=f"rw_rm_{i}", on_click=writing.remove_sample, args=(i,), width="stretch")
        st.markdown("**Add a sample**")
        st.text_input("Title (optional)", key="rw_new_title")
        st.text_area("Paste something you wrote yourself", key="rw_new_text", height=140)
        st.file_uploader("…or add files (.txt, .docx, .pdf). A file holding several pieces under headings such as "
                         "\"Essay 1: …\" becomes one sample per piece.", type=["txt", "md", "docx", "pdf"],
                         accept_multiple_files=True, key=f"rw_files_{ss.get('rw_files_n', 0)}")
        st.button("Add", on_click=_add_samples, key="rw_add")


def _style_section(samples, up):
    if not samples:
        return
    with st.expander("🎨 Your style, as the app sees it"):
        mine = writing.style_stats([s["text"] for s in samples])
        if mine:
            st.markdown(writing.style_notes(mine))
        own = writing.own(samples)
        if own["words"]:
            st.caption("Never flagged, because you use them yourself: "
                       + ", ".join(sorted(w.split(" (")[0] for w in own["words"])) + " (and any phrase from your "
                       "samples, such as your sentence openings).")
        prof = writing.profile(samples)
        st.markdown("**In the AI's words**")
        if prof:
            st.markdown(prof)
        else:
            st.caption("Not learned yet - it happens on your first rewrite (about a minute), or now:")
        if st.button("🔄 Learn my style again" if prof else "Learn my style", disabled=not up, key="rw_learn"):
            with st.spinner("Reading your samples…"):
                try:
                    writing.learn_profile(samples)
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
