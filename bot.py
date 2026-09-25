"""
Video -> Documentation engine (+ CLI)
-------------------------------------
Any video (upload or YouTube link), in any language.

Two-step pipeline:
  1. EXTRACT  - Gemini watches and listens to the video and captures only evidence:
                the full transcript (time + speaker) and what is shown on screen
                (text, fields, numbers, code, charts, clicks). No interpretation.
  2. WRITE    - From that evidence, writes professional documentation for the
                chosen reader (developer / client / manager...) and language.
                Every key fact carries [MM:SS] so it can be checked in the video.

Benefit: step 2 runs on text only, so documentation for another reader or
language can be regenerated without re-sending the video.
"""

import os
import re
import sys
import json
import time
import uuid
import argparse
import subprocess
from datetime import datetime
from typing import Literal

import imageio_ffmpeg
from pydantic import BaseModel
from google import genai
from google.genai import types, errors


def _load_env():
    """Read the .env file without an extra library."""
    path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# ---- Config ----
API_KEY = os.environ.get("GEMINI_API_KEY", "")
# if a model is busy (503) or out of quota (429), the next one is tried
MODELS = [os.environ.get("GEMINI_MODEL", "gemini-flash-latest"), "gemini-2.5-flash"]
PROJECTS_FILE = os.path.join(os.path.dirname(__file__), "projects.json")

INLINE_LIMIT_MB = 18        # inline request limit is ~20 MB
LONG_VIDEO_MIN = 45         # longer videos use low media resolution to save tokens

VIDEO_TYPES = {
    "auto": "🔍 Auto-detect",
    "project": "💻 Project / requirement",
    "trading": "📈 Trading / finance",
    "tutorial": "🛠️ Tutorial / how-to",
    "lecture": "🎓 Lecture / explainer",
    "meeting": "🤝 Meeting / discussion",
    "review": "🛒 Product review / ad",
    "other": "📦 Other",
}

AUDIENCES = {
    "General": "plain language, every important point; neither too technical nor too basic",
    "Developer": "technical depth: architecture, data model, APIs, validations, edge cases, "
                 "exact steps/code; anything unclear becomes an open question",
    "Client / Business owner": "no jargon: what they get, the benefit, what is in and out of scope, "
                               "what they need to decide or approve",
    "Manager / Team lead": "scope, work breakdown, milestones, dependencies, risks, "
                           "who (which role) does what",
    "Beginner / Student": "start from the basics, explain every term, use examples",
    "Trader / Investor": "exact rules, numbers, levels, risk-reward, what needs verifying",
}

LANGUAGES = ["English", "Hindi", "Hinglish", "Marathi", "Gujarati", "Bengali", "Tamil",
             "Telugu", "Kannada", "Malayalam", "Punjabi", "Urdu", "Spanish", "French", "Arabic"]

# documentation sections for each video type
TYPE_GUIDE = {
    "project": "Overview; Problem / goal; Users & roles; Functional requirements (numbered, "
               "each one testable, with [MM:SS] where shown); Screens / UI flow (step by step, "
               "field names exact); Business rules & validations; Data model (table: entity, fields); "
               "Integrations / APIs; Non-functional (security, performance, privacy); "
               "Suggested tech stack (suggestion); Milestones; Acceptance criteria; Out of scope",
    "trading": "Market / instrument; Core idea of the strategy; Timeframe; Indicators & settings; "
               "Entry rules (exact conditions); Exit rules (target, stop loss, trailing); "
               "Position sizing & risk; Examples from the video (table: time, instrument, entry, SL, target, "
               "result); When it will not work; Claims to backtest/verify. "
               "End with: 'These are the video's claims, not financial advice.'",
    "tutorial": "What you will build / learn; Prerequisites (versions); Step-by-step (numbered; exact "
                "commands, code, settings, menu path); How to verify; Common errors & fixes; Next steps",
    "lecture": "Topics; Explanation of each concept; Definitions & formulas; Examples; "
               "Summary; 5 revision questions (with answers)",
    "meeting": "Participants; Agenda; Discussion (topic-wise); Decisions; "
               "Action items (table: who, what, when); Open issues",
    "review": "Product; Features; Pros; Cons; Price / offers; Who it suits; Verdict; "
              "Claims to verify",
    "other": "Whatever sections are most useful for this video",
}


