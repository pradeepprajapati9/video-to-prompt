"""
Video -> Project + Ultra Prompt bot
-----------------------------------
Ek video do (koi project explain karta hua). Bot use dekhega + sunega,
requirement samjhega, aur:
  1. project ko projects.json me save karega
  2. ek "ultra pro-max" prompt banayega jise user copy karke project shuru kar sake.

Free stack: Google Gemini API (free tier me video seedha samajh leta hai).
"""

import os
import sys
import json
import time
from datetime import datetime

import google.generativeai as genai


def _load_env():
    """.env file ko bina extra library ke padho."""
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
API_KEY = os.environ.get("GEMINI_API_KEY", "")   # apni free key yaha ya env me
MODEL = "gemini-flash-latest"                     # free tier, video support
PROJECTS_FILE = os.path.join(os.path.dirname(__file__), "projects.json")


def build_instruction(lang="Hinglish"):
    """Gemini ke liye instruction. lang = jis bhasha me samjhana hai."""
    return f"""
Tum ek senior software architect ho. Tumhe ek video di gayi hai jisme koi
banda ek project/feature explain ya demo kar raha hai (screen, aawaz, ya dono).

Video ko dhyaan se DEKHO aur SUNO. Fir SIRF ek valid JSON do (aur kuch nahi),
is exact shape me:

{{
  "project_name": "chhota clear naam",
  "summary": "2-3 line me kya banana hai",
  "requirements": ["point 1", "point 2", "..."],
  "tech_stack": ["suggested tools/languages"],
  "ultra_prompt": "ek lamba, detail-bhara prompt jise user kisi AI/coder ko de kar poora project shuru kara sake. Isme goal, features, steps, aur output format sab likha ho.",
  "spoken_language": "video me jo bhasha boli gayi (jaise Hindi, English)",
  "transcript": [
    {{"time": "MM:SS", "text": "video me jo bola gaya, bilkul waisa hi, usi bhasha me"}}
  ],
  "transcript_translated": [
    {{"time": "MM:SS", "text": "upar wali line ka {lang} me anuvaad"}}
  ],
  "speech_explanation": "{lang} me saaf samjhao ki video me aakhir kya bola gaya: main baatein, kya maanga gaya, koi zaroori detail. Point-wise, aasaan bhasha me."
}}

Rules:
- transcript me poori boli hui baat likho, kuch chhodo mat. Agar video me koi
  bola hi nahi, to transcript aur transcript_translated khali list [] rakho.
- transcript_translated aur speech_explanation hamesha {lang} me hon.
"""


INSTRUCTION = build_instruction()


def load_projects():
    if os.path.exists(PROJECTS_FILE):
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_project(entry):
    projects = load_projects()
    projects.append(entry)
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)


def analyze_video(video_path):
    if not API_KEY:
        sys.exit("GEMINI_API_KEY set karo. Free key: https://aistudio.google.com/apikey")

    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel(MODEL)

    print(f"[+] Video upload ho rahi hai: {video_path}")
    video = genai.upload_file(path=video_path)

    # Gemini ko video process karne me thoda time lagta hai
    while video.state.name == "PROCESSING":
        print("    ...processing")
        time.sleep(3)
        video = genai.get_file(video.name)

    if video.state.name == "FAILED":
        sys.exit("Video process nahi hui.")

    print("[+] Video ready. AI samajh raha hai...")
    resp = model.generate_content([video, INSTRUCTION])

    text = resp.text.strip()
    # kabhi kabhi ```json ... ``` me aata hai, clean karo
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text)


def main():
    if len(sys.argv) < 2:
        sys.exit("Use karo:  python bot.py <video-file-path>")

    video_path = sys.argv[1]
    if not os.path.exists(video_path):
        sys.exit(f"File nahi mili: {video_path}")

    data = analyze_video(video_path)
    data["saved_at"] = datetime.now().isoformat(timespec="seconds")
    data["source_video"] = os.path.basename(video_path)

    save_project(data)

    print("\n==================  PROJECT SAVED  ==================")
    print(f"Naam    : {data['project_name']}")
    print(f"Summary : {data['summary']}")
    print("\n----------  ULTRA PROMPT (copy karo)  ----------\n")
    print(data["ultra_prompt"])
    if data.get("transcript"):
        print("\n----------  VIDEO ME KYA BOLA GAYA  ----------\n")
        for line in data["transcript"]:
            print(f"[{line.get('time', '')}] {line.get('text', '')}")
        print("\n" + data.get("speech_explanation", ""))
    print("\n====================================================")
    print(f"Sab kuch save hua: {PROJECTS_FILE}")


if __name__ == "__main__":
    main()
