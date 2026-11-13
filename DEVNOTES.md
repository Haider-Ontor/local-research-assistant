# Developer notes (handoff)

State of the project, so work can continue in a new session without the chat history.

## Releases
- v1.0.0 one-click install (uv bootstrap, first-run setup, 🧠 Models tab, hardware-based model suggestions).
- v1.1.0 ✍️ Rewrite tab (rewrite in your voice + AI-sign checker).
- v1.1.1 checker aligned with Wikipedia "Signs of AI writing" (Nov 2026 revision 1385229372).
- next: "📄 Academic article" mode in the Rewrite tab (see below).

## ✍️ Rewrite (writing.py + ui_writing.py)
- Samples: `data/writing_samples.json` (private, gitignored). The owner's 10 IELTS essays are there.
- Style profile written once by the local model: `data/writing_profile.json` (keyed by samples hash).
- Pipeline per chunk (~450 words): "voice" = notes -> write up in voice -> polish pass on flagged sentences
  -> rhythm pass (skipped in academic mode). "light" = rewrite only flagged sentences.
- `drift()` rejects attempts that lose/add numbers, copy samples, drop paragraphs, add "I" opinions,
  describe "the text", add a conclusion, or drop a citation ([3], (Smith et al., 2019)); retries with feedback, then falls back to light, then keeps original.
- `find_tells()` = AI signs; anything present in the user's samples is exempt; weak signs need a neighbour.

## Academic article mode (Nov 2026)
- Toggle `rw_academic` (default on). Sets `writing._MODE["academic"]`, which:
  - replaces the IELTS style profile with `writing.ACADEMIC` rules (formal, cautious, objective, clear,
    citations kept, tenses per Ecarnot et al. 2015, same paragraphs, no added intro/conclusion);
  - drops essay openers/linkers/joins/paragraph-count notes from the style notes;
  - uses at most 2 samples (vocabulary only); removes the "open/close like my samples" instruction;
  - skips the rhythm pass;
  - adds `writing._ACADEMIC` checks to `find_tells` (contractions, essay linkers, "I believe", colloquial/emotive words, "Interestingly", overstated claims).
- Sources: university "academic writing tips" page (formal language, caution, clarity, evidence, balance,
  objectivity, limitations) and Ecarnot F. et al., Eur Geriatr Med 2015;6:573-579 (IMRAD, tenses).

## Ideas not done yet
- Section-aware rewriting (Introduction / Methods / Results / Discussion tense rules per section).
- Let the user add samples of their own academic writing (best fix for "my voice" in articles).

## Testing
- Engine: `services.ensure_llm()`; rewrite: `writing.rewrite(text, samples, profile, "voice", True, stats,
  academic=True)`; the stream contains `MARK`/`RESET`/`POLISH` tokens (see ui_writing._stream_into).
- UI: `streamlit.testing.v1.AppTest.from_file("app.py")`.

## Academic mode, round 2 (2026-11-13)
- ACADEMIC rules now also follow the user's coherence/cohesion, Level 7 and STEMM-writing guides: topic sentence
  first, key terms kept identical (no synonym swapping), varied/balanced transitions, no "As for", lone "On the other
  hand" or reflexive "not only...but also", natural collocations, calm authority (not over-hedged), signposting.
- Output may have 1-2 paragraphs per input paragraph (`_academic_user` rewrites the paragraph instruction).
- Checker (`_ACADEMIC`): added "As for", lone "On the other hand", stacked Moreover/Additionally/Furthermore/Also.
