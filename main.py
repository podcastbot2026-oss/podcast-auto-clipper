import os, subprocess, glob, re, json, time, urllib.request
from datetime import datetime
import pytz
from faster_whisper import WhisperModel
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
BASE_PATH    = f"/home/runner/work/{REPO_NAME}/{REPO_NAME}"
LOGO_PATH    = f"{BASE_PATH}/logo.png"
SEARCH       = "raj shamani viral podcast"
IST          = pytz.timezone("Asia/Kolkata")
COOKIES_PATH = "/tmp/cookies.txt"

# Caption style
OUTPUT_W, OUTPUT_H = 1080, 1920
FONT_NAME          = "Arial Black"
FONT_SIZE          = 85
WORDS_PER_CHUNK    = 3
CAPTION_MARGIN_V   = 190
OUTLINE_SIZE       = 4
HIGHLIGHT_COLOR    = "&H0000FFFF&"
# ══════════════════════════════════════════════════════

RUN_START = time.time()
def elapsed(): return f"[{time.time()-RUN_START:.0f}s]"


# ── SETUP COOKIES ────────────────────────────────────
def setup_cookies():
    if YT_COOKIES:
        with open(COOKIES_PATH, "w") as f:
            f.write(YT_COOKIES)
        print(f"{elapsed()} 🍪 Cookies loaded")
    else:
        print(f"{elapsed()} ⚠️  No cookies")


# ── SCHEDULE TIMES ───────────────────────────────────
def get_schedule_times():
    now   = datetime.now(IST)
    d     = now.date()
    slot1 = IST.localize(datetime(d.year, d.month, d.day, 16,  0, 0))
    slot2 = IST.localize(datetime(d.year, d.month, d.day, 16, 15, 0))
    return slot1.isoformat(), slot2.isoformat()


# ── SEARCH ───────────────────────────────────────────
def search_podcasts():
    print(f"{elapsed()} 🔍 Searching: {SEARCH}")
    yt  = build("youtube", "v3", developerKey=API_KEY)
    res = yt.search().list(
        q=SEARCH, part="snippet", type="video",
        order="viewCount", videoDuration="long",
        maxResults=20
    ).execute()
    results = [
        (item["id"]["videoId"], item["snippet"]["title"])
        for item in res["items"]
    ]
    print(f"{elapsed()}    Found {len(results)} videos")
    return results


# ── DUPLICATE CHECK ──────────────────────────────────
def already_uploaded(keyword):
    yt  = build("youtube", "v3", developerKey=API_KEY)
    res = yt.search().list(
        q=keyword, part="snippet", type="video",
        channelId=CHANNEL_ID, maxResults=3
    ).execute()
    exists = len(res.get("items", [])) > 0
    if exists:
        print(f"{elapsed()}    ⏭  Already uploaded — skipping")
    return exists


