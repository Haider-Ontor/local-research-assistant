"""The app's setup page (first start on a new computer) and its 🧠 Models tab: what this computer can run, the model
the app suggests for it, any GGUF model on Hugging Face, and the downloads."""
from pathlib import Path
import streamlit as st
import catalog, config, downloads, firstrun, hardware, hub, indexer, models, services

GB = 2 ** 30
ss = st.session_state
BADGE = {"ok": "✅ fits", "cpu-vision": "✅ fits (vision on the CPU)", "tight": "⚠️ only just fits",
         "too-big": "🐢 too big", "cpu": "💻 runs on the processor"}


def gb(n):
    return f"{n / GB:.1f} GB" if n >= GB / 10 else f"{n / 2 ** 20:.0f} MB"


def count(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n)


def eta(s):
    if s is None:
        return ""
    return f"{s / 3600:.1f} h left" if s > 5400 else f"{s / 60:.0f} min left" if s > 90 else f"{s:.0f} s left"


@st.cache_data(ttl=900, show_spinner=False)
def _options(vision, gpu_key):
    return catalog.options(vision)


def options(vision=True):
    return _options(vision, str(models.gpu()))


# ---------------------------------------------------------------- this computer
def hardware_box(hw):
    g = hw["gpu"]
    if g:
        st.markdown(f"**Graphics card:** {g['name']} · {g['total'] / 1024:.0f} GB")
    else:
        others = ", ".join(f"{x['name']} ({x['total'] / 1024:.1f} GB)" for x in hw["gpus"]) or "none found"
        st.markdown(f"**Graphics card:** none the AI can use (found: {others}) - it runs on the processor, which "
                    "works but answers take longer")
    st.markdown(f"**Memory:** {hw['ram']:.0f} GB · **Processor:** {hw['cpu']}")
    st.caption(f"AI engine for this computer: llama.cpp **{hw['engine']}** build - {hw['engine_why']}.")


def engine_box(hw, key="eng"):
    info = downloads.engine_info()
    tag = catalog.load()["engine"]["tag"]
    if info:
        st.markdown(f"**AI engine:** llama.cpp {info['tag'] if info['tag'] != 'own' else '(installed by hand)'} · "
                    f"{info['build']} build")
    else:
        st.markdown("**AI engine:** not installed")
    builds = list(catalog.load()["engine"]["builds"])
    with st.expander("Install or change the AI engine"):
        b = st.selectbox("Build", builds, index=builds.index(hw["engine"]), key=f"{key}_build",
                         help="cuda-13 / cuda-12: NVIDIA cards (cuda-13 needs a driver from 2025 or later). vulkan: "
                              "AMD, Intel and NVIDIA cards. cpu: no graphics card.")
        size = sum(f["size"] for f in catalog.engine_files(b))
        same = info and info["build"] == b and info["tag"] == tag
        st.caption(f"llama.cpp {tag}, {gb(size)} download." + (" Already installed." if same else "")
                   + (" Suggested for this computer." if b == hw["engine"] else ""))
        if st.button("Reinstall" if same else "Install", key=f"{key}_go", width="stretch",
                     help="The answer engine stops for a moment while the new one is put in place."):
            downloads.install_engine(b)
            st.rerun()


# ---------------------------------------------------------------- downloads
def _job_lines(job):
    if job.status == "done":
        st.markdown(f"✅ {job.title} - done")
        return
    if job.status == "error":
        st.markdown(f"❌ **{job.title}**: {job.error}")
        if st.button("Try again", key=f"retry_{job.id}"):
            downloads.start(job.title, job.files, job.after, job.kind)
            downloads.forget_finished()
            st.rerun()
        return
    if job.status == "cancelled":
        st.markdown(f"⏸ {job.title} - {job.message}")
        if st.button("Continue", key=f"resume_{job.id}"):
            downloads.start(job.title, job.files, job.after, job.kind)
            downloads.forget_finished()
            st.rerun()
        return
    text = f"{job.title} · {gb(job.done)} of {gb(job.total)}"
    if job.speed:
        text += f" · {job.speed / 2 ** 20:.1f} MB/s · {eta(job.eta())}"
    c1, c2 = st.columns([6, 1], vertical_alignment="bottom")
    c1.progress(job.fraction(), text=text)
    c2.button("Stop", key=f"stop_{job.id}", on_click=job.cancel, width="stretch")
    if job.message and not job.message.startswith("Downloading"):
        st.caption(job.message)


