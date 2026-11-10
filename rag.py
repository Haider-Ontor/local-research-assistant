"""Search (semantic + keyword) over the index, and answer questions with the local model."""
import os, re, json, time, sqlite3, subprocess, difflib
from pathlib import Path
import numpy as np
import requests
from openai import OpenAI
import config

STOP = set("""a an the of in on for to and or with by from at as is are was were be been being this that these those it its
what which who whom how why when where does do did can could should would will shall may might about into than then there
their they them we our you your i me my he she his her not no yes any all some such more most other paper papers study
studies show shows shown use used using based between""".split())


def page_label(paper, page):
    off = paper.get("first_printed_page")
    return f"p. {off + page - 1}" if off else f"PDF p. {page}"


def printed_page(paper, page):
    off = paper.get("first_printed_page")
    return off + page - 1 if off else page


def short_authors(p):
    lasts = [a["last"] for a in p.get("authors", []) if a.get("last")]
    if not lasts:
        return ""
    return lasts[0] if len(lasts) == 1 else (f"{lasts[0]} & {lasts[1]}" if len(lasts) == 2 else f"{lasts[0]} et al.")


class Index:
    def __init__(self, db_path=config.DB_PATH):
        self.db_path = str(db_path)
        self.papers, self.hidden = {}, {}
        self.ids = np.zeros(0, dtype=np.int64)
        self.E = np.zeros((0, 768), dtype=np.float32)
        if not Path(db_path).exists():
            return
        con = sqlite3.connect(self.db_path)
        for (pid, path, source, zkey, zatt, meta, n_pages, fpp, dup, status) in con.execute(
                "SELECT id, path, source, zotero_key, zotero_att_key, meta, n_pages, first_printed_page, dup_of, status FROM papers"):
            rec = json.loads(meta or "{}")
            rec.update(id=pid, path=path, source=source, zotero_key=zkey, zotero_att_key=zatt, n_pages=n_pages,
                       first_printed_page=fpp, status=status)
            (self.hidden if dup is not None else self.papers)[pid] = rec
        rows = con.execute("SELECT id, paper_id, page, is_ref, emb FROM chunks ORDER BY id").fetchall()
        con.close()
        if rows:
            self.ids = np.array([r[0] for r in rows], dtype=np.int64)
            self.pids = np.array([r[1] for r in rows], dtype=np.int64)
            self.pages = np.array([r[2] for r in rows], dtype=np.int32)
            self.is_ref = np.array([r[3] for r in rows], dtype=bool)
            self.E = np.vstack([np.frombuffer(r[4], dtype=np.float32) for r in rows])

    def __len__(self):
        return len(self.ids)

    def _mask(self, allowed, include_refs):
        m = np.ones(len(self.ids), dtype=bool)
        if not include_refs:
            m &= ~self.is_ref
        if allowed is not None:
            m &= np.isin(self.pids, np.fromiter(allowed, dtype=np.int64))
        return m

    def search(self, query, k=6, allowed=None, include_refs=False, per_paper=3, use_vectors=True):
        """Hybrid retrieval: embedding similarity + BM25 keywords, merged by reciprocal-rank fusion."""
        if not len(self.ids) or not query.strip():
            return []
        mask = self._mask(allowed, include_refs)
        if not mask.any():
            return []
        scores = {}
        if use_vectors:
            try:
                qv = embed_query(query)
                sims = self.E @ qv
                sims[~mask] = -np.inf
                top = np.argsort(-sims)[:60]
                for rank, i in enumerate(t for t in top if np.isfinite(sims[t])):
                    scores[int(self.ids[i])] = scores.get(int(self.ids[i]), 0) + 1 / (60 + rank)
            except Exception:
                pass
        terms = [t for t in re.findall(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]", query.lower()) if t not in STOP]
        if terms:
            fts = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
            con = sqlite3.connect(self.db_path)
            try:
                rows = con.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 1.0, 0.4) LIMIT 400",
                                   (fts,)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            con.close()
            pos = np.searchsorted(self.ids, [r[0] for r in rows]) if rows else []
            rank = 0
            for (cid,), i in zip(rows, pos):
                if i < len(self.ids) and self.ids[i] == cid and mask[i]:
                    scores[cid] = scores.get(cid, 0) + 1 / (60 + rank)
                    rank += 1
                    if rank >= 60:
                        break
        ranked = sorted(scores, key=scores.get, reverse=True)
        out, per = [], {}
        for cid in ranked:
            i = int(np.searchsorted(self.ids, cid))
            pid = int(self.pids[i])
            if per.get(pid, 0) >= per_paper:
                continue
            per[pid] = per.get(pid, 0) + 1
            out.append({"chunk_id": cid, "paper_id": pid, "page": int(self.pages[i]), "score": scores[cid]})
            if len(out) >= k:
                break
        if out:
            con = sqlite3.connect(self.db_path)
            texts = dict(con.execute(f"SELECT id, text FROM chunks WHERE id IN ({','.join('?' * len(out))})",
                                     [h["chunk_id"] for h in out]).fetchall())
            con.close()
            for h in out:
                h["text"] = texts.get(h["chunk_id"], "")
        return out

    def chunks_of(self, pid, include_refs=False):
        con = sqlite3.connect(self.db_path)
        rows = con.execute("SELECT page, is_ref, text FROM chunks WHERE paper_id=? ORDER BY id", (pid,)).fetchall()
        con.close()
        return [(p, t) for p, r, t in rows if include_refs or not r]

    def related(self, pid, k=5):
        """Papers in the library whose content is closest to this one: [(paper_id, similarity)]."""
        if not len(self.ids):
            return []
        if not hasattr(self, "_cent"):
            ids = np.unique(self.pids)
            rows = []
            for i in ids:
                m = (self.pids == i) & ~self.is_ref
                v = self.E[m if m.any() else self.pids == i].mean(0)
                rows.append(v / (np.linalg.norm(v) + 1e-9))
            self._cent = (ids, np.vstack(rows))
        ids, M = self._cent
        where = np.nonzero(ids == pid)[0]
        if not len(where):
            return []
        sims = M @ M[where[0]]
        order = [int(j) for j in np.argsort(-sims) if ids[j] != pid and int(ids[j]) in self.papers]
        return [(int(ids[j]), float(sims[j])) for j in order[:k]]