# ── DOWNLOAD (your v5.0 method) ──────────────────────
def download_video(video_id):
    print(f"{elapsed()} 📥 Downloading...")

    # Clean old files
    for f in glob.glob("/tmp/source.*"):
        try: os.remove(f)
        except: pass

    clean_url = f"https://www.youtube.com/watch?v={video_id}"

    FMT = (
        "bestvideo[height<=1080][height>=720][ext=mp4]"
        "+bestaudio[ext=m4a]"
        "/bestvideo[height<=1080][height>=720]+bestaudio"
        "/bestvideo[height>=720]+bestaudio"
        "/bestvideo+bestaudio"
        "/best"
    )

    cmd = [
        "yt-dlp", "-f", FMT,
        "-o", "/tmp/source.%(ext)s",
        "--no-playlist",
        "--merge-output-format", "mp4",
        "--postprocessor-args", "ffmpeg:-c copy",
        "--concurrent-fragments", "8",
        "--retries", "3",
        "--fragment-retries", "3",
        "--http-headers",
        "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36",
    ]

    # Add cookies if available
    if YT_COOKIES and os.path.exists(COOKIES_PATH):
        cmd += ["--cookies", COOKIES_PATH]

    subprocess.run(cmd, capture_output=True, text=True)

    src = [
        f for f in glob.glob("/tmp/source.*")
        if os.path.exists(f) and os.path.getsize(f) > 5_000_000
    ]

    # Fallback
    if not src:
        print(f"{elapsed()}    ⚠️  Fallback download...")
        cmd2 = [
            "yt-dlp", "-f", "bestvideo+bestaudio/best",
            "-o", "/tmp/source.%(ext)s",
            "--no-playlist",
            "--merge-output-format", "mp4",
            "--postprocessor-args", "ffmpeg:-c copy",
        ]
        if YT_COOKIES and os.path.exists(COOKIES_PATH):
            cmd2 += ["--cookies", COOKIES_PATH]
        subprocess.run(cmd2, capture_output=True)
        src = [
            f for f in glob.glob("/tmp/source.*")
            if os.path.exists(f) and os.path.getsize(f) > 1_000_000
        ]

    if not src:
        raise Exception("Download failed")

    source = src[0]
    mb     = os.path.getsize(source) // 1_000_000
    print(f"{elapsed()}    ✅ Downloaded {mb}MB → {source}")
    return source


# ── VIDEO HELPERS ────────────────────────────────────
def video_dims(path):
    r = subprocess.run(
        ["ffprobe","-v","quiet","-print_format","json",
         "-show_streams", path],
        capture_output=True, text=True)
    try:
        for s in json.loads(r.stdout).get("streams",[]):
            if s.get("codec_type") == "video":
                return int(s["width"]), int(s["height"])
    except: pass
    return 1920, 1080

def audio_channels(path):
    r = subprocess.run(
        ["ffprobe","-v","quiet","-print_format","json",
         "-show_streams", path],
        capture_output=True, text=True)
    try:
        for s in json.loads(r.stdout).get("streams",[]):
            if s.get("codec_type") == "audio":
                return int(s.get("channels", 1))
    except: pass
    return 1

def get_duration(path):
    r = subprocess.run(
        ["ffprobe","-v","quiet","-show_entries",
         "format=duration","-of","csv=p=0", path],
        capture_output=True, text=True)
    try:    return float(r.stdout.strip())
    except: return 3600