@st.fragment(run_every=1.0)
def _live_downloads():
    for job in downloads.jobs():
        _job_lines(job)
    if not downloads.active():
        st.rerun()                      # all finished: refresh the whole page


def downloads_panel():
    js = downloads.jobs()
    if not js:
        return
    st.markdown("#### Downloads")
    if downloads.active():
        st.caption("They carry on while you use the app (keep it open). If the connection drops they pick up again "
                   "where they stopped.")
        _live_downloads()
    else:
        for job in js:
            _job_lines(job)
        st.button("Clear this list", key="dl_clear", on_click=downloads.forget_finished)


def sidebar_note():
    act = downloads.active()
    if act:
        done, total = sum(j.done for j in act), sum(j.total for j in act)
        st.caption(f"⬇️ Downloading {len(act)} item{'s' * (len(act) > 1)}: {done / max(total, 1):.0%} "
                   "(see 🧠 Models)")


def _job_for(title):
    return next((j for j in downloads.jobs() if j.title == title and j.active), None)


# ---------------------------------------------------------------- one model
def _use(path, vision, on_change):
    with st.status(f"Loading {models.short({'path': path})} (up to a minute)…", expanded=True) as status:
        try:
            services.switch_chat(path, vision, log=st.write)
            status.update(label=f"Now using {models.short({'path': path})}", state="complete")
        except Exception as e:
            status.update(label="Couldn't switch", state="error")
            st.error(str(e))
            return
    on_change()
    st.rerun()


def _in_use(path):
    run = models.running()
    return bool(run and run.get("path") and models.same_file(run["path"], path))


def option_card(o, vision, on_change, key, star=False):
    e, v, p = o["e"], o["v"], o["p"]
    size = catalog.download_size(e, v, vision)
    with st.container(border=True):
        head = f"{'⭐ ' if star else ''}**{e['name']}** · {e['maker']} · {v['quant']} · {gb(size)}"
        st.markdown(head + ("  ·  ✔️ tested with this app" if e["tested"] else ""))
        st.caption(e["about"] + f" · licence: {e['license'] or 'see its page'}")
        for line in models.explain(catalog.as_model(o, vision), p)[1:]:
            st.caption(line)
        local = catalog.find_local(e, v, vision)
        title = f"{e['name']} {v['quant']}" + (" + vision" if vision and e["vision"] else "")
        job = _job_for(title)
        if local and _in_use(local[0]):
            st.button("✓ In use", key=f"{key}_inuse", disabled=True, width="stretch")
        elif local:
            if st.button("Use it", key=f"{key}_use", type="primary", width="stretch"):
                _use(local[0], vision, on_change)
        elif job:
            st.caption(f"⬇️ Downloading: {job.fraction():.0%} (see Downloads)")
        elif st.button(f"⬇️ Download ({gb(size)})", key=f"{key}_dl", width="stretch"):
            start_download(e, v, vision)
            st.rerun()


def start_download(e, v, vision, then_use=False):
    files = catalog.download_files(e, v, vision)
    title = f"{e['name']} {v['quant']}" + (" + vision" if vision and e["vision"] else "")

    def after(job):
        models.scan(refresh=True)
    return downloads.start(title, files, after=after)


# ---------------------------------------------------------------- Hugging Face
@st.cache_data(ttl=600, show_spinner="Searching Hugging Face…")
def _search(q, sort):
    return hub.search(q, sort=sort)


@st.cache_data(ttl=600, show_spinner="Reading the model's file list…")
def _repo(repo_id):
    return hub.repo(repo_id)


def _remote_info(repo_id):
    e = catalog.entry(repo_id)
    if e:
        return e["info"]
    return ss.get("remote_info", {}).get(repo_id) or _cached_remote(repo_id)


def _cached_remote(repo_id):
    for k, v in hub._cache().items():
        if k.startswith(repo_id + "/"):
            return v
    return None


