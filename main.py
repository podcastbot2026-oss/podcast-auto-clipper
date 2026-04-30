import os
import subprocess
import json
import urllib.request
from datetime import datetime
import pytz
import whisper
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

# ══════════════════════════════════════════════════════
API_KEY    = os.environ["YT_API_KEY"]
CHANNEL_ID = os.environ["MY_CHANNEL_ID"]
TOKEN_JSON = os.environ["OAUTH_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
REPO_NAME  = "podcast-auto-clipper"
BASE_PATH  = f"/home/runner/work/{REPO_NAME}/{REPO_NAME}"
LOGO_PATH  = f"{BASE_PATH}/logo.png"
VIDEO_DIR  = f"{BASE_PATH}/videos"
PROGRESS   = f"{BASE_PATH}/progress.json"
IST        = pytz.timezone("Asia/Kolkata")
# ══════════════════════════════════════════════════════


# ── GET TODAY'S VIDEO ────────────────────────────────
def get_todays_video():
    # Read progress
    with open(PROGRESS, "r") as f:
        data = json.load(f)
    last_index = data.get("last_index", 0)

    # Get all videos sorted
    videos = sorted([
        f for f in os.listdir(VIDEO_DIR)
        if f.endswith(".mp4")
    ])

    if not videos:
        print("❌ No videos found in videos/ folder")
        return None, None, None

    total = len(videos)
    print(f"   📁 Total videos: {total}")

    # Pick next video
    next_index = last_index % total
    video_name = videos[next_index]
    video_path = os.path.join(VIDEO_DIR, video_name)

    print(f"   🎬 Today's video: {video_name}")
    print(f"   📌 Index: {next_index + 1} of {total}")

    return video_path, video_name, next_index


# ── SAVE PROGRESS ────────────────────────────────────
def save_progress(index):
    with open(PROGRESS, "r") as f:
        data = json.load(f)
    data["last_index"] = index + 1
    with open(PROGRESS, "w") as f:
        json.dump(data, f)
    print(f"   💾 Progress saved — next run uses video {index + 2}")


# ── SCHEDULE TIMES ───────────────────────────────────
def get_schedule_times():
    now   = datetime.now(IST)
    d     = now.date()
    slot1 = IST.localize(datetime(d.year, d.month, d.day, 16,  0, 0))
    slot2 = IST.localize(datetime(d.year, d.month, d.day, 16, 15, 0))
    return slot1.isoformat(), slot2.isoformat()


# ── AI METADATA USING GEMINI ─────────────────────────
def generate_ai_metadata(transcript, slot):
    print(f"   🤖 Gemini generating metadata for Short {slot}...")

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
        "contents": [{"parts": [{"text": prompt}]}],
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
        url, data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data     = json.loads(resp.read().decode())
            raw      = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            raw      = raw.replace("```json","").replace("```","").strip()
            metadata = json.loads(raw)
            print(f"   ✅ Title: {metadata['title']}")
            return metadata
    except Exception as e:
        print(f"   ⚠️  Gemini failed ({e}) — using default")
        return {
            "title":       "Best Podcast Moment You Must Watch",
            "description": "Amazing podcast clip!\n\n#Shorts #Podcast #Viral #Motivation",
            "tags":        ["podcast","shorts","viral","clips","interview",
                           "motivation","trending","fyp","podcastclips","best"]
        }


# ── FIND 2 BEST MOMENTS FROM ONE VIDEO ───────────────
def find_two_moments(path):
    print("   🎯 Finding 2 best moments from video...")
    model  = whisper.load_model("tiny")
    result = model.transcribe(path)
    segs   = result["segments"]

    if not segs:
        return (60, 119), (300, 359), "No transcript."

    # Sort all segments by word count descending
    sorted_segs = sorted(
        segs,
        key=lambda s: len(s["text"].split()),
        reverse=True
    )

    full_text = " ".join(s["text"] for s in segs)

    # Best moment 1 — highest word count segment
    best1     = sorted_segs[0]
    start1    = max(0, best1["start"] - 2)
    end1      = min(best1["end"] + 10, start1 + 59)

    # Best moment 2 — find another segment far from moment 1
    best2 = None
    for seg in sorted_segs[1:]:
        # Must be at least 90 seconds away from moment 1
        if abs(seg["start"] - start1) > 90:
            best2 = seg
            break

    # Fallback if no second moment found far enough
    if not best2:
        best2 = sorted_segs[1] if len(sorted_segs) > 1 else sorted_segs[0]

    start2 = max(0, best2["start"] - 2)
    end2   = min(best2["end"] + 10, start2 + 59)

    # Make sure moment 2 doesn't overlap moment 1
    if abs(start2 - start1) < 60:
        start2 = end1 + 30
        end2   = start2 + 59

    print(f"   ✅ Moment 1: {start1:.0f}s → {end1:.0f}s")
    print(f"   ✅ Moment 2: {start2:.0f}s → {end2:.0f}s")

    return (start1, end1), (start2, end2), full_text


# ── CAPTIONS ─────────────────────────────────────────
def fmt_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02}:{s:05.2f}"