# ---------------------------------------------------------------- model calls
_CLIENTS = {}


def client(kind="chat"):
    """Shared connection to the built-in engine ("chat" = Qwen3 answers, "embed" = search model)."""
    if kind not in _CLIENTS:
        url = config.CHAT_URL if kind == "chat" else config.EMBED_URL
        _CLIENTS[kind] = OpenAI(base_url=url, api_key="local", timeout=600)
    return _CLIENTS[kind]


def embed_query(q):
    r = client("embed").embeddings.create(model=config.EMBED_MODEL, input=["search_query: " + q])
    v = np.asarray(r.data[0].embedding, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def server_status():
    import services
    up = services.api_up()
    return up, [config.CHAT_MODEL, config.EMBED_MODEL] if up else []


def gpu_problem():
    """Returns a message if Windows reports the NVIDIA GPU as not working, else None."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_PnPEntity | ? { $_.Name -like '*NVIDIA GeForce*' } | "
                              "% { \"$($_.Name)|$($_.ConfigManagerErrorCode)\" }"],
                             capture_output=True, text=True, timeout=15, creationflags=0x08000000).stdout.strip()
        for line in out.splitlines():
            name, code = line.rsplit("|", 1)
            if code.strip() not in ("0", ""):
                return f"{name} isn't working (Windows error code {code.strip()}), so the model runs on the CPU."
    except Exception:
        pass
    return None


SYSTEM = """You are a meticulous research assistant for an engineering researcher. You answer ONLY from the numbered excerpts of the researcher's own papers given below.
Rules:
- After every factual statement, cite the excerpt number(s) in square brackets, e.g. [2] or [1][4].
- Report numbers, units, frequencies, materials, sample sizes and methods exactly as written in the excerpts. Never work out numbers yourself (e.g. per-group counts from a total) or assume groups are equal.
- Attribute a number only to the method, device, material or study that the excerpt itself ties it to. Excerpts often mention other techniques or cite other work (e.g. "ref. 11 reported ...", "the tetrapolar method is limited to ..."); never transfer those values to the main paper or to a different method, and say "citing earlier work" when a paper is only reporting someone else's result.
- If the excerpts do not contain the answer, say so plainly, then mention which excerpts come closest. Never invent facts, numbers or references.
- Don't stretch: if an excerpt only touches the topic indirectly, say it is only loosely related in a few words instead of claiming it covers the topic.
- When excerpts disagree, point out the disagreement and cite both sides.
- Refer to papers by first author and year (e.g. "Meaney et al., 2016 [3]"), never as "excerpt [3]" alone.
- Be concise. Use short paragraphs or bullet points. Use LaTeX ($...$) for equations."""


SYSTEM_EXPLAIN = """You are a patient research tutor for an engineering researcher. You get numbered excerpts from the researcher's own papers. Help them UNDERSTAND what the papers say, not just find it.

The researcher's work: {RESEARCH}

Write the answer under these four headings:
### Answer
The direct answer in 2-5 sentences, using only the excerpts. Cite the excerpt number after every claim, e.g. [2] or [1][3].
### What this means
Explain it in plain language for someone new to this exact topic: define every technical term, symbol and unit you use; explain any equation term by term; give intuition (a simple physical picture or analogy) where it helps.
### How they got it
What the authors actually did to obtain this - samples, setup, instruments, frequency range, conditions, analysis - from the excerpts, cited.
### Why it matters
Why the result matters, its limits and caveats, and how it could be useful for the researcher's work described above.

Rules:
- Every fact from the papers MUST carry its excerpt number. Explanations from your own general knowledge (definitions, intuition, background physics, implications) carry NO number - that is how the reader tells them apart - so never put an excerpt number on a sentence the excerpt does not support.
- Never present general knowledge as if a paper said it. Never invent numbers, results or references.
- Only report numbers that are written in the excerpts. Never work numbers out yourself (e.g. dividing a total sample count by the number of groups) and never assume groups are equal in size.
- Don't copy equations out of the excerpts (PDF text garbles them). Name the equation or model, say what it relates and define its symbols; use the textbook form from the reference facts only when it helps.
- Attribute a number only to the method, material or study the excerpt ties it to. Excerpts often describe other techniques or cite other work; say "citing earlier work" when a paper only reports someone else's result.
- If the excerpts don't answer the question, say so under Answer, then still explain the closest relevant material.
- Refer to papers by first author and year, e.g. "Smith et al., 2019 [2]".
- Talk to the researcher directly as "you" ("your work", "your measurements"), never "the researcher".
- Use LaTeX ($...$) for symbols and equations."""


REFERENCE_INTRO = ("\n\nReference facts (correct - follow them whenever you explain these topics, and never contradict "
                   "them):\n")


def reference_notes():
    notes = (config.REFERENCE_NOTES or "").strip()
    return REFERENCE_INTRO + notes if notes else ""


def research_context():
    """Who the researcher is and what they work on (RESEARCH_CONTEXT in local_settings.py)."""
    return (config.RESEARCH_CONTEXT or "").strip() or "not described (relate findings to engineering research in general)"


def system_prompt(explain):
    if not explain:
        return SYSTEM
    return SYSTEM_EXPLAIN.replace("{RESEARCH}", research_context()) + reference_notes()


def range_label(paper, pages):
    a, b = pages
    if a == b:
        return page_label(paper, a)
    off = paper.get("first_printed_page")
    return f"pp. {off + a - 1}–{off + b - 1}" if off else f"PDF pp. {a}–{b}"


def build_excerpts(hits, papers):
    blocks = []
    for n, h in enumerate(hits, 1):
        p = papers.get(h["paper_id"], {})
        who = short_authors(p)
        where = range_label(p, h["pages"]) if h.get("pages") else page_label(p, h["page"])
        head = f"[{n}] \"{p.get('title', '')}\" ({who + ', ' if who else ''}{p.get('year', 'n.d.')}), {where}"
        blocks.append(head + "\n" + h["text"])
    return "\n\n".join(blocks)


def _stitch(a, b, max_overlap=80, min_overlap=5):
    """Join two consecutive passages. If b repeats the end of a (older word-window passages overlap), the repeated
    words are dropped; otherwise they're joined as separate paragraphs. Line breaks (tables) are kept."""
    aw, bw = a.split(), b.split()
    for k in range(min(max_overlap, len(aw), len(bw)), min_overlap - 1, -1):
        if aw[-k:] == bw[:k]:
            m = re.match(r"\s*(?:\S+\s+){%d}" % k, b + " ")
            return a + " " + (b + " ")[m.end():].strip()
    return a.rstrip() + "\n\n" + b.lstrip()


def expand_hits(hits, window=1, db_path=config.DB_PATH):
    """Wider context: add the passages either side of each match, merging matches that touch.
    Each result keeps the matched passage(s) in 'cores' (for highlighting) and the match's page in 'page'."""
    if not hits:
        return hits
    groups = []
    for h in hits:
        lo, hi = h["chunk_id"] - window, h["chunk_id"] + window
        for g in groups:
            if g["paper_id"] == h["paper_id"] and lo <= g["hi"] + 1 and hi >= g["lo"] - 1:
                g["lo"], g["hi"] = min(g["lo"], lo), max(g["hi"], hi)
                g["cores"].append(h)
                break
        else:
            groups.append({"paper_id": h["paper_id"], "lo": lo, "hi": hi, "cores": [h]})
    con = sqlite3.connect(str(db_path))
    out = []
    for g in groups:
        best = g["cores"][0]
        hit_ids = {c["chunk_id"] for c in g["cores"]}
        rows = [r for r in con.execute("SELECT id, page, is_ref, text FROM chunks WHERE paper_id=? AND id BETWEEN ? AND ? "
                                       "ORDER BY id", (g["paper_id"], g["lo"], g["hi"])).fetchall()
                if r[0] in hit_ids or not r[2]]
        text = ""
        for r in rows:
            text = _stitch(text, r[3]) if text else r[3]
        pages = [r[1] for r in rows] or [best["page"]]
        out.append({"chunk_id": best["chunk_id"], "paper_id": g["paper_id"], "page": best["page"], "score": best["score"],
                    "pages": (min(pages), max(pages)), "text": text or best["text"], "core": best["text"],
                    "cores": [(c["page"], c["text"]) for c in g["cores"]]})
    con.close()
    return out


def _visible(full):
    out = re.sub(r"<think>.*?</think>", "", full, flags=re.S)
    i = out.find("<think>")
    if i != -1:
        out = out[:i]
    for n in range(min(7, len(out)), 0, -1):
        if "<think>".startswith(out[-n:]):
            out = out[:-n]
            break
    return out.lstrip()


def stream_chat(messages, think=False, max_tokens=config.MAX_ANSWER_TOKENS, stats=None):
    """Yields visible answer text; reasoning (if any) and speed are recorded in `stats`."""
    stats = stats if stats is not None else {}
    stats.update(reasoning="", tokens=0, first=None, start=time.time())
    # thinking is switched on/off through the chat template (works for Qwen3 and Qwen3.5); the engine caps how long
    # the model may think (REASONING_BUDGET). Low temperature keeps numbers and wording close to the papers.
    resp = client().chat.completions.create(model=config.CHAT_MODEL, messages=messages, stream=True,
                                            temperature=0.6 if think else 0.3, top_p=0.95 if think else 0.8,
                                            max_tokens=max_tokens + (config.REASONING_BUDGET + 200 if think else 0),
                                            extra_body={"chat_template_kwargs": {"enable_thinking": bool(think)},
                                                        "top_k": 20})
    full, shown = "", ""
    for ch in resp:
        if not ch.choices:
            continue
        d = ch.choices[0].delta
        extra = d.model_extra or {}
        rc = getattr(d, "reasoning_content", None) or extra.get("reasoning_content") or extra.get("reasoning")
        if rc:
            stats["reasoning"] += rc
            stats["tokens"] += 1
        if d.content:
            stats["tokens"] += 1
            full += d.content
            vis = _visible(full)
            if stats["first"] is None and vis:
                stats["first"] = time.time() - stats["start"]
            if vis.startswith(shown) and len(vis) > len(shown):
                yield vis[len(shown):]
                shown = vis
    m = re.findall(r"<think>(.*?)(?:</think>|$)", full, flags=re.S)
    if m:
        stats["reasoning"] += "\n".join(m)
    stats["elapsed"] = time.time() - stats["start"]


def fit_hits(hits, papers, history_chars, budget_tokens, explain=False):
    """Drop the lowest-ranked excerpts if the prompt would overflow the model's context."""
    est = lambda s: len(s) / 3.2
    sp = system_prompt(explain)
    while hits and est(sp) + est(build_excerpts(hits, papers)) + history_chars / 3.2 + 200 > budget_tokens:
        hits = hits[:-1]
    return hits


def answer_messages(question, hits, papers, history, explain=False):
    msgs = [{"role": "system", "content": system_prompt(explain)}]
    for m in history[-4:]:
        msgs.append({"role": m["role"], "content": m["content"][:700]})
    msgs.append({"role": "user", "content": f"Excerpts from my papers:\n\n{build_excerpts(hits, papers)}\n\nQuestion: {question}"})
    return msgs


def parse_citations(text, n_hits):
    found = []
    for grp in re.findall(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]", text):
        for part in re.split(r"\s*[,;]\s*", grp):
            if re.match(r"^\d+\s*[–-]\s*\d+$", part):
                a, b = map(int, re.split(r"\s*[–-]\s*", part))
                rng = range(a, b + 1) if b - a < 20 else []
            else:
                rng = [int(part)]
            for x in rng:
                if 1 <= x <= n_hits and x not in found:
                    found.append(x)
    return found


# ---------------------------------------------------------------- study guides
GUIDE_PROMPT = """You are a patient research tutor. Write a study guide that helps an engineering researcher properly UNDERSTAND the paper below. The paper's text is given with page labels such as (p. 4).

The researcher's work: {RESEARCH}

Use exactly these eight headings, in this order, and no others:
## In one paragraph
## The problem and why it matters
## Key concepts
(This section is the glossary - do not add a separate glossary section.) Every technical term, quantity and acronym the paper relies on, as "- **term**: plain-language definition" in one or two lines, with units or "dimensionless".
## Method, step by step
A numbered list of what the authors actually did: samples, setup, instruments, frequency range, conditions, analysis.
## Key equations and models
For each equation or model the paper uses: its name, what it relates, every symbol defined with its unit (or "dimensionless"), what it tells you, and the page where it appears so the reader can look at it. Do NOT copy equations out of the text - PDF text extraction garbles them. You may give the textbook form from the reference facts when the paper uses a standard model, introduced with "Standard form:". If the paper uses no equations, say so.
## Results
The main findings with exact numbers, units and conditions.
## Limitations and open questions
## How this could help your work
Only for the parts of the researcher's work this paper is genuinely relevant to; skip the rest. Write to the researcher as "you".

Rules:
- Put the page label after every fact taken from the paper, e.g. (p. 4) - in every section, including "In one paragraph".
- Explanations from your own general knowledge (definitions, intuition, physics) carry NO page label - that is how the reader tells them apart - so never put a page label on something the paper doesn't say.
- Keep every number with the exact quantity and unit the paper gives it (e.g. a conductivity in S/m is not eps''; a median is not a mean). Only report numbers the paper states; never work numbers out yourself or assume groups are equal in size.
- Never invent results, numbers or claims about the paper. If the paper is only reporting someone else's result, say so."""


def guide_system():
    return (GUIDE_PROMPT.replace("{RESEARCH}", research_context())
            + reference_notes())

NOTES_PROMPT = """You are taking detailed study notes on one part of a research paper. The text has page labels such as (p. 4).
Cover everything important in this part: aims, definitions of terms and symbols, method details (samples, setup, instruments, conditions), each equation or model used (its name, what it relates, its symbols and its page - do not copy the equation itself, PDF text garbles it), every numeric result with the exact quantity name, unit and conditions the paper gives (e.g. "conductivity 0.45e-2 to 1.6e-2 S/m"), what figures and tables show, and limitations. Keep the page label after each note. Write notes only, no introduction, at most 450 words."""

GUIDE_DIRECT_CHARS = 26000      # papers up to ~8k tokens are read in one go (leaves room for careful thinking)
NOTES_PART_CHARS = 22000        # longer papers are read in parts, then the guide is written from the notes


def paper_pages_text(paper, chunks):
    """The paper's body as ['(p. N) text', ...], one entry per page, passage overlaps removed."""
    pages, cur_pg, cur = [], None, ""
    for pg, t in chunks:
        if pg != cur_pg:
            if cur:
                pages.append((cur_pg, cur))
            cur_pg, cur = pg, t
        else:
            cur = _stitch(cur, t)
    if cur:
        pages.append((cur_pg, cur))
    return [f"({page_label(paper, pg)}) {t}" for pg, t in pages]


def guide_plan(paper, chunks):
    """('direct', head, [full_text]) for normal papers, ('notes', head, [part, ...]) for long ones."""
    names = ", ".join(f"{a.get('first', '')} {a.get('last', '')}".strip() for a in paper.get("authors", [])[:8])
    head = (f"Title: {paper.get('title', '')}\nAuthors: {names}\nYear: {paper.get('year', '')}\n"
            f"Published in: {paper.get('venue', '')}\n" + (f"Abstract: {paper['abstract']}\n" if paper.get("abstract") else ""))
    pages = paper_pages_text(paper, chunks)
    full = "\n\n".join(pages)
    if len(full) <= GUIDE_DIRECT_CHARS:
        return "direct", head, [full]
    parts, cur = [], ""
    for b in pages:
        if cur and len(cur) + len(b) > NOTES_PART_CHARS:
            parts.append(cur)
            cur = ""
        cur += b + "\n\n"
    if cur:
        parts.append(cur)
    return "notes", head, parts


def notes_messages(head, part, i, n):
    return [{"role": "system", "content": NOTES_PROMPT},
            {"role": "user", "content": f"{head}\nPart {i} of {n} of the paper:\n\n{part}"}]


def guide_messages(head, body, from_notes=False):
    what = "Detailed notes taken from the whole paper, in order" if from_notes else "Full text of the paper"
    return [{"role": "system", "content": guide_system()},
            {"role": "user", "content": f"{head}\n{what}:\n\n{body}"}]


def complete(messages, max_tokens=900):
    """Non-streamed answer (used for study-guide notes)."""
    return "".join(stream_chat(messages, False, max_tokens=max_tokens))


# ---------------------------------------------------------------- automatic number check
_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_NUM = re.compile(r"(?<![A-Za-z_\d.{^])\d+(?:,\d{3})*(?:\.\d+)?(?!\d)")    # skips names like S11 and sub/superscripts
_CITE = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")
_MATH = re.compile(r"\$\$.*?\$\$|\$[^$\n]+\$", re.S)
_PAGE = re.compile(r"\((?:PDF\s+)?pp?\.\s*(\d+)(?:\s*[–-]\s*(\d+))?\)")


def _val(tok):
    try:
        return round(float(tok.replace(",", "")), 6)
    except ValueError:
        return None


def numbers_in(text):
    return {v for v in (_val(m.group(0)) for m in _NUM.finditer((text or "").translate(_SUP))) if v is not None}


def _segments(text):
    """(offset, sentence) pieces: each line, split at sentence ends."""
    for line in re.finditer(r"[^\n]+", text):
        body, start = line.group(0), 0
        for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z*\-])", body):     # "(p. 4)" / "[2]" after a full stop stay with it
            yield line.start() + start, body[start:m.start()]
            start = m.end()
        yield line.start() + start, body[start:]


