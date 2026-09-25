"""
Streamlit UI: koi bhi video (file ya YouTube link) -> reader ke hisaab se documentation.
Chalao:  streamlit run app.py
"""

import os
import uuid
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st

import bot

st.set_page_config(page_title="Video → Docs", page_icon="🎬", layout="wide")

# ---- Password (Streamlit secrets me APP_PASSWORD ho to hi) ----
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
if APP_PASSWORD and not st.session_state.get("authed"):
    st.title("🔒 Video → Docs")
    pw = st.text_input("Password", type="password")
    if pw == APP_PASSWORD:
        st.session_state["authed"] = True
        st.rerun()
    elif pw:
        st.error("Galat password")
    st.stop()

if not bot.API_KEY:
    st.error("GEMINI_API_KEY nahi mili. .env ya Streamlit secrets me daalo.")
    st.stop()


def fmt_type(key):
    return bot.VIDEO_TYPES.get(key, key)


def effective_type(rec):
    t = rec["settings"].get("video_type", "auto")
    return rec["evidence"].get("video_type", "other") if t == "auto" else t


def show_lines(lines):
    for l in lines:
        who = f"**{l['speaker']}:** " if l.get("speaker") else ""
        st.markdown(f"`{l['time']}` {who}{l['text']}")


def settings_row(prefix, defaults=None):
    """Type / reader / bhasha chunne wali row. defaults = pehle se chuni values."""
    d = defaults or {}
    types_, auds, langs = list(bot.VIDEO_TYPES), list(bot.AUDIENCES), bot.LANGUAGES
    c1, c2, c3 = st.columns(3)
    vtype = c1.selectbox("Video type", types_, format_func=fmt_type, key=f"{prefix}-type",
                         index=types_.index(d.get("video_type", "auto")) if d.get("video_type") in types_ else 0)
    aud = c2.selectbox("Kiske liye (reader)", auds, key=f"{prefix}-aud",
                       index=auds.index(d["audience"]) if d.get("audience") in auds else 0)
    lang = c3.selectbox("Bhasha", langs, key=f"{prefix}-lang",
                        index=langs.index(d["lang"]) if d.get("lang") in langs else 0)
    return vtype, aud, lang


# ---- Sidebar: history ----
with st.sidebar:
    if st.button("➕ Naya video", width="stretch"):
        st.session_state.pop("rec", None)
        st.rerun()
    st.subheader("🕘 History")
    history = bot.load_projects()
    if not history:
        st.caption("Abhi tak kuch nahi.")
    for r in reversed(history[-30:]):
        label = f"{r['doc']['title'][:40]}\n\n{r['settings'].get('audience', '')} · {r['saved_at'][:10]}"
        if st.button(label, key=f"h-{r['id']}", width="stretch"):
            st.session_state["rec"] = r
            st.rerun()


# ---- Input ----
def input_screen():
    st.title("🎬 Video → Documentation")
    st.caption("Koi bhi video, kisi bhi bhasha me. Tool poori video dekhta aur sunta hai, "
               "phir jiske liye chahiye (developer, client, manager...) uske hisaab se documentation likhta hai. "
               "Har point ke saath video ka time hota hai, taaki check kar sako.")

    src = st.radio("Video kahan se?", ["📁 File upload", "▶️ YouTube link"], horizontal=True,
                   label_visibility="collapsed")
    uploaded, yt = None, ""
    if src.startswith("📁"):
        uploaded = st.file_uploader("Video chuno", type=["mp4", "mov", "avi", "mkv", "webm", "m4v"])
    else:
        yt = st.text_input("YouTube link (public video)", placeholder="https://www.youtube.com/watch?v=...")

    vtype, aud, lang = settings_row("new")
    note = st.text_area("Extra note (optional)",
                        placeholder="Jaise: 'sirf backend chahiye', 'entry rules pe focus', 'client ko bhejna hai'")

    ready = bool(uploaded) or yt.startswith("http")
    if not st.button("Analyze karo", type="primary", disabled=not ready):
        return

    tmp_path = None
    with st.status("Kaam chal raha hai...", expanded=True) as status:
        try:
            if uploaded:
                suffix = os.path.splitext(uploaded.name)[1] or ".mp4"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    tmp_path = tmp.name
            rec = bot.analyze(path=tmp_path, youtube_url=yt or None, video_type=vtype, audience=aud,
                              lang=lang, note=note, source_name=uploaded.name if uploaded else yt,
                              log=status.write)
            bot.save_project(rec)
            st.session_state["rec"] = rec
            status.update(label="Ho gaya ✅", state="complete", expanded=False)
        except Exception as e:
            status.update(label="Error aaya", state="error")
            st.error(str(e))
            return
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
    st.rerun()


