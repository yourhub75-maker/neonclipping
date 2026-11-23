import hashlib
import http.cookiejar
import json
import os
import re
import tempfile
import time
from pathlib import Path
import requests

CLIPS_DIR = Path("clips")
UPLOADED_DB = Path("uploaded_clips.json")


def load_cookies():
    raw = os.environ.get("YT_COOKIES", "").strip()
    if not raw:
        return None
    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt", encoding="utf-8")
    tmp.write(raw)
    tmp.close()
    cj = http.cookiejar.MozillaCookieJar(tmp.name)
    cj.load(ignore_discard=True, ignore_expires=True)
    os.unlink(tmp.name)
    return cj


def sapisid_hash(sapisid, origin="https://studio.youtube.com"):
    ts = str(int(time.time()))
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def get_studio_context(session):
    resp = session.get("https://studio.youtube.com", timeout=20)
    html = resp.text
    channel_match = re.search(r'"channelId":"(UC[^"]+)"', html)
    api_key_match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    client_ver_match = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', html)
    if not (channel_match and api_key_match):
        raise RuntimeError("YouTube Studio cookies expired ya invalid hain.")
    return {
        "channel_id": channel_match.group(1),
        "api_key": api_key_match.group(1),
        "client_version": client_ver_match.group(1) if client_ver_match else "1.20261101.01.00",
    }


def upload_short(video_path: Path):
    cj = load_cookies()
    if not cj:
        print("[AutoYT] YT_COOKIES env variable empty hai, skip kar rahe hain.")
        return False

    session = requests.Session()
    session.cookies = cj
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Origin": "https://studio.youtube.com",
        "Referer": "https://studio.youtube.com/",
    })

    sapisid = next((c.value for c in cj if c.name in ("SAPISID", "__Secure-3PAPISID")), None)
    if not sapisid:
        raise RuntimeError("SAPISID cookie nahi mili—dubara Studio login karke cookies export karein.")

    ctx = get_studio_context(session)
    auth_header = sapisid_hash(sapisid)
    file_size = video_path.stat().st_size
    title = video_path.stem.replace("_", " ").replace("-", " ").strip()[:85] + " #Shorts"

    # 1. Start Resumable Upload Session
    init_headers = {
        "Authorization": auth_header,
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": "video/mp4",
        "Content-Type": "application/json",
    }
    init_res = session.post(
        "https://upload.youtube.com/upload/studio",
        headers=init_headers,
        json={"frontendUploadId": f"nc_{int(time.time())}"},
        timeout=30,
    )
    upload_url = init_res.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError(f"Upload URL nahi mila: {init_res.status_code}")

    # 2. Stream Video Bytes (Low RAM usage)
    with open(video_path, "rb") as f:
        up_res = session.post(
            upload_url,
            headers={
                "Authorization": auth_header,
                "X-Goog-Upload-Command": "upload, finalize",
                "X-Goog-Upload-Offset": "0",
                "Content-Type": "video/mp4",
            },
            data=f,
            timeout=180,
        )
    scotty_id = up_res.json().get("scottyResourceId")
    if not scotty_id:
        raise RuntimeError(f"Video bytes upload fail: {up_res.text[:200]}")

    # 3. Create & Publish Short via YouTube Studio InnerTube API
    create_payload = {
        "context": {
            "client": {
                "clientName": "WEB_CREATOR",
                "clientVersion": ctx["client_version"],
            }
        },
        "channelId": ctx["channel_id"],
        "resourceId": {"scottyResourceId": {"id": scotty_id}},
        "frontendUploadId": f"nc_{int(time.time())}",
        "initialMetadata": {
            "title": {"newTitle": title},
            "description": {"newDescription": f"{title}\n\n#Shorts #LiveClips"},
            "privacy": {"newPrivacy": "PUBLIC"},
            "madeForKids": {"newTfda": "NOT_MADE_FOR_KIDS"},
        },
    }
    pub_res = session.post(
        f"https://studio.youtube.com/youtubei/v1/upload/createvideo?key={ctx['api_key']}",
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
        json=create_payload,
        timeout=30,
    )
    if pub_res.status_code == 200:
        print(f"[AutoYT] Uploaded & Published: {title}")
        return True
    raise RuntimeError(f"Publish fail ({pub_res.status_code}): {pub_res.text[:200]}")


def watch_loop():
    print("[AutoYT] Lightweight Cookie Watcher chalu ho gaya...")
    done = set(json.loads(UPLOADED_DB.read_text())) if UPLOADED_DB.exists() else set()

    while True:
        try:
            if CLIPS_DIR.exists():
                for clip in sorted(CLIPS_DIR.glob("*.mp4")):
                    if clip.name in done:
                        continue
                    # Wait 3 sec to ensure ffmpeg finished writing file
                    size1 = clip.stat().st_size
                    time.sleep(3)
                    if size1 == 0 or clip.stat().st_size != size1:
                        continue

                    if upload_short(clip):
                        done.add(clip.name)
                        UPLOADED_DB.write_text(json.dumps(list(done)))
                        # Delete clip after upload so 512MB Render storage never fills up
                        clip.unlink(missing_ok=True)
        except Exception as e:
            print(f"[AutoYT Error] {e}")
        time.sleep(10)


if __name__ == "__main__":
    watch_loop()
