from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import os
import uuid
import asyncio
from pathlib import Path

app = FastAPI(title="MediaVault Pro API", version="1.0.0")

# CORS — allow frontend to call backend
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

# ── HELPERS ────────────────────────────────────────────
def get_base_ydl_opts():
    """Cookie aur basic safety har request mein inject karne ke liye"""
    opts = {
        "quiet": True,
        "no_warnings": True,
    }
    # 🔥 FIX 4: Safety Check for Cookies
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"
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
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def format_views(n: int) -> str:
    if not n:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# ── MODELS ────────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    max_results: int = 8

class DownloadRequest(BaseModel):
    url: str
    format: str  # "mp4" | "mp3"
    quality: str = "best"  # "best" | "1080" | "720" | "480"


# ── SEARCH YOUTUBE ────────────────────────────────────
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

        if not result or "entries" not in result:
             return {"error": "No results found or search blocked."}

        videos = []
        for entry in result.get("entries", []):
            if not entry:
                continue
            videos.append({
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                "thumbnail": entry.get("thumbnail") or f"https://img.youtube.com/vi/{entry.get('id')}/hqdefault.jpg",
                "duration": format_duration(entry.get("duration", 0)),
                "duration_sec": entry.get("duration", 0),
                "channel": entry.get("uploader") or entry.get("channel"),
                "views": format_views(entry.get("view_count", 0)),
                "upload_date": entry.get("upload_date"),
            })

        return {"results": videos, "query": req.query}

    except Exception as e:
        return {"error": str(e), "message": "Search failed due to backend error"}


# ── VIDEO INFO ────────────────────────────────────────
@app.get("/api/info")
async def get_video_info(url: str):
    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        "skip_download": True,
        "ignoreerrors": True,  
        "format": "bv*+ba/b",  
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
        if not info:
            return {"error": "Failed to extract info. Video might be restricted, age-gated, or unavailable."}

        formats = []
        seen = set()
        for f in info.get("formats", []):
            height = f.get("height")
            ext = f.get("ext")
            vcodec = f.get("vcodec")
            
            if height and ext == "mp4" and vcodec != 'none' and height not in seen:
                seen.add(height)
                formats.append({
                    "format_id": f["format_id"],
                    "quality": f"{height}p",
                    "ext": ext,
                    "filesize": f.get("filesize") or f.get("filesize_approx"),
                })
        
        if not formats:
            formats.append({
                "format_id": "bestaudio",
                "quality": "Audio Only / Auto",
                "ext": "mp4",
                "filesize": None
            })

        return {
            "id": info.get("id"),
            "title": info.get("title"),
            "description": (info.get("description") or "")[:300],
            "thumbnail": info.get("thumbnail"),
            "duration": format_duration(info.get("duration", 0)),
            "duration_sec": info.get("duration", 0),
            "channel": info.get("uploader"),
            "views": format_views(info.get("view_count", 0)),
            "likes": format_views(info.get("like_count", 0)),
            "upload_date": info.get("upload_date"),
            "formats": sorted(formats, key=lambda x: int(str(x["quality"]).replace("p","").replace("Audio Only / Auto", "0")), reverse=True),
        }

    except Exception as e:
        return {"error": str(e), "message": "Backend yt-dlp extraction failed"}


# ── DOWNLOAD VIDEO (THE REAL FIX APPLIED) ──────────────────────────────
@app.post("/api/download/video")
async def download_video(req: DownloadRequest):
    file_id = str(uuid.uuid4())
    output_template = DOWNLOAD_DIR / f"{file_id}.%(ext)s"

    # 🔥 FIX 1 & 2: Safe Parsing and No Extension Restriction on Download
    if req.quality in ["best", "Audio Only / Auto"]:
        format_str = "bv*+ba/b"
    else:
        try:
            # Agar frontend "720p" bhejta hai, toh "p" hatakar safe int banayega
            q = int(str(req.quality).replace("p", ""))
            format_str = f"bv*[height<={q}]+ba/bv*+ba/b"
        except:
            format_str = "bv*+ba/b"

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        "format": format_str,
        "outtmpl": str(output_template),
        "merge_output_format": "mp4", # Yeh final output ko MP4 banayega, chahe webm kyun na download ho
    })

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _do_download(req.url, ydl_opts))

        final_path = DOWNLOAD_DIR / f"{file_id}.mp4"
        if not final_path.exists():
            mkv_path = DOWNLOAD_DIR / f"{file_id}.mkv"
            if mkv_path.exists():
                final_path = mkv_path
            else:
                return {"error": "Video conversion failed at backend"}

        return FileResponse(
            path=str(final_path),
            filename=f"mediavault_{file_id[:8]}.mp4",
            media_type="video/mp4",
        )

    except Exception as e:
        return {"error": str(e), "message": "Download failed due to yt-dlp error"}


# ── DOWNLOAD AUDIO ──────────────────────────────
@app.post("/api/download/audio")
async def download_audio(req: DownloadRequest):
    file_id = str(uuid.uuid4())
    output_template = DOWNLOAD_DIR / f"{file_id}.%(ext)s"
    audio_ext = "m4a" if req.quality == "m4a" else "mp3"

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        "format": "bestaudio/best",
        "outtmpl": str(output_template),
        "writethumbnail": True,  
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": audio_ext,
                "preferredquality": "192",
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail", "already_have_thumbnail": False},
        ],
    })

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _do_download(req.url, ydl_opts))

        final_path = DOWNLOAD_DIR / f"{file_id}.{audio_ext}"
        if not final_path.exists():
             return {"error": f"{audio_ext.upper()} conversion failed at backend"}

        return FileResponse(
            path=str(final_path),
            filename=f"mediavault_{file_id[:8]}.{audio_ext}",
            media_type=f"audio/{'mp4' if audio_ext == 'm4a' else 'mpeg'}",
        )

    except Exception as e:
         return {"error": str(e), "message": "Audio download failed due to yt-dlp error"}


# ── INSTAGRAM DOWNLOAD ────────────────────────────────
@app.post("/api/download/instagram")
async def download_instagram(req: DownloadRequest):
    file_id = str(uuid.uuid4())
    output_path = DOWNLOAD_DIR / f"{file_id}.mp4"

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        "format": "bv*+ba/b",
        "outtmpl": str(output_path),
    })

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _do_download(req.url, ydl_opts))

        if not output_path.exists():
            return {"error": "Instagram download failed at backend"}

        return FileResponse(
            path=str(output_path),
            filename=f"reel_{file_id[:8]}.mp4",
            media_type="video/mp4",
        )

    except Exception as e:
        return {"error": str(e), "message": "Instagram download failed due to yt-dlp error"}


# ── HEALTH CHECK ──────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "MediaVault Pro API is running", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"ok": True}