def mean_volume(path, pan):
    r = subprocess.run(
        ["ffmpeg","-y","-i", path,
         "-af", f"pan=mono|c0={pan},volumedetect",
         "-f","null","-"],
        capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*([-\d.]+)", r.stderr)
    return float(m.group(1)) if m else -100.0

def speaker_side(path):
    lv = mean_volume(path, "FL")
    rv = mean_volume(path, "FR")
    diff = lv - rv
    if diff > 2:  return "left"
    if diff < -2: return "right"
    return "center"

def build_crop(src_w, src_h, side):
    crop_w = min(int(src_h * 9/16), src_w)
    half   = crop_w // 2
    if side == "left":
        x = max(0, src_w//4 - half)
    elif side == "right":
        x = min(src_w - crop_w, 3*src_w//4 - half)
    else:
        x = (src_w - crop_w) // 2
    return f"crop={crop_w}:{src_h}:{x}:0,scale={OUTPUT_W}:{OUTPUT_H}"


# ── FIND 2 MOMENTS ───────────────────────────────────
def find_two_moments(duration):
    third = duration // 3
    s1    = max(0, third - 30)
    e1    = s1 + 50
    s2    = max(e1 + 60, 2 * third)
    e2    = s2 + 50
    if e2 > duration:
        s2 = max(e1 + 60, duration - 55)
        e2 = s2 + 50
    return (s1, e1), (s2, e2)


# ── ASS CAPTIONS (your v5.0) ─────────────────────────
def sec_to_ass(s):
    h=int(s//3600); m=int((s%3600)//60); sec=s%60
    return f"{h}:{m:02d}:{sec:05.2f}"

def make_ass(word_segs):
    header = (
        f"[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {OUTPUT_W}\nPlayResY: {OUTPUT_H}\n"
        f"ScaledBorderAndShadow: yes\nWrapStyle: 0\n\n"
        f"[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, "
        f"SecondaryColour, OutlineColour, BackColour, Bold, "
        f"Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        f"Spacing, Angle, BorderStyle, Outline, Shadow, "
        f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{FONT_NAME},{FONT_SIZE},"
        f"&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,2,0,1,{OUTLINE_SIZE},2,2,"
        f"60,60,{CAPTION_MARGIN_V},1\n\n"
        f"[Events]\n"
        f"Format: Layer, Start, End, Style, Name, "
        f"MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    i = 0
    while i < len(word_segs):
        chunk  = word_segs[i:i+WORDS_PER_CHUNK]
        t0, t1 = chunk[0]["start"], chunk[-1]["end"]
        if t1 - t0 >= 0.05:
            words = [
                w["word"].strip().upper()
                for w in chunk if w["word"].strip()
            ]
            if words:
                if len(words) >= 2:
                    words[-1] = (
                        f"{{\\c{HIGHLIGHT_COLOR}}}"
                        f"{words[-1]}"
                        f"{{\\c&H00FFFFFF&}}"
                    )
                events.append(
                    f"Dialogue: 0,{sec_to_ass(t0)},"
                    f"{sec_to_ass(t1)},Default,,0,0,0,,"
                    f"{' '.join(words)}"
                )
        i += WORDS_PER_CHUNK
    return header + "\n".join(events) + "\n"


# ── TRANSCRIBE (your v5.0) ───────────────────────────
def transcribe_clip(model, clip_path):
    print(f"{elapsed()}    🎤 Transcribing...")
    segs, info = model.transcribe(
        clip_path,
        beam_size=3,
        vad_filter=True,
        word_timestamps=True,
        task="translate",
        language="hi",
    )
    word_segs = []
    full_text = []
    for seg in segs:
        full_text.append(seg.text)
        if seg.words:
            for w in seg.words:
                wd = w.word.strip()
                if wd and not re.match(r'^[^\w]+$', wd):
                    word_segs.append({
                        "word":  wd,
                        "start": w.start,
                        "end":   w.end
                    })
        else:
            words = [w for w in seg.text.strip().split() if w]
            if words:
                d = (seg.end - seg.start) / len(words)
                for k, w in enumerate(words):
                    word_segs.append({
                        "word":  w,
                        "start": seg.start + k * d,
                        "end":   seg.start + (k+1) * d
                    })
    transcript = " ".join(full_text)
    print(f"{elapsed()}    ✅ {len(word_segs)} words")
    return word_segs, transcript


# ── MAKE CLIP + LOGO ─────────────────────────────────
def make_clip(source, start, end, word_segs, vf_crop, index):
    print(f"{elapsed()}    ✂️  Making clip {index}...")
    raw = f"/tmp/raw_{index}.mp4"
    ass = f"/tmp/caps_{index}.ass"
    out = f"/tmp/clip_{index}.mp4"
    dur = str(int(end - start))

    # Extract raw clip
    subprocess.run(
        ["ffmpeg","-y","-ss",str(start),"-t",dur,
         "-i",source,"-c","copy", raw],
        capture_output=True
    )

    # Write captions
    with open(ass,"w",encoding="utf-8") as f:
        f.write(make_ass(word_segs))

    vf_full = f"{vf_crop},ass={ass}"

    if os.path.exists(LOGO_PATH):
        cmd = [
            "ffmpeg","-y","-i", raw,"-i", LOGO_PATH,
            "-filter_complex",
            (
                f"[0:v]{vf_full}[base];"
                "[1:v]scale=120:-1[logo];"
                "[base][logo]overlay=W-w-20:20"
            ),
            "-c:v","libx264","-preset","fast","-crf","17",
            "-maxrate","4000k","-bufsize","8000k",
            "-profile:v","high","-level","4.1",
            "-c:a","aac","-b:a","192k","-ar","44100",
            "-r","30","-pix_fmt","yuv420p",
            "-movflags","+faststart", out
        ]
        print(f"{elapsed()}    🖼️  Logo: top right")
    else:
        cmd = [
            "ffmpeg","-y","-i", raw,
            "-vf", vf_full,
            "-c:v","libx264","-preset","fast","-crf","17",
            "-maxrate","4000k","-bufsize","8000k",
            "-c:a","aac","-b:a","192k","-ar","44100",
            "-r","30","-pix_fmt","yuv420p",
            "-movflags","+faststart", out
        ]

    res = subprocess.run(cmd, capture_output=True, text=True)

    if not (os.path.exists(out) and os.path.getsize(out) > 200_000):
        print(f"{elapsed()}    ⚠️  Retrying without captions...")
        subprocess.run([
            "ffmpeg","-y","-i", raw,"-vf", vf_crop,
            "-c:v","libx264","-preset","fast","-crf","17",
            "-c:a","aac","-b:a","192k","-r","30",
            "-pix_fmt","yuv420p", out,
        ], capture_output=True)

    if os.path.exists(raw): os.remove(raw)
    mb = os.path.getsize(out)//1_000_000 if os.path.exists(out) else 0
    print(f"{elapsed()}    ✅ Clip {index} ready — {mb}MB")
    return out


# ── GEMINI AI METADATA ───────────────────────────────
def generate_ai_metadata(transcript, slot):
    print(f"{elapsed()}    🤖 Gemini metadata for Short {slot}...")
    prompt = f"""You are a YouTube Shorts expert.
Based on this podcast transcript generate:
1. Viral catchy title (max 90 chars, no clickbait)
2. Compelling description (3-4 lines, hashtags at end)
3. 10 relevant tags as JSON array

Transcript: {transcript[:1500]}

Reply ONLY in this exact JSON, nothing else:
{{
  "title": "your title here",
  "description": "your description here",
  "tags": ["tag1","tag2","tag3","tag4","tag5",
           "tag6","tag7","tag8","tag9","tag10"]
}}"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 500}
    }).encode("utf-8")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    )
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            raw  = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            raw  = raw.replace("```json","").replace("```","").strip()
            meta = json.loads(raw)
            print(f"{elapsed()}    ✅ Title: {meta['title']}")
            return meta
    except Exception as e:
        print(f"{elapsed()}    ⚠️  Gemini failed ({e}) — default")
        return {
            "title":       "Raj Shamani Best Moment You Must Watch",
            "description": "Best moment from Raj Shamani!\n\n#Shorts #RajShamani #Podcast #Viral",
            "tags":        ["rajshamani","podcast","shorts","viral","motivation",
                           "clips","interview","trending","fyp","podcastclips"]
        }


# ── UPLOAD SCHEDULED ─────────────────────────────────
def upload_scheduled(clip, metadata, publish_time, slot):
    print(f"{elapsed()}    ⬆️  Scheduling Short {slot}...")
    creds = Credentials.from_authorized_user_info(json.loads(TOKEN_JSON))
    yt    = build("youtube", "v3", credentials=creds)
    body  = {
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
    media = MediaFileUpload(clip, mimetype="video/mp4", resumable=True)
    r     = yt.videos().insert(
        part="snippet,status", body=body, media_body=media
    ).execute()
    print(f"{elapsed()}    ✅ Scheduled!")
    print(f"{elapsed()}    📅 Goes live: {publish_time}")
    print(f"{elapsed()}    🔗 youtube.com/watch?v={r['id']}")


# ── CLEANUP ──────────────────────────────────────────
def cleanup():
    for pat in ["/tmp/source.*","/tmp/raw_*.mp4",
                "/tmp/clip_*.mp4","/tmp/caps_*.ass",
                "/tmp/raw_t*.mp4"]:
        for f in glob.glob(pat):
            try: os.remove(f)
            except: pass
    print(f"{elapsed()} 🧹 Cleaned")


# ── MAIN ─────────────────────────────────────────────
def run():
    print("="*58)
    print("   🚀 PODCAST CLIPPER BOT")
    print(f"   🕒 {datetime.now(IST).strftime('%I:%M %p IST')}")
    print(f"   🔍 {SEARCH}")
    print("="*58)

    setup_cookies()
    slot1_time, slot2_time = get_schedule_times()

    # Load Whisper once
    print(f"\n{elapsed()} 🎤 Loading Whisper base...")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    print(f"{elapsed()} ✅ Whisper ready")

    # Find a video that downloads successfully
    videos = search_podcasts()
    source = None
    title  = ""

    for vid_id, vid_title in videos:
        if already_uploaded(vid_title[:25]):
            continue
        try:
            source = download_video(vid_id)
            title  = vid_title
            break
        except Exception as e:
            print(f"{elapsed()}    ⚠️  Skipping — {e}")
            continue

    if not source:
        print("\n❌ No video could be downloaded today.")
        return

    # Video properties
    src_w, src_h = video_dims(source)
    n_ch         = audio_channels(source)
    is_landscape = src_w > src_h
    duration     = get_duration(source)
    print(f"{elapsed()} 📐 {src_w}×{src_h} {n_ch}ch {duration:.0f}s")

    # Speaker crop
    if is_landscape:
        side    = speaker_side(source) if n_ch >= 2 else "center"
        vf_crop = build_crop(src_w, src_h, side)
        print(f"{elapsed()} 🎯 Speaker: {side}")
    else:
        vf_crop = (
            f"scale={OUTPUT_W}:{OUTPUT_H}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black"
        )

    # Find 2 moments
    (s1,e1),(s2,e2) = find_two_moments(duration)

    # Short 1
    print(f"\n{'─'*50}")
    print(f"{elapsed()} ▶ SHORT 1  {s1:.0f}s→{e1:.0f}s")
    raw1 = "/tmp/raw_t1.mp4"
    subprocess.run(
        ["ffmpeg","-y","-ss",str(s1),"-t",str(int(e1-s1)),
         "-i",source,"-c","copy",raw1],
        capture_output=True
    )
    word_segs1, transcript1 = transcribe_clip(model, raw1)
    if os.path.exists(raw1): os.remove(raw1)
    clip1 = make_clip(source, s1, e1, word_segs1, vf_crop, 1)
    meta1 = generate_ai_metadata(transcript1, 1)

    # Short 2
    print(f"\n{'─'*50}")
    print(f"{elapsed()} ▶ SHORT 2  {s2:.0f}s→{e2:.0f}s")
    raw2 = "/tmp/raw_t2.mp4"
    subprocess.run(
        ["ffmpeg","-y","-ss",str(s2),"-t",str(int(e2-s2)),
         "-i",source,"-c","copy",raw2],
        capture_output=True
    )
    word_segs2, transcript2 = transcribe_clip(model, raw2)
    if os.path.exists(raw2): os.remove(raw2)
    clip2 = make_clip(source, s2, e2, word_segs2, vf_crop, 2)
    meta2 = generate_ai_metadata(transcript2, 2)

    # Upload
    print(f"\n{'─'*50}")
    upload_scheduled(clip1, meta1, slot1_time, 1)
    upload_scheduled(clip2, meta2, slot2_time, 2)

    cleanup()

    total = time.time() - RUN_START
    print(f"\n{'='*58}")
    print(f"   ✅ BOTH SHORTS SCHEDULED!")
    print(f"   🎬 {title[:45]}")
    print(f"   📅 Short 1 → 4:00 PM IST")
    print(f"   📅 Short 2 → 4:15 PM IST")
    print(f"   ⏱  {int(total//60)}m {int(total%60)}s")
    print(f"{'='*58}")


if __name__ == "__main__":
    run()
