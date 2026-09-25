"""
Streamlit UI: video upload karo -> project + ultra prompt milega.
Chalao:  streamlit run app.py
"""

import os
import json
import time
import subprocess
import tempfile
from datetime import datetime

import streamlit as st
import google.generativeai as genai
import imageio_ffmpeg

from bot import _load_env, build_instruction, MODEL, PROJECTS_FILE, save_project

# Inline video limit ~20 MB. Isse badi video ko compress karenge.
COMPRESS_ABOVE_MB = 18
# Compress karne ke baad video itni MB ke aas-paas laayenge
TARGET_MB = 15


def _duration_sec(ffmpeg, in_path):
    """ffmpeg stderr se video ki length (seconds) nikaalo."""
    out = subprocess.run([ffmpeg, "-i", in_path], capture_output=True, text=True)
    for line in out.stderr.splitlines():
        if "Duration:" in line:
            t = line.split("Duration:")[1].split(",")[0].strip()  # HH:MM:SS.xx
            h, m, s = t.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0


def compress_video(in_path, target_mb=TARGET_MB):
    """Video ko target_mb ke aas-paas laao. Chhoti/lambi dono handle."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    out_path = in_path + ".small.mp4"

    dur = _duration_sec(ffmpeg, in_path)
    if dur <= 0:
        # duration nahi mili -> quality-based fallback
        vbr_args = ["-crf", "30"]
    else:
        # total target bitrate (kbps); audio ke liye 64k reserve
        audio_k = 64
        total_k = (target_mb * 8 * 1024) / dur      # kbit/s
        video_k = max(300, int(total_k - audio_k))  # kam se kam 300k video
        vbr_args = ["-b:v", f"{video_k}k",
                    "-maxrate", f"{int(video_k * 1.5)}k",
                    "-bufsize", f"{video_k * 2}k"]

    cmd = [
        ffmpeg, "-y", "-i", in_path,
        "-vf", "scale=-2:480",       # height 480p, width auto
        "-r", "10",                   # 10 fps (Gemini frames sample karta hai)
        "-c:v", "libx264", *vbr_args, "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "64k",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True)
    return out_path if os.path.exists(out_path) else in_path

_load_env()
API_KEY = os.environ.get("GEMINI_API_KEY", "")

st.set_page_config(page_title="Video → Project + Prompt", page_icon="🎬")
st.title("🎬 Video → Project + Ultra Prompt")
st.caption("Video upload karo. Bot dekhega + sunega, project samjhega, aur ready prompt dega.")

if not API_KEY:
    st.error("GEMINI_API_KEY nahi mili. .env file me daalo.")
    st.stop()


def analyze(video_bytes, filename, user_note="", lang="Hinglish"):
    # temp file me save karo
    suffix = os.path.splitext(filename)[1] or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(video_bytes)
        tmp_path = tmp.name

    # Inline video ki limit ~20 MB hai. Isse badi ho to compress karo.
    size_mb = len(video_bytes) / (1024 * 1024)
    if size_mb > COMPRESS_ABOVE_MB:
        st.warning(f"⚠️ Video badi hai ({size_mb:.0f} MB). Hum ise compress kar rahe hain, thoda ruko...")
        with st.spinner("Video compress ho rahi hai..."):
            small = compress_video(tmp_path)
        if small != tmp_path:
            new_mb = os.path.getsize(small) / (1024 * 1024)
            st.info(f"✅ Compress ho gaya: {size_mb:.0f} MB → {new_mb:.0f} MB")
            os.unlink(tmp_path)
            tmp_path = small

    # final bytes padho (compressed ya original)
    with open(tmp_path, "rb") as f:
        final_bytes = f.read()
    os.unlink(tmp_path)

    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel(MODEL)

    with st.status("AI project samajh raha hai...", expanded=True) as status:
        prompt = build_instruction(lang)
        if user_note.strip():
            prompt += f"\n\nUser ne ye extra note diya hai, ise dhyaan me rakho:\n{user_note.strip()}"
        # File API ki jagah inline video (usi endpoint se jo chalti hai)
        resp = model.generate_content([
            {"mime_type": "video/mp4", "data": final_bytes},
            prompt,
        ], generation_config={"response_mime_type": "application/json"})
        status.update(label="Ho gaya!", state="complete")

    text = resp.text.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text)


uploaded = st.file_uploader("Video chuno", type=["mp4", "mov", "avi", "mkv", "webm"])

user_note = st.text_area(
    "Description (optional)",
    placeholder="Project se related koi extra baat... jaise 'React me chahiye', 'sirf backend', 'mobile app', etc.",
)

LANGUAGES = ["Hinglish", "Hindi", "English", "Marathi", "Gujarati", "Bengali",
             "Tamil", "Telugu", "Punjabi", "Urdu", "Spanish", "French", "Arabic"]
lang = st.selectbox("Video me jo bola gaya, kis bhasha me samjhaun?", LANGUAGES)

if uploaded and st.button("Analyze karo", type="primary"):
    try:
        data = analyze(uploaded.getvalue(), uploaded.name, user_note, lang)
    except Exception as e:
        st.error(f"Error: {e}")
        data = None

    if data:
        data["saved_at"] = datetime.now().isoformat(timespec="seconds")
        data["source_video"] = uploaded.name
        save_project(data)

        st.success(f"Saved: {data['project_name']}")
        st.subheader(data["project_name"])
        st.write(data["summary"])

        with st.expander("Requirements"):
            for r in data.get("requirements", []):
                st.markdown(f"- {r}")
        with st.expander("Tech stack"):
            st.write(", ".join(data.get("tech_stack", [])))

        st.subheader("🚀 Ultra Prompt (copy karo)")
        st.code(data["ultra_prompt"], language="text")

        st.subheader("🗣️ Video me kya bola gaya")
        transcript = data.get("transcript", [])
        if not transcript:
            st.info("Is video me koi boli hui baat nahi mili.")
        else:
            st.markdown(f"**Samjho ({lang}):**")
            st.write(data.get("speech_explanation", ""))

            translated = data.get("transcript_translated", [])
            with st.expander(f"Poora transcript — {lang} me"):
                for line in translated:
                    st.markdown(f"`{line.get('time', '')}` {line.get('text', '')}")
            with st.expander(f"Original transcript — {data.get('spoken_language', '')}"):
                for line in transcript:
                    st.markdown(f"`{line.get('time', '')}` {line.get('text', '')}")

st.divider()
st.caption(f"Sab projects yaha save hote hain: {PROJECTS_FILE}")