def hf_browser(on_change):
    c1, c2 = st.columns([4, 1])
    q = c1.text_input("Search Hugging Face for GGUF models, or paste a link to a model",
                      placeholder="e.g.  qwen3.5   gemma 4   mistral   https://huggingface.co/unsloth/Qwen3.5-4B-GGUF",
                      key="hf_q").strip()
    sort = c2.selectbox("Sort by", ["downloads", "likes", "lastModified", "trendingScore"], key="hf_sort",
                        format_func={"downloads": "Downloads", "likes": "Likes", "lastModified": "Newest",
                                     "trendingScore": "Trending"}.get)
    if not q:
        st.caption("Any GGUF chat model works. Look for repositories by **unsloth**, **bartowski**, **ggml-org** or "
                   "**lmstudio-community** - they publish ready-made versions of most open models.")
        return
    link = hub.parse_link(q) if "/" in q and " " not in q else None
    if link and link.get("url"):
        direct_download(link)
        return
    if link and link.get("repo"):
        repo_panel(link["repo"], link.get("path"), on_change)
        return
    try:
        res = _search(q, sort)
    except Exception as ex:
        st.error(f"Couldn't reach Hugging Face: {ex}")
        return
    if not res:
        st.info("No GGUF models match. Try fewer words, e.g. just the model family.")
        return

    def label(i):
        r = res[i]
        bits = [r["id"]]
        if r["params"]:
            bits.append(f"{r['params'] / 1e9:.1f}B")
        bits += [f"⬇ {count(r['downloads'])}", f"♥ {count(r['likes'])}", r["updated"]]
        return " · ".join(bits) + (" · 🔒 gated" if r["gated"] else "")
    i = st.selectbox(f"{len(res)} repositories", range(len(res)), format_func=label, key=f"hf_pick_{q}_{sort}")
    repo_panel(res[i]["id"], None, on_change)


