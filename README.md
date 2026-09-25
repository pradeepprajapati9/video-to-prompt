# Video → Documentation

Upload a video (or paste a YouTube link) in any language. Gemini watches and listens to it,
then writes documentation for the reader you choose: developer, client, manager, beginner or trader.

## How it works

1. **Extract**: Gemini records only what the video contains: the full transcript (with time
   and speaker) and everything shown on screen (text, form fields, numbers, code, charts, clicks).
2. **Write**: from that material, it writes the documentation. Its sections depend on the
   video type (project, trading, tutorial, lecture, meeting, review), and every important fact
   carries a `[MM:SS]` timestamp. Anything the video leaves unclear goes under *Open questions*
   instead of being guessed.

Step 2 works from text only, so you can rewrite the documentation for another reader or
language without sending the video again.

The result also includes key points, action items, the transcript (which you can translate),
an on-screen log, a *Ask the video* chat and a ready-made AI prompt. You can download it as
`.md` or as print-ready `.html` (use Ctrl+P to save a PDF).

## Setup

```
pip install -r requirements.txt
```

Add your key to `.env` (free key: https://aistudio.google.com/apikey):

```
GEMINI_API_KEY=your-key
APP_PASSWORD=optional-password-for-live-app
```

## Run

```
streamlit run app.py
```

CLI:

```
python bot.py video.mp4 --audience Developer --lang Hinglish
python bot.py "https://www.youtube.com/watch?v=..." --audience "Client / Business owner"
```

## Notes

- Videos are sent through the File API at full quality (up to 2 GB) and deleted from Google
  once analysis is done. If the File API is blocked, the app falls back to a compressed
  inline upload (720p, 2 fps).
- On the free tier, each model allows about 20 requests per day, and one video uses 2.
  If a model is busy or out of quota, the app moves to the next one (5 models, each with its
  own quota), which gives roughly 50 videos per day.
- On the free tier, Google may use the data you send to improve its products. Do not upload
  videos that contain patient or other confidential data.