def check_numbers(text, sources_for, others=None):
    """Compare every number in a sentence that cites a source with the numbers in that source's text.
    sources_for(sentence) -> list of source texts (or [] when the sentence cites nothing).
    others: optional [(label, text)] of all sources, to tell 'cited the wrong source' apart from 'not found anywhere'.
    Returns ([(start, end, [labels where it was found instead]), ...], number of numbers checked)."""
    t = (text or "").translate(_SUP)
    skip = [m.span() for r in (_CITE, _PAGE) for m in r.finditer(t)]      # numbers in equations ARE checked
    other_nums = [(lab, numbers_in(s)) for lab, s in (others or [])]
    bad, checked = [], 0
    for off, seg in _segments(t):
        srcs = sources_for(seg)
        if not srcs:
            continue
        have = set().union(*(numbers_in(s) for s in srcs))
        for m in _NUM.finditer(seg):
            a, b = off + m.start(), off + m.end()
            if any(x <= a and b <= y for x, y in skip):
                continue
            v = _val(m.group(0))
            if v is None or (v.is_integer() and 1900 <= v <= 2099):      # years are names, not results
                continue
            checked += 1
            if v not in have:
                bad.append((a, b, [lab for lab, nums in other_nums if v in nums]))
    return bad, checked


def answer_sources_for(hits):
    def f(seg):
        out = []
        for grp in _CITE.findall(seg):
            for n in parse_citations(f"[{grp}]", len(hits)):
                out.append(hits[n - 1].get("text", ""))
        return out
    return f