def repo_panel(repo_id, want_path, on_change):
    try:
        r = _repo(repo_id)
    except Exception as ex:
        code = getattr(getattr(ex, "response", None), "status_code", None)
        if code in (401, 404):
            st.error(f"There's no public model called **{repo_id}** on Hugging Face. Check the name (or the link); "
                     "a private repository needs an access token (🔑 below).")
        else:
            st.error(f"Couldn't open {repo_id} on Hugging Face: {ex}")
        return
    versions, vision_files = hub.group(r["files"])
    if not versions:
        st.warning("This repository has no model files (.gguf).")
        return
    with st.container(border=True):
        bits = [f"**[{repo_id}](https://huggingface.co/{repo_id})**", f"licence: {r['license'] or 'not stated'}"]
        if r["params"]:
            bits.append(f"{r['params'] / 1e9:.1f}B parameters")
        if r["arch"]:
            bits.append(r["arch"])
        st.markdown(" · ".join(bits))
        if r["gated"]:
            st.warning("Gated model: accept its licence on its Hugging Face page (link above), then add an access "
                       "token below (🔑).", icon="🔒")
        k = repo_id.replace("/", "_")
        vision = bool(vision_files) and st.toggle(
            f"👁 With its vision file ({gb(vision_files[0]['size'])}: lets it look at figures)", value=True,
            key=f"{k}_vision")
        vf = vision_files[0] if vision else None
        if not vision_files:
            st.caption("No vision file in this repository: the model can't look at figures.")
        info = _remote_info(repo_id)
        if info and info.get("kind", "chat") != "chat":
            st.warning("This isn't a chat model (it's a " + str(info.get("kind")) + " model), so it can't answer "
                       "questions.")
            return
        if not info:
            st.caption("Fit is a rough guess from the file size until the app reads the model's description "
                       "(about 10 MB).")
            if st.button("🔍 Work out exactly how it runs on this computer", key=f"{k}_probe"):
                q = lambda v: abs(hub.quality(v["quant"])) if hub.quality(v["quant"]) is not None else 99
                probe = min(versions, key=lambda v: (q(v), v["size"]))      # Q4_K_M or the closest to it
                with st.spinner("Reading the model's description from Hugging Face…"):
                    try:
                        info = hub.describe_remote(repo_id, probe, r["files"])
                        ss.setdefault("remote_info", {})[repo_id] = info
                    except Exception as ex:
                        st.error(f"Couldn't read it: {ex}")
        best = hub.best_version(info, versions, vf)[0] if info else next(
            (v for v in versions if hub.quality(v["quant"]) is not None
             and hub.quick_fit(v["size"], vf["size"] if vf else 0) in ("ok", "cpu")), None)
        plans = {v["path"]: models.plan(hub.as_model(info, v, vf), bool(vf)) for v in versions} if info else {}

        def label(j):
            v = versions[j]
            fit = plans[v["path"]]["fit"] if info else hub.quick_fit(v["size"], vf["size"] if vf else 0)
            return (f"{v['quant'] or Path(v['path']).name} · {gb(v['size'])} · {BADGE.get(fit, fit)}"
                    + ("  ⭐ suggested" if best is v else "") + ("" if info else " (rough)"))
        default = next((j for j, v in enumerate(versions) if want_path and v["path"] == want_path),
                       versions.index(best) if best in versions else 0)
        j = st.selectbox("Version (smaller = less memory, a little less accurate)", range(len(versions)),
                         index=default, format_func=label, key=f"{k}_ver")
        v = versions[j]
        if info:
            m = dict(hub.as_model(info, v, vf), quant=v["quant"], source="Hugging Face")
            for line in models.explain(m, plans[v["path"]]):
                st.caption(line)
        folder = hub.download_dir(repo_id)
        local = folder / v["path"]
        vlocal = folder / vf["path"] if vf else None
        title = f"{repo_id.split('/')[1]} {v['quant']}" + (" + vision" if vf else "")
        if local.exists() and (not vlocal or vlocal.exists()):
            if _in_use(str(local)):
                st.button("✓ In use", key=f"{k}_inuse", disabled=True)
            elif st.button("Use it", key=f"{k}_use", type="primary"):
                _use(str(local), bool(vf), on_change)
        elif _job_for(title):
            st.caption("⬇️ Downloading (see Downloads)")
        else:
            size = v["size"] + (vf["size"] if vf else 0)
            if st.button(f"⬇️ Download ({gb(size)})", key=f"{k}_dl", type="primary"):
                files = [{"url": hub.file_url(repo_id, p), "dest": str(folder / p), "size": s, "sha256": sha}
                         for p, s, sha in zip(v["parts"], v["sizes"], v["sha256s"])]
                if vf:
                    files.append({"url": hub.file_url(repo_id, vf["path"]), "dest": str(folder / vf["path"]),
                                  "size": vf["size"], "sha256": vf.get("sha256")})
                downloads.start(title, files, after=lambda job: models.scan(refresh=True))
                st.rerun()
        st.caption(f"Saved in {folder}")


def direct_download(link):
    st.markdown(f"Direct link to **{link['name']}**")
    st.caption("Only download models from sources you trust. Without a published checksum the app can't check the "
               "file arrived intact.")
    dest = Path(config.MODELS_DIR) / "other" / link["name"]
    if dest.exists():
        st.success(f"Already downloaded: {dest} - pick it under 🧠 AI model in the sidebar.")
    elif st.button("⬇️ Download", key="direct_dl"):
        import requests
        try:
            size = int(requests.head(link["url"], allow_redirects=True, timeout=20,
                                     headers={"User-Agent": hub.UA}).headers.get("content-length") or 0) or None
        except Exception:
            size = None
        downloads.start(link["name"], [{"url": link["url"], "dest": str(dest), "size": size, "sha256": None}],
                        after=lambda job: models.scan(refresh=True))
        st.rerun()


def token_box():
    with st.expander("🔑 Hugging Face access token (only for gated models such as Llama)"):
        st.caption("Some models need you to accept their licence on Hugging Face first. Then create a *read* token "
                   "at huggingface.co/settings/tokens and paste it here. It's kept in this app's data folder and only "
                   "sent to Hugging Face.")
        has = bool(hub.token())
        t = st.text_input("Token", type="password", key="hf_token_in",
                          placeholder="hf_… (saved)" if has else "hf_…")
        c1, c2 = st.columns(2)
        if c1.button("Save token", key="hf_token_save", disabled=not t):
            hub.save_token(t)
            st.success("Saved.")
        if has and c2.button("Remove token", key="hf_token_rm"):
            hub.save_token("")
            st.rerun()


