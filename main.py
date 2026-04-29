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
    start     = max(0, best["start"] - 2)
    end       = min(best["end"] + 10, start + 59)
    full_text = " ".join(s["text"] for s in segs)
    print(f"   ✅ Moment: {start:.0f}s → {end:.0f}s")
    return start, end, full_text


# ── CAPTIONS ─────────────────────────────────────────
def fmt_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02}:{s:05.2f}"


def make_captions(path, start, end, index):
    print("   📝 Making Cyber Yellow captions...")
    model  = whisper.load_model("tiny")
    result = model.transcribe(path)

    # ── CAPTION STYLE ──────────────────────────────
    # Font    : Arial Narrow
    # Style   : Bold + Italic
    # Size    : 22
    # Color   : Cyber Yellow
    # Outline : Black 3px
    # Shadow  : 1px
    # BG      : Transparent
    # Position: Bottom center
    # Text    : ALL CAPS
    # ───────────────────────────────────────────────
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial Narrow,22,&H0000E6FF,"
        "&H000000FF,&H00000000,&H00000000,1,1,0,0,100,"
        "100,2,0,1,3,1,2,10,10,150,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines = []
    for seg in result["segments"]:
        if seg["end"] < start or seg["start"] > end:
            continue
        s    = max(0, seg["start"] - start)
        e    = min(end - start, seg["end"] - start)
        text = seg["text"].strip().upper()
        lines.append(
            f"Dialogue: 0,{fmt_time(s)},{fmt_time(e)},"
            f"Default,,0,0,0,,{text}"
        )

    cap = f"/tmp/caps_{index}.ass"
    with open(cap, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines))
    print("   ✅ Captions ready")
    return cap


# ── CUT CLIP + LOGO + CAPTIONS ───────────────────────
def make_clip(path, start, end, index):
    print("   ✂️  Cutting clip + logo + captions...")
    caps = make_captions(path, start, end, index)
    out  = f"/tmp/clip_{index}.mp4"

    if os.path.exists(LOGO_PATH):
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-to", str(end),
            "-i", path,
            "-i", LOGO_PATH,
            "-filter_complex",
            (
                "[0:v]scale=1080:1920"
                ":force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"ass={caps}[base];"
                "[1:v]scale=120:-1[logo];"
                "[base][logo]overlay=W-w-20:20"
            ),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            out
        ]
        print("   🖼️  Logo: top right")
    else:
        print("   ⚠️  logo.png not found — no logo")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-to", str(end),
            "-i", path,
            "-vf",
            (
                "scale=1080:1920"
                ":force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
                f"ass={caps}"
            ),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            out
        ]

    subprocess.run(cmd, check=True)
    print(f"   ✅ Clip {index} ready")
    return out


# ── UPLOAD SCHEDULED ─────────────────────────────────
def upload_scheduled(clip, metadata, publish_time, slot):
    print(f"   ⬆️  Scheduling Short {slot}...")
    creds = Credentials.from_authorized_user_info(
        json.loads(TOKEN_JSON)
    )
    yt = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title":       metadata["title"][:90] + " #Shorts",
            "description": metadata["description"],
            "tags":        metadata["tags"],
            "categoryId":  "22"
        },
        "status": {
            "privacyStatus":           "private",
            "publishAt":               publish_time,
            "selfDeclaredMadeForKids": False
        }
    }
    media = MediaFileUpload(
        clip, mimetype="video/mp4", resumable=True
    )
    r = yt.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    ).execute()
    print(f"   ✅ Short {slot} scheduled!")
    print(f"   📅 Goes live: {publish_time}")
    print(f"   🔗 youtube.com/watch?v={r['id']}")


# ── CLEANUP ──────────────────────────────────────────
def cleanup(index):
    for f in [
        f"/tmp/input_{index}.mp4",
        f"/tmp/clip_{index}.mp4",
        f"/tmp/caps_{index}.ass"
    ]:
        if os.path.exists(f):
            os.remove(f)
    print(f"   🧹 Temp files {index} deleted")


# ── MAIN ─────────────────────────────────────────────
def run():
    print("=" * 58)
    print("   🚀 PODCAST CLIPPER BOT STARTED")
    print(f"   🕒 {datetime.now(IST).strftime('%I:%M %p IST')}")
    print(f"   🔍 Search: {SEARCH}")
    print("   🎨 Cyber Yellow · Bold Italic · Size 22")
    print("   🤖 AI: Google Gemini Free")
    print("=" * 58)

    # Setup cookies first
    setup_cookies()

    slot1_time, slot2_time = get_schedule_times()
    print(f"\n📅 Short 1 → 4:00 PM IST")
    print(f"📅 Short 2 → 4:15 PM IST")

    videos = search_podcasts()
    clips  = []

    for vid_id, title in videos:
        if len(clips) == 2:
            break
        print(f"\n📹 Checking: {title[:52]}...")
        if already_uploaded(title[:25]):
            continue
        index                  = len(clips) + 1
        path                   = download_video(vid_id, index)
        start, end, transcript = find_best_moment(path)
        clip                   = make_clip(path, start, end, index)
        metadata               = generate_ai_metadata(
                                     transcript, index
                                 )
        clips.append((clip, metadata))
        print(f"   ✅ Short {index} prepared")

    if not clips:
        print("\nℹ️  All videos already uploaded today.")
        return

    print(f"\n📤 Scheduling Short 1 → 4:00 PM IST")
    upload_scheduled(clips[0][0], clips[0][1], slot1_time, 1)
    cleanup(1)

    if len(clips) == 2:
        print(f"\n📤 Scheduling Short 2 → 4:15 PM IST")
        upload_scheduled(clips[1][0], clips[1][1], slot2_time, 2)
        cleanup(2)

    print("\n" + "=" * 58)
    print("   ✅ BOTH SHORTS SCHEDULED!")
    print("   📅 Short 1 → 4:00 PM IST")
    print("   📅 Short 2 → 4:15 PM IST")
    print("=" * 58)


if __name__ == "__main__":
    run()