def guide_sources_for(paper, chunks_all):
    """For study guides: a sentence's '(p. N)' labels -> the text of those PDF pages (and their neighbours)."""
    by_page = {}
    for pg, t in chunks_all:
        by_page.setdefault(pg, []).append(t)
    off = paper.get("first_printed_page")

    def f(seg):
        out = []
        for m in _PAGE.finditer(seg):
            a = int(m.group(1))
            b = int(m.group(2) or a)
            pdf = "PDF" in m.group(0)
            for n in range(a, min(b, a + 5) + 1):
                p = n if pdf or not off else n - off + 1
                for q in (p - 1, p, p + 1):
                    out.extend(by_page.get(q, []))
        return out
    return f


# ---------------------------------------------------------------- source page images
_TOK = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")


def passage_rects(page, passages):
    """Rectangles (one per text line) covering where the given passages appear on the page.
    Page words and passage text are cut into the same tokens, so Markdown symbols and table pipes don't matter."""
    import pymupdf
    words = page.get_text("words")
    if not words:
        return []
    seq, owner = [], []
    for i, w in enumerate(words):
        for tok in _TOK.findall(w[4].lower()):
            seq.append(tok)
            owner.append(i)
    hit = set()
    for text in passages:
        tw = _TOK.findall((text or "").lower())
        if not tw:
            continue
        sm = difflib.SequenceMatcher(None, seq, tw, autojunk=False)
        blocks = [(a, size) for a, b, size in sm.get_matching_blocks() if size >= 5]
        pos = sorted(i for a, size in blocks for i in range(a, a + size))
        for x, y in zip(pos, pos[1:]):                       # bridge small gaps (hyphenated words, symbols)
            if 1 < y - x <= 4:
                pos.extend(range(x + 1, y))
        hit.update(owner[i] for i in pos)
    lines = {}
    for i in sorted(hit):
        x0, y0, x1, y1, _, b, l, _ = words[i]
        r = pymupdf.Rect(x0, y0, x1, y1)
        lines[(b, l)] = lines[(b, l)] | r if (b, l) in lines else r
    return list(lines.values())


