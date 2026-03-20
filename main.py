from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import os
import uuid
import asyncio
from pathlib import Path

app = FastAPI(title="MediaVault Pro API", version="2.0.0")

# ── CORS ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://aneekpal01.github.io",
        "http://localhost:8000",
        "http://localhost:3000"
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)


# ── HELPERS ─────────────────────────────────────────
def get_base_ydl_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        }
    }

    cookie_path = Path(__file__).parent / "cookies.txt"
    if cookie_path.exists():
        opts["cookiefile"] = str(cookie_path)

    return opts


def _do_download(url: str, opts: dict):
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def format_duration(seconds: int) -> str:
    if not seconds:
        return "0:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_views(n: int) -> str:
    if not n:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# ── MODELS ──────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    max_results: int = 8


class DownloadRequest(BaseModel):
    url: str
    format: str
    quality: str = "best"


# ── SEARCH ──────────────────────────────────────────
@app.post("/api/search")
async def search_youtube(req: SearchRequest):
    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        "extract_flat": True,
        "default_search": f"ytsearch{req.max_results}",
        "skip_download": True,
        "ignoreerrors": True,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch{req.max_results}:{req.query}", download=False)

        videos = []
        for entry in result.get("entries", []):
            if not entry:
                continue
            videos.append({
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                "thumbnail": entry.get("thumbnail"),
                "duration": format_duration(entry.get("duration", 0)),
                "channel": entry.get("uploader"),
                "views": format_views(entry.get("view_count", 0)),
            })

        return {"results": videos}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── INFO (FIXED) ────────────────────────────────────
@app.get("/api/info")
async def get_video_info(url: str):
    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        "skip_download": True,
        "ignoreerrors": False,
        # ❌ NO FORMAT HERE
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen = set()

        for f in info.get("formats", []):
            height = f.get("height")
            vcodec = f.get("vcodec")

            if height and vcodec != "none" and height not in seen:
                seen.add(height)
                formats.append({
                    "format_id": f["format_id"],
                    "quality": f"{height}p",
                    "filesize": f.get("filesize") or f.get("filesize_approx"),
                })

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": format_duration(info.get("duration", 0)),
            "formats": sorted(formats, key=lambda x: int(x["quality"].replace("p", "")), reverse=True),
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")


# ── DOWNLOAD VIDEO ─────────────────────────────────
@app.post("/api/download/video")
async def download_video(req: DownloadRequest):
    file_id = str(uuid.uuid4())
    output_template = DOWNLOAD_DIR / f"{file_id}.%(ext)s"

    if req.quality == "best":
        format_str = "bv*+ba/b"
    else:
        try:
            q = int(req.quality.replace("p", ""))
            format_str = f"bv*[height<={q}]+ba/b"
        except:
            format_str = "bv*+ba/b"

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        "format": format_str,
        "outtmpl": str(output_template),
        "merge_output_format": "mp4",
    })

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _do_download(req.url, ydl_opts))

        file_path = DOWNLOAD_DIR / f"{file_id}.mp4"
        if not file_path.exists():
            raise HTTPException(status_code=500, detail="Download failed")

        return FileResponse(str(file_path), media_type="video/mp4")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── HEALTH ─────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "OK"}
