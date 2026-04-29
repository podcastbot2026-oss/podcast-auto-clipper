import os
import subprocess
import json
import urllib.request
from datetime import datetime
import pytz
import yt_dlp
import whisper
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# ══════════════════════════════════════════════════════
API_KEY      = os.environ["YT_API_KEY"]
CHANNEL_ID   = os.environ["MY_CHANNEL_ID"]
TOKEN_JSON   = os.environ["OAUTH_TOKEN"]
GEMINI_KEY   = os.environ["GEMINI_API_KEY"]
YT_COOKIES   = os.environ.get("YT_COOKIES", "")
REPO_NAME    = "podcast-auto-clipper"
LOGO_PATH    = f"/home/runner/work/{REPO_NAME}/{REPO_NAME}/logo.png"
SEARCH       = "raj shamani viral podcast"
IST          = pytz.timezone("Asia/Kolkata")
COOKIES_PATH = "/tmp/cookies.txt"
# ══════════════════════════════════════════════════════


# ── WRITE COOKIES FILE ───────────────────────────────
def setup_cookies():
    if YT_COOKIES:
        with open(COOKIES_PATH, "w") as f:
            f.write(YT_COOKIES)
        print("   🍪 YouTube cookies loaded")
    else:
        print("   ⚠️  No cookies found — may get blocked")


# ── SCHEDULE TIMES ───────────────────────────────────
def get_schedule_times():
    now   = datetime.now(IST)
    d     = now.date()
    slot1 = IST.localize(datetime(d.year, d.month, d.day, 16,  0, 0))
    slot2 = IST.localize(datetime(d.year, d.month, d.day, 16, 15, 0))
    return slot1.isoformat(), slot2.isoformat()


# ── AI METADATA USING GEMINI (FREE) ──────────────────
def generate_ai_metadata(transcript, slot):
    print(f"   🤖 Gemini AI generating metadata for Short {slot}...")

    prompt = f"""You are a YouTube Shorts expert.
Based on this podcast transcript, generate:
1. A viral catchy title (max 90 chars, no clickbait)
2. A compelling description (3-4 lines with hashtags at end)
3. 10 relevant tags as JSON array

Transcript:
{transcript[:1500]}

Reply ONLY in this exact JSON format, nothing else:
{{
  "title": "your title here",
  "description": "your description here",
  "tags": ["tag1","tag2","tag3","tag4","tag5",
           "tag6","tag7","tag8","tag9","tag10"]
}}"""

    payload = json.dumps({
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature":     0.7,
            "maxOutputTokens": 500
        }
    }).encode("utf-8")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    )

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            raw  = (
                data["candidates"][0]["content"]
                    ["parts"][0]["text"].strip()
            )
            raw      = raw.replace("```json","").replace("```","").strip()
            metadata = json.loads(raw)
            print(f"   ✅ Title: {metadata['title']}")
            return metadata
    except Exception as e:
        print(f"   ⚠️  Gemini failed ({e}) — using default")
        return {
            "title":       "Raj Shamani Best Moment You Must Watch",
            "description": (
                "Best moment from Raj Shamani podcast!\n\n"
                "#Shorts #RajShamani #Podcast #Viral #Motivation"
            ),
            "tags": [
                "rajshamani","podcast","shorts","viral",
                "motivation","clips","interview","trending",
                "fyp","podcastclips"
            ]
        }


# ── SEARCH ───────────────────────────────────────────
def search_podcasts():
    print(f"🔍 Searching: {SEARCH}")
    yt  = build("youtube", "v3", developerKey=API_KEY)
    res = yt.search().list(
        q=SEARCH,
        part="snippet",
        type="video",
        order="viewCount",
        videoDuration="long",
        maxResults=20
    ).execute()
    results = [
        (item["id"]["videoId"], item["snippet"]["title"])
        for item in res["items"]
    ]
    print(f"   Found {len(results)} videos")
    return results


# ── DUPLICATE CHECK ──────────────────────────────────
def already_uploaded(keyword):
    yt  = build("youtube", "v3", developerKey=API_KEY)
    res = yt.search().list(
        q=keyword,
        part="snippet",
        type="video",
        channelId=CHANNEL_ID,
        maxResults=3
    ).execute()
    exists = len(res.get("items", [])) > 0
    if exists:
        print("   ⏭  Already on channel — skipping")
    return exists


# ── DOWNLOAD ─────────────────────────────────────────
def download_video(video_id, index):
    print(f"   ⬇️  Downloading video {index}...")

    opts = {
        "format":              "best[ext=mp4]/bestvideo+bestaudio/best",
        "outtmpl":             f"/tmp/input_{index}.%(ext)s",
        "merge_output_format": "mp4",
        "ignoreerrors":        False,
        "no_warnings":         False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
    }

    # Add cookies if available
    if YT_COOKIES and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([
            f"https://www.youtube.com/watch?v={video_id}"
        ])
    print(f"   ✅ Download {index} done")
    return f"/tmp/input_{index}.mp4"


# ── BEST MOMENT ──────────────────────────────────────
def find_best_moment(path):
    print("   🎯 Finding best 60 second moment...")
    model  = whisper.load_model("tiny")
    result = model.transcribe(path)
    segs   = result["segments"]
    if not segs:
        return 60, 119, "No transcript available."
    best      = max(segs, key=lambda s: len(s["text"].split()))
    start
