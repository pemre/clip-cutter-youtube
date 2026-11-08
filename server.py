#!/usr/bin/env python3
"""Local clip cutter.

Routes
  GET  /                      -> the UI (index.html)
  GET  /api/preview?url=...    -> prepares a small local copy for scrubbing, returns {src,title}
  POST /api/cut {url,start,end,name} -> starts a server-side cut, returns {id}
  GET  /api/job?id=...         -> {state,progress,file,error}
  GET  /cache/<file>, /out/<file> -> media, with HTTP Range support
"""
import json
import re
import subprocess
import threading
import uuid
import urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
OUT = ROOT / "out"
CACHE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

PREVIEW_FORMAT = "bv*[height<=360]+ba/b"   # small + with audio, for scrubbing
CUT_FORMAT = "bv*[height<=1080]+ba/b"      # final clip quality
VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})")

JOBS = {}
JOBS_LOCK = threading.Lock()


def run_ytdlp(args, timeout=1800):
    return subprocess.run(["yt-dlp", *args], capture_output=True, text=True, timeout=timeout)


def timestamp(seconds):
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    return f"{hours:02d}:{minutes:02d}:{seconds % 60:06.3f}"


def safe_name(name):
    name = Path(str(name or "")).name
    name = re.sub(r"[^\w\-. ()]+", "_", name).strip(" .")
    return (name or "clip")[:90]


def prepare_preview(url, video_id):
    target = CACHE / f"{video_id}.mp4"
    title = ""
    meta = run_ytdlp(["-j", "--no-warnings", "--no-playlist", "--skip-download", url])
    try:
        title = json.loads(meta.stdout.strip().splitlines()[-1]).get("title", "")
    except Exception:
        pass
    if target.exists() and target.stat().st_size > 100_000:
        return target, title
    result = run_ytdlp(["-f", PREVIEW_FORMAT, "--merge-output-format", "mp4", "--no-playlist",
                        "-o", str(CACHE / f"{video_id}.%(ext)s"), url])
    if not target.exists():
        tail = (result.stderr or "download failed").strip().splitlines()[-1:]
        raise RuntimeError(tail[0] if tail else "download failed")
    return target, title


def cut_worker(job_id, url, start, end, name):
    job = JOBS[job_id]
    total = max(0.001, float(end) - float(start))
    try:
        command = [
            "yt-dlp", "--newline", "--no-playlist",
            "--download-sections", f"*{timestamp(start)}-{timestamp(end)}",
            "--force-keyframes-at-cuts",
            "-f", CUT_FORMAT, "--merge-output-format", "mp4",
            "-o", str(OUT / (name + ".%(ext)s")), url,
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        for raw in process.stdout:
            for line in raw.replace("\r", "\n").split("\n"):
                download = re.search(r"\[download\]\s+([\d.]+)%", line)
                if download:
                    job["progress"] = max(job["progress"], int(float(download.group(1)) * 0.3))
                    continue
                clip = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", line)
                if clip:
                    done = int(clip.group(1)) * 3600 + int(clip.group(2)) * 60 + float(clip.group(3))
                    job["progress"] = max(job["progress"], min(99, 30 + int(done / total * 69)))
        if process.wait() != 0:
            raise RuntimeError("yt-dlp failed")
        files = sorted(OUT.glob(name + ".*"), key=lambda p: p.stat().st_mtime)
        if not files:
            raise RuntimeError("no output file")
        with JOBS_LOCK:
            job["file"] = files[-1].name
            job["progress"] = 100
            job["state"] = "done"
    except Exception as exc:
        with JOBS_LOCK:
            job["state"] = "error"
            job["error"] = str(exc)[:300]


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path.startswith("/cache/"):
            return self.serve_media(CACHE / Path(parsed.path).name)
        if parsed.path.startswith("/out/"):
            return self.serve_media(OUT / Path(parsed.path).name)
        if parsed.path == "/api/preview":
            url = query.get("url", [""])[0]
            try:
                match = VIDEO_ID.search(url)
                if not match:
                    raise ValueError("not a YouTube link")
                path, title = prepare_preview(url, match.group(1))
                return self.send_json({"src": f"/cache/{path.name}", "title": title})
            except Exception as exc:
                return self.send_json({"error": str(exc)[:300]})
        if parsed.path == "/api/job":
            job_id = query.get("id", [""])[0]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id) or {"state": "error", "error": "unknown job"})
            job.pop("url", None)
            return self.send_json(job)
        return super().do_GET()

    # ---------- POST ----------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/cut":
            return self.send_error(404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            url = str(payload.get("url", ""))
            if not VIDEO_ID.search(url):
                raise ValueError("not a YouTube link")
            start, end = float(payload.get("start", 0)), float(payload.get("end", 0))
            if end <= start:
                raise ValueError("end must be after start")
            name = safe_name(payload.get("name") or "clip")
            if not name.lower().endswith(".mp4"):
                name += ".mp4"
            name = name[:-4]
        except Exception as exc:
            return self.send_json({"error": str(exc)[:300]})
        job_id = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            JOBS[job_id] = {"state": "running", "progress": 5, "file": None, "error": None}
        threading.Thread(target=cut_worker, args=(job_id, url, start, end, name), daemon=True).start()
        return self.send_json({"id": job_id, "name": name})

    # ---------- helpers ----------
    def send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_media(self, path):
        if not path.exists():
            return self.send_error(404)
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match:
            partial = True
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = min(int(match.group(2)), size - 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = handle.read(min(1 << 18, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)


if __name__ == "__main__":
    print("http://localhost:8917/")
    ThreadingHTTPServer(("0.0.0.0", 8917), Handler).serve_forever()