# ---------------------------------------------------------------- the 🧠 Models tab
def models_tab(on_change):
    hw = hardware.detect()
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("#### This computer")
        hardware_box(hw)
    with c2:
        st.markdown("#### &nbsp;")
        engine_box(hw)
    downloads_panel()
    st.markdown("#### Suggested for this computer")
    vision = st.toggle("👁 Vision: models that can look at figures and tables", value=True, key="mt_vision",
                       help="Vision needs an extra file and some memory. Off = the suggestions include text-only "
                            "models and leave more room for the text.")
    opts = options(vision)
    fitting = [o for o in opts if o["v"]]
    if not fitting:
        st.warning("None of the suggested models fits this computer's memory.")
    st.caption("Ordered by how well they answer questions about papers, using the best version that runs fully on "
               "this computer. ⭐ is the app's suggestion.")
    cols = st.columns(2)
    for i, o in enumerate(fitting[:6]):
        with cols[i % 2]:
            option_card(o, vision, on_change, key=f"cat{i}", star=i == 0)
    rest = fitting[6:]
    too_big = [o for o in opts if not o["v"]]
    if rest:
        with st.expander(f"{len(rest)} more that fit"):
            cols = st.columns(2)
            for i, o in enumerate(rest):
                with cols[i % 2]:
                    option_card(o, vision, on_change, key=f"rest{i}")
    if too_big:
        st.caption("Too big for this computer: " + ", ".join(
            f"{o['e']['name']} (≥ {gb(o['smallest']['size'])})" for o in too_big))
    st.markdown("#### Any model from Hugging Face")
    hf_browser(on_change)
    token_box()


# ---------------------------------------------------------------- first start
def _browse():
    p = firstrun.browse_folder(ss.get("setup_papers") or config.PAPERS_DIR)
    if p:
        ss["setup_papers"] = p