# ---- Output shapes (Gemini answers in these, so there are no parse errors) ----
class Line(BaseModel):
    time: str
    speaker: str
    text: str


class Visual(BaseModel):
    time: str
    kind: str
    content: str


class Evidence(BaseModel):
    video_type: Literal["project", "trading", "tutorial", "lecture", "meeting", "review", "other"]
    title: str
    spoken_language: str
    duration: str
    transcript: list[Line]
    visuals: list[Visual]


class Point(BaseModel):
    point: str
    time: str


class Doc(BaseModel):
    title: str
    tldr: str
    key_points: list[Point]
    documentation: str
    action_items: list[str]
    open_questions: list[str]
    ultra_prompt: str


class Translated(BaseModel):
    lines: list[Line]


# ---- Gemini ----
_client = None


def client():
    global _client
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY not found. Get a free key: https://aistudio.google.com/apikey")
    if _client is None:
        _client = genai.Client(api_key=API_KEY)
    return _client


def _generate(contents, schema=None, temperature=0.2, media_resolution=None):
    """Call with retry + model fallback. Returns a parsed object when a schema is given."""
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=65536,
        response_mime_type="application/json" if schema else None,
        response_schema=schema,
        media_resolution=media_resolution,
    )
    last = None
    for model in MODELS:
        for attempt in range(2):
            try:
                resp = client().models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as e:
                last = e
                if e.code in (429, 500, 503, 504):
                    time.sleep(4 * (attempt + 1))
                    continue
                raise
            if not schema:
                return resp.text
            if resp.parsed is not None:
                return resp.parsed
            reason = resp.candidates[0].finish_reason if resp.candidates else "unknown"
            raise RuntimeError(f"Incomplete response ({reason}). Try a shorter video.")
    raise RuntimeError(f"Gemini is busy right now, try again in 1-2 minutes. ({last})")