# ---- Result ----
def result_screen(rec):
    d, ev, s = rec["doc"], rec["evidence"], rec["settings"]
    rid = rec["id"]

    st.title(d["title"])
    st.caption(f"{fmt_type(effective_type(rec))}  ·  👤 {s.get('audience', '')}  ·  "
               f"🌐 {s.get('lang', '')}  ·  📁 {rec.get('source', '')}")
    if d.get("tldr"):
        st.info(d["tldr"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Duration", ev.get("duration") or "—")
    m2.metric("Video ki bhasha", ev.get("spoken_language") or "—")
    m3.metric("Transcript lines", len(ev.get("transcript", [])))
    m4.metric("Screen notes", len(ev.get("visuals", [])))

    safe = "".join(c for c in d["title"] if c.isalnum() or c in " -_")[:60].strip() or "video"
    b1, b2, _ = st.columns([1, 1, 2])
    b1.download_button("⬇️ Markdown (.md)", bot.to_markdown(rec), f"{safe}.md", "text/markdown",
                       width="stretch")
    b2.download_button("⬇️ Print / PDF (.html)", bot.to_html(rec), f"{safe}.html", "text/html",
                       width="stretch", help="Browser me kholo aur Ctrl+P se PDF save karo")

    has_evidence = bool(ev.get("transcript") or ev.get("visuals"))
    if has_evidence:
        with st.expander("🔁 Isi video ki documentation kisi aur reader / bhasha ke liye banao (video dobara nahi jayegi)"):
            vtype, aud, lang = settings_row(f"re-{rid}", s)
            note = st.text_input("Extra note", value=s.get("note", ""), key=f"re-note-{rid}")
            if st.button("Dobara likho", key=f"re-btn-{rid}"):
                with st.spinner("Likh rahe hain..."):
                    try:
                        new = bot.write_doc(ev, vtype, aud, lang, note)
                    except Exception as e:
                        st.error(str(e))
                        new = None
                if new:
                    rec2 = {**rec, "id": uuid.uuid4().hex[:8], "doc": new, "qa": [],
                            "saved_at": datetime.now().isoformat(timespec="seconds"),
                            "settings": {"video_type": vtype, "audience": aud, "lang": lang, "note": note}}
                    bot.save_project(rec2)
                    st.session_state["rec"] = rec2
                    st.rerun()

    tabs = st.tabs(["📄 Documentation", "📌 Key points", "✅ Actions & questions",
                    "🗣️ Transcript", "🖥️ Screen log", "💬 Video se poocho", "🚀 AI prompt"])

    with tabs[0]:
        st.markdown(d.get("documentation") or "_Is purani entry me documentation nahi hai._")

    with tabs[1]:
        for p in d.get("key_points", []):
            t = f"`{p['time']}` " if p.get("time") else ""
            st.markdown(f"- {t}{p['point']}")

    with tabs[2]:
        st.subheader("Action items")
        for i, a in enumerate(d.get("action_items", [])):
            st.checkbox(a, key=f"a-{rid}-{i}")
        if d.get("open_questions"):
            st.subheader("Open questions")
            st.caption("Ye baatein video me clear nahi thi. Kaam shuru karne se pehle inka jawab le lo.")
            for q in d["open_questions"]:
                st.markdown(f"- {q}")

    with tabs[3]:
        lines = ev.get("transcript", [])
        if not lines:
            st.write("Is video me koi boli hui baat nahi mili.")
        else:
            c1, c2 = st.columns([2, 1])
            tlang = c1.selectbox("Translate karo", bot.LANGUAGES, key=f"tl-{rid}",
                                 index=bot.LANGUAGES.index(s.get("lang", "Hinglish"))
                                 if s.get("lang") in bot.LANGUAGES else 0)
            done = rec.setdefault("translations", {}).get(tlang)
            if not done and c2.button("Translate", key=f"tlb-{rid}", width="stretch"):
                with st.spinner("Translate ho raha hai..."):
                    try:
                        rec["translations"][tlang] = bot.translate(lines, tlang)
                        bot.save_project(rec)
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
            if done:
                st.markdown(f"**{tlang}**")
                show_lines(done)
                with st.expander(f"Original ({ev.get('spoken_language', '')})"):
                    show_lines(lines)
            else:
                show_lines(lines)

    with tabs[4]:
        vis = ev.get("visuals", [])
        if vis:
            st.caption("Video me screen par jo dikha: text, fields, numbers, code, charts, clicks.")
            st.dataframe(pd.DataFrame(vis)[["time", "kind", "content"]], hide_index=True,
                         width="stretch")
        else:
            st.write("Screen ka koi note nahi.")

    with tabs[5]:
        if not has_evidence:
            st.write("Is purani entry me video ka data nahi hai, sawaal nahi pooch sakte.")
        else:
            qa = rec.setdefault("qa", [])
            for turn in qa:
                st.chat_message("user").write(turn["q"])
                st.chat_message("assistant").markdown(turn["a"])
            q = st.chat_input("Video ke baare me kuch bhi poocho...", key=f"ask-{rid}")
            if q:
                with st.spinner("Soch raha hai..."):
                    try:
                        qa.append({"q": q, "a": bot.ask(rec, q, s.get("lang", "Hinglish"))})
                        bot.save_project(rec)
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    with tabs[6]:
        if d.get("ultra_prompt"):
            st.caption("Ise copy karke kisi bhi AI (Claude, ChatGPT, Gemini) ko do. Video ki saari zaroori baatein isme hain.")
            st.code(d["ultra_prompt"], language="text")


if st.session_state.get("rec"):
    result_screen(st.session_state["rec"])
else:
    input_screen()