def make_captions(path, start, end, index):
    print(f"   📝 Making captions for clip {index}...")
    model  = whisper.load_model("tiny")
    result = model.transcribe(path)

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
    print(f"   ✅ Captions {index} ready")
    return cap


# ── MAKE CLIP ─────────────────────────────────────────
def make_clip(path, start, end, index):
    print(f"   ✂️  Making clip {index}...")
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
        f"/tmp/clip_{index}.mp4",
        f"/tmp/caps_{index}.ass"
    ]:
        if os.path.exists(f):
            os.remove(f)
    print(f"   🧹 Temp files {index} deleted")


# ── PUSH PROGRESS TO GITHUB ──────────────────────────
def push_progress():
    try:
        os.system('git config --global user.email "bot@bot.com"')
        os.system('git config --global user.name "PodcastBot"')
        os.system(f"git -C {BASE_PATH} add progress.json")
        os.system(f'git -C {BASE_PATH} commit -m "Update progress"')
        os.system(f"git -C {BASE_PATH} push")
        print("   💾 Progress pushed to GitHub")
    except Exception as e:
        print(f"   ⚠️  Push failed: {e}")


# ── MAIN ─────────────────────────────────────────────
def run():
    print("=" * 58)
    print("   🚀 PODCAST CLIPPER BOT STARTED")
    print(f"   🕒 {datetime.now(IST).strftime('%I:%M %p IST')}")
    print("   🎨 Cyber Yellow · Bold Italic · Size 22")
    print("   🤖 AI: Google Gemini Free")
    print("=" * 58)

    # Get today's video
    video_path, video_name, video_index = get_todays_video()
    if not video_path:
        return

    slot1_time, slot2_time = get_schedule_times()
    print(f"\n📅 Short 1 → 4:00 PM IST")
    print(f"📅 Short 2 → 4:15 PM IST")

    # Find 2 moments from same video
    (s1, e1), (s2, e2), transcript = find_two_moments(video_path)

    # Make clip 1
    print(f"\n🎬 Making Short 1...")
    clip1     = make_clip(video_path, s1, e1, 1)
    metadata1 = generate_ai_metadata(transcript, 1)

    # Make clip 2
    print(f"\n🎬 Making Short 2...")
    clip2     = make_clip(video_path, s2, e2, 2)
    metadata2 = generate_ai_metadata(transcript, 2)

    # Upload both scheduled
    print(f"\n📤 Scheduling Short 1 → 4:00 PM IST")
    upload_scheduled(clip1, metadata1, slot1_time, 1)
    cleanup(1)

    print(f"\n📤 Scheduling Short 2 → 4:15 PM IST")
    upload_scheduled(clip2, metadata2, slot2_time, 2)
    cleanup(2)

    # Save progress — move to next video tomorrow
    save_progress(video_index)
    push_progress()

    print("\n" + "=" * 58)
    print("   ✅ BOTH SHORTS SCHEDULED!")
    print(f"   🎬 From video: {video_name}")
    print("   📅 Short 1 → 4:00 PM IST")
    print("   📅 Short 2 → 4:15 PM IST")
    print("=" * 58)


if __name__ == "__main__":
    run()