# ---- Video input ----
def _ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def video_duration(path):
    """Video length in seconds (0 if unknown)."""
    out = subprocess.run([_ffmpeg(), "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0


def compress_video(path, target_mb=15):
    """Shrink for inline upload. Gemini samples ~1 frame/sec, so we drop fps
    and keep resolution (720p) so on-screen text stays readable."""
    out_path = path + ".small.mp4"
    dur = video_duration(path)
    if dur > 0:
        video_k = max(150, int(target_mb * 8 * 1024 / dur - 48))
        rate = ["-b:v", f"{video_k}k", "-maxrate", f"{int(video_k * 1.5)}k", "-bufsize", f"{video_k * 2}k"]
    else:
        rate = ["-crf", "30"]
    subprocess.run([
        _ffmpeg(), "-y", "-i", path,
        "-vf", "scale=-2:'min(720,ih)'", "-r", "2",
        "-c:v", "libx264", *rate, "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "48k", "-ac", "1",
        out_path,
    ], capture_output=True)
    return out_path if os.path.exists(out_path) else path


def prepare_video(path=None, youtube_url=None, log=print):
    """Build the video part for Gemini. Returns (part, cleanup_fn, duration_sec)."""
    if youtube_url:
        log("Sending YouTube video to Gemini...")
        return types.Part(file_data=types.FileData(file_uri=youtube_url.strip())), (lambda: None), 0

    dur = video_duration(path)
    # 1) File API: full quality, up to 2 GB
    try:
        log("Uploading video (full quality)...")
        f = client().files.upload(file=path)
        while f.state.name == "PROCESSING":
            time.sleep(3)
            f = client().files.get(name=f.name)
        if f.state.name == "ACTIVE":
            return f, (lambda: _safe_delete(f.name)), dur
        log("Upload was not processed, trying another method...")
    except Exception as e:
        log(f"Upload failed ({str(e)[:80]}), trying another method...")

    # 2) Fallback: inline (works even if the network blocks the File API)
    send = path
    if os.path.getsize(path) / 1048576 > INLINE_LIMIT_MB:
        log("Video is large, compressing...")
        send = compress_video(path)
    with open(send, "rb") as fh:
        data = fh.read()
    if send != path:
        os.unlink(send)
    if len(data) / 1048576 > 20:
        raise RuntimeError("Video is too long. Upload a shorter part or use a YouTube link.")
    return types.Part.from_bytes(data=data, mime_type="video/mp4"), (lambda: None), dur


def _safe_delete(name):
    """Delete the uploaded video from Google (privacy)."""
    try:
        client().files.delete(name=name)
    except Exception:
        pass


# ---- Step 1: extract evidence ----
EXTRACT_PROMPT = """
You are a meticulous video analyst. Watch AND listen to the whole video, start to end.
Your only job is to capture EVIDENCE, faithfully. Do not summarize, interpret or advise.

1. transcript: every spoken sentence, verbatim, in the ORIGINAL spoken language and script
   (do not translate). Segments of 1-3 sentences. time = start as MM:SS (H:MM:SS if >1h).
   speaker = name if said/shown, else "Speaker 1", "Speaker 2"...
   No speech at all -> empty list.
2. visuals: everything important that is SHOWN, in time order. kind is one of:
   screen_text, ui_action, form_field, code, chart, slide, table, scene.
   Copy on-screen text EXACTLY: field labels, button names, menu paths, URLs, error
   messages, numbers, prices, dates, code lines, chart symbol/timeframe/indicator values/levels.
   ui_action = what the user did ("clicked Submit", "opened Claim Details tab").
   Skip frames that repeat with nothing new.
3. video_type: project (someone describing/demoing software or a process to automate/build),
   trading (markets, stocks, crypto, strategy), tutorial (how to do something step by step),
   lecture (teaching a concept), meeting (people discussing / deciding), review (product review/ad),
   other.
4. title: short, specific. duration: total length MM:SS.
"""


def extract(video_part, duration_sec=0):
    low = types.MediaResolution.MEDIA_RESOLUTION_LOW if duration_sec > LONG_VIDEO_MIN * 60 else None
    ev = _generate([video_part, EXTRACT_PROMPT], schema=Evidence, temperature=0.1, media_resolution=low)
    return ev.model_dump()


def evidence_text(ev):
    """Evidence as compact text (for the step 2 prompt)."""
    out = [f"Title: {ev.get('title', '')}", f"Duration: {ev.get('duration', '')}",
           f"Spoken language: {ev.get('spoken_language', '')}", "", "SPOKEN:"]
    out += [f"[{l['time']}] {l['speaker']}: {l['text']}" for l in ev.get("transcript", [])] or ["(no speech)"]
    out += ["", "ON SCREEN:"]
    out += [f"[{v['time']}] ({v['kind']}) {v['content']}" for v in ev.get("visuals", [])] or ["(nothing noted)"]
    return "\n".join(out)


# ---- Step 2: write documentation ----
def write_doc(ev, video_type="auto", audience="General", lang="English", note=""):
    vtype = ev.get("video_type", "other") if video_type == "auto" else video_type
    prompt = f"""
You are a senior analyst and technical writer. Below is the complete evidence extracted
from a video (what was said + what was shown, with timestamps). Write documentation so
that someone who never watches the video fully understands it and can act on it.

VIDEO TYPE: {vtype}
READER: {audience} -> {AUDIENCES.get(audience, audience)}
LANGUAGE: {lang} (keep technical terms like API, stop loss, database as-is)
{"USER NOTE: " + note.strip() if note.strip() else ""}

Documentation sections for this type (adapt depth and wording to the READER):
{TYPE_GUIDE.get(vtype, TYPE_GUIDE["other"])}

Rules:
- Use ONLY the evidence. Never invent facts, numbers, names or rules.
- Put [MM:SS] after important facts so the reader can verify in the video.
- If you add your own recommendation (e.g. tech stack), mark it "(suggestion)".
- Anything unclear, missing or contradictory goes to open_questions, not guessed.
- documentation: Markdown with ## headings, numbered lists, tables where they help.
  Do not repeat the title or TL;DR inside it.
- key_points: 5-10 most important points. Put the timestamp ONLY in the time field
  (MM:SS, "" if general), never inside the point text.
- action_items: concrete next steps for THIS reader.
- ultra_prompt: a complete, self-contained prompt the reader can paste into any AI to
  continue the work (project -> build it; trading -> code/backtest the rules;
  tutorial/lecture -> practice/learn deeper; meeting -> execute action items). Include all
  relevant facts from the evidence, since the AI will not see the video.
- Everything except ultra_prompt in {lang}. ultra_prompt in English.

EVIDENCE:
{evidence_text(ev)}
"""
    return _generate([prompt], schema=Doc, temperature=0.3).model_dump()


# ---- Extras: transcript translation + ask the video ----
def translate(lines, lang):
    if not lines:
        return []
    src = "\n".join(f"[{l['time']}] {l['speaker']}: {l['text']}" for l in lines)
    prompt = (f"Translate each line to {lang}. Keep time and speaker unchanged, same number of lines, "
              f"natural meaning over word-by-word.\n\n{src}")
    return _generate([prompt], schema=Translated, temperature=0.1).model_dump()["lines"]


def ask(record, question, lang="English"):
    prompt = f"""
Answer the question using ONLY this video's evidence below. Answer in {lang}.
Cite [MM:SS] for facts. If the video does not contain the answer, say so clearly
and then (separately, marked as general knowledge) give a short helpful answer.

QUESTION: {question}

EVIDENCE:
{evidence_text(record["evidence"])}
"""
    return _generate([prompt], temperature=0.2)


# ---- Full pipeline ----
def analyze(path=None, youtube_url=None, video_type="auto", audience="General",
            lang="English", note="", source_name="", log=print):
    part, cleanup, dur = prepare_video(path, youtube_url, log)
    try:
        log("AI is watching and listening (transcript + screen)...")
        ev = extract(part, dur)
    finally:
        cleanup()
    log("Writing documentation...")
    doc = write_doc(ev, video_type, audience, lang, note)
    return {
        "id": uuid.uuid4().hex[:8],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "source": source_name or youtube_url or os.path.basename(path or ""),
        "settings": {"video_type": video_type, "audience": audience, "lang": lang, "note": note},
        "evidence": ev,
        "doc": doc,
        "translations": {},
    }


# ---- Storage ----
def normalize(rec):
    """Convert an entry from the old (v1) format to the new one."""
    if "doc" in rec:
        return rec
    return {
        "id": rec.get("saved_at", "old"),
        "saved_at": rec.get("saved_at", ""),
        "source": rec.get("source_video", ""),
        "settings": {"video_type": rec.get("video_type", "project"), "audience": "General",
                     "lang": rec.get("lang", "English"), "note": ""},
        "evidence": {"video_type": rec.get("video_type", "project"),
                     "title": rec.get("title") or rec.get("project_name", ""),
                     "spoken_language": rec.get("spoken_language", ""), "duration": "",
                     "transcript": [{"speaker": "", **l} for l in rec.get("transcript", [])],
                     "visuals": []},
        "doc": {"title": rec.get("title") or rec.get("project_name", "Untitled"),
                "tldr": rec.get("tldr") or rec.get("summary", ""),
                "key_points": [{"point": p, "time": ""} for p in
                               (rec.get("key_points") or rec.get("requirements", []))],
                "documentation": rec.get("documentation", ""),
                "action_items": rec.get("action_items", []),
                "open_questions": [],
                "ultra_prompt": rec.get("ultra_prompt", "")},
        "translations": {},
    }


def load_projects():
    if os.path.exists(PROJECTS_FILE):
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            return [normalize(r) for r in json.load(f)]
    return []


def save_project(rec):
    """Add a new record, or update the one with the same id."""
    projects = [p for p in load_projects() if p["id"] != rec["id"]] + [rec]
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)


# ---- Export ----
def to_markdown(rec):
    d, ev, s = rec["doc"], rec["evidence"], rec["settings"]
    vtype = ev.get("video_type") if s.get("video_type") == "auto" else s.get("video_type")
    out = [f"# {d['title']}", "",
           "| | |", "|---|---|",
           f"| Type | {VIDEO_TYPES.get(vtype, vtype)} |",
           f"| Reader | {s.get('audience', '')} |",
           f"| Source | {rec.get('source', '')} |",
           f"| Duration | {ev.get('duration', '')} |",
           f"| Spoken language | {ev.get('spoken_language', '')} |",
           f"| Generated | {rec.get('saved_at', '')} |", ""]
    if d.get("tldr"):
        out += ["## TL;DR", d["tldr"], ""]
    if d.get("key_points"):
        out += ["## Key points"]
        out += [f"- {p['point']}" + (f" `[{p['time']}]`" if p.get("time") else "") for p in d["key_points"]]
        out.append("")
    if d.get("documentation"):
        out += [d["documentation"], ""]
    if d.get("action_items"):
        out += ["## Action items"] + [f"- [ ] {a}" for a in d["action_items"]] + [""]
    if d.get("open_questions"):
        out += ["## Open questions"] + [f"- {q}" for q in d["open_questions"]] + [""]
    if d.get("ultra_prompt"):
        out += ["## Appendix A: AI prompt", "```", d["ultra_prompt"], "```", ""]
    if ev.get("transcript"):
        out += ["## Appendix B: Transcript", ""]
        out += [f"- `{l['time']}` **{l.get('speaker', '')}**: {l['text']}" for l in ev["transcript"]]
        out.append("")
    if ev.get("visuals"):
        out += ["## Appendix C: On-screen log", ""]
        out += [f"- `{v['time']}` *{v['kind']}*: {v['content']}" for v in ev["visuals"]]
    return "\n".join(out)


def to_html(rec):
    """Print-ready HTML (open in a browser, Ctrl+P -> PDF; also opens in Word)."""
    import markdown
    body = markdown.markdown(to_markdown(rec), extensions=["tables", "fenced_code", "sane_lists"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{rec['doc']['title']}</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:40px auto;padding:0 20px;
color:#1f2328;line-height:1.6}}
h1{{border-bottom:3px solid #0969da;padding-bottom:8px}} h2{{margin-top:32px;color:#0969da}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
td,th{{border:1px solid #d0d7de;padding:6px 10px;text-align:left;vertical-align:top}}
th{{background:#f6f8fa}} code{{background:#f6f8fa;padding:1px 5px;border-radius:4px}}
pre{{background:#f6f8fa;padding:12px;border-radius:6px;white-space:pre-wrap}}
@media print{{body{{margin:0}} h2{{page-break-after:avoid}}}}
</style></head><body>{body}</body></html>"""


# ---- CLI ----
def main():
    ap = argparse.ArgumentParser(description="Video -> documentation")
    ap.add_argument("video", help="video file path or YouTube link")
    ap.add_argument("--lang", default="English")
    ap.add_argument("--audience", default="General", choices=list(AUDIENCES))
    ap.add_argument("--type", default="auto", choices=list(VIDEO_TYPES))
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    is_url = a.video.startswith("http")
    if not is_url and not os.path.exists(a.video):
        sys.exit(f"File not found: {a.video}")

    rec = analyze(path=None if is_url else a.video, youtube_url=a.video if is_url else None,
                  video_type=a.type, audience=a.audience, lang=a.lang, note=a.note,
                  log=lambda m: print(f"[+] {m}"))
    save_project(rec)

    base = "video" if is_url else os.path.splitext(a.video)[0]
    with open(base + "_docs.md", "w", encoding="utf-8") as f:
        f.write(to_markdown(rec))
    with open(base + "_docs.html", "w", encoding="utf-8") as f:
        f.write(to_html(rec))
    print(f"\n{rec['doc']['title']}\n\n{rec['doc']['tldr']}\n")
    print(f"Documentation: {base}_docs.md  |  {base}_docs.html")


if __name__ == "__main__":
    main()