def page_images(path, page_no, passages=(), zoom=2.0):
    """(full page PNG, close-up PNG around the passage or None), with the passage highlighted."""
    import pymupdf
    with pymupdf.open(path) as doc:
        if not 1 <= page_no <= len(doc):
            return None, None
        page = doc[page_no - 1]
        rects = passage_rects(page, passages)
        for r in rects:
            a = page.add_highlight_annot(r)
            if a:
                a.set_colors(stroke=(1, 0.82, 0.2))
                a.update()
        m = pymupdf.Matrix(zoom, zoom)
        full = page.get_pixmap(matrix=m, annots=True).tobytes("png")
        crop = None
        if rects:
            # close-up of where the passage starts: same column as its first line, at most ~45% of the page tall
            pr, r0 = page.rect, rects[0]
            mid = (pr.x0 + pr.x1) / 2
            left = (r0.x0 + r0.x1) / 2 < mid
            same = [r for r in rects if ((r.x0 + r.x1) / 2 < mid) == left
                    and r0.y0 - 2 <= r.y0 <= r0.y0 + 0.45 * pr.height]
            y1 = max(r.y1 for r in same)
            clip = pymupdf.Rect(pr.x0, max(pr.y0, r0.y0 - 60), pr.x1, min(pr.y1, y1 + 60))
            if clip.height < 0.85 * pr.height:
                crop = page.get_pixmap(matrix=m, clip=clip, annots=True).tobytes("png")
    return full, crop


# ---------------------------------------------------------------- opening PDFs
EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)), None)


def open_pdf(paper, page=1):
    if paper.get("zotero_att_key") and config.OPEN_ZOTERO_PDFS_IN == "zotero":
        os.startfile(f"zotero://open-pdf/library/items/{paper['zotero_att_key']}?page={page}")
    elif EDGE:
        subprocess.Popen([EDGE, Path(paper["path"]).as_uri() + f"#page={page}"])
    else:
        os.startfile(paper["path"])


def show_in_zotero(paper):
    if paper.get("zotero_key"):
        os.startfile(f"zotero://select/library/items/{paper['zotero_key']}")