def first_run_page():
    st.title("📚 Welcome to Research Assistant")
    st.markdown("Chat with your research papers, find passages, explain figures and cite them - with an AI that "
                "runs **entirely on this computer**. Nothing you read or ask leaves it.")
    pend = firstrun.pending()
    if pend:
        if downloads.active():
            st.info(f"Setting up with **{pend['name']}**. The downloads below carry on in the background - keep "
                    "this tab open; the app continues by itself when they're done.", icon="⬇️")
            downloads_panel()
            return
        if firstrun.ready(pend):
            _finish(pend)
            return
        downloads_panel()
        st.warning("Some downloads didn't finish. Press **Try again** above, or choose again.", icon="⚠️")
        if st.button("Choose again"):
            firstrun.clear_pending()
            downloads.forget_finished()
            st.rerun()
        return

    stt = firstrun.status()
    hw = hardware.detect()
    st.subheader("1 · This computer")
    hardware_box(hw)

    st.subheader("2 · Your papers")
    if stt["settings"]:
        st.markdown(f"Papers folder: `{config.PAPERS_DIR}` (from local_settings.py)")
        use_zotero, folder, context = None, None, None
    else:
        ss.setdefault("setup_papers", str(config.PAPERS_DIR))
        c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
        folder = c1.text_input("Folder with your PDFs (subfolders included; it's created if it doesn't exist)",
                               key="setup_papers")
        c2.button("Browse…", on_click=_browse, width="stretch")
        z = firstrun.zotero_dir()
        use_zotero = st.checkbox(f"Also use my Zotero library ({z})", value=True) if z else False
        context = st.text_area("What do you work on? (optional - a few lines; explanations relate findings to it)",
                               key="setup_context", height=80,
                               placeholder="e.g. PhD on perovskite solar cells: thin-film deposition, XRD, PL "
                                           "spectroscopy, device stability.")

    st.subheader("3 · The AI model")
    vision = st.toggle("👁 Able to look at figures and tables (vision)", value=True, key="setup_vision")
    fitting = [o for o in options(vision) if o["v"]][:6]
    local = [m for m in models.scan() if m["kind"] == "chat"]
    choices = [("cat", o) for o in fitting] + [("local", m) for m in local]
    if not choices:
        st.error("No model fits this computer's memory.")
        return

    def label(j):
        kind, x = choices[j]
        if kind == "cat":
            here = catalog.find_local(x["e"], x["v"], vision)
            return (f"{'⭐ Suggested: ' if j == 0 else ''}{x['e']['name']} · {x['v']['quant']} · "
                    + ("already on this computer" if here else f"{gb(catalog.download_size(x['e'], x['v'], vision))} "
                                                               "download") + f" · {BADGE[x['p']['fit']]}")
        return f"Already on this computer: {models.label(x)}"
    j = st.radio("Model", range(len(choices)), format_func=label, key="setup_model", label_visibility="collapsed")
    kind, x = choices[j]
    if kind == "cat":
        st.caption(x["e"]["about"] + f" · {x['e']['maker']} · licence: {x['e']['license']}")
        for line in models.explain(catalog.as_model(x, vision), x["p"])[1:]:
            st.caption(line)
    else:
        for line in models.explain(x, models.plan(x, vision)):
            st.caption(line)
    st.caption("You can switch models or download others any time in the 🧠 Models tab.")

    # what has to be downloaded
    todo = []
    if not stt["engine"]:
        todo.append(("AI engine (llama.cpp, " + hw["engine"] + " build)",
                     sum(f["size"] for f in catalog.engine_files(hw["engine"]))))
    if not stt["embed"]:
        todo.append(("Search model (nomic-embed-text v1.5)", catalog.load()["embed"]["size"]))
    here = catalog.find_local(x["e"], x["v"], vision) if kind == "cat" else (x["path"], None)
    if not here:
        todo.append((f"{x['e']['name']} {x['v']['quant']}" + (" with vision" if vision and x["e"]["vision"] else ""),
                     catalog.download_size(x["e"], x["v"], vision)))
    st.subheader("4 · Download and start")
    if todo:
        total = sum(s for _, s in todo)
        for name, size in todo:
            st.markdown(f"- {name}: {gb(size)}")
        import shutil
        free = shutil.disk_usage(config.APP_DIR).free
        st.caption(f"In total {gb(total)}, saved in {config.APP_DIR} ({gb(free)} free on that drive). From Hugging "
                   "Face and GitHub; every file is checked against its published checksum.")
    if st.button("⬇️ Download and set up" if todo else "Start", type="primary"):
        if not stt["settings"]:
            firstrun.write_settings(folder or config.PAPERS_DIR, use_zotero, context or "")
        if not stt["engine"]:
            downloads.install_engine(hw["engine"])
        if not stt["embed"]:
            downloads.start("Search model", catalog.embed_files(), kind="embed")
        if here:
            path, name = here[0], (x["e"]["name"] if kind == "cat" else models.short(x))
        else:
            start_download(x["e"], x["v"], vision)
            path, name = catalog.download_files(x["e"], x["v"], vision)[0]["dest"], x["e"]["name"]
        firstrun.mark_pending({"path": path, "vision": bool(vision), "name": name})
        st.rerun()


def _finish(pend):
    with st.status("Starting the AI and reading your papers…", expanded=True) as status:
        try:
            models.scan(refresh=True)
            models._GPU.clear()
            models.apply(pend["path"], pend["vision"])
            models.save_choice(pend["path"], pend["vision"])
            services.ensure_llm(log=st.write, timeout=300)
        except Exception as e:
            status.update(label="The AI engine didn't start", state="error")
            st.error(str(e))
            if st.button("Choose another model"):
                firstrun.clear_pending()
                st.rerun()
            return
        bar = st.progress(0.0)
        try:
            res = indexer.update_index(log=st.write, progress=lambda i, n, name: bar.progress(i / n, text=f"{i}/{n}"))
            st.write(f"{res['total']} papers in your library.")
        except Exception as e:
            st.warning(f"Your papers weren't indexed yet ({e}). Use 🔄 Check for new papers in the sidebar.")
        status.update(label="All set", state="complete")
    firstrun.clear_pending()
    downloads.forget_finished()
    st.button("Open Research Assistant", type="primary", on_click=lambda: None)
