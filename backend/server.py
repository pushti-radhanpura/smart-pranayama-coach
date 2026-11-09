"""
server.py
=========
FastAPI application for the Smart Pranayama Breathing Coach.

Endpoints:
  GET  /               -> frontend/index.html
  GET  /health         -> healthcheck and pattern loading validation
  GET  /patterns       -> list of 10 standardized pranayama patterns
  POST /evaluate       -> live chunk evaluation (verdict, tips, speech flag)
  POST /finish         -> final session review
  POST /compare        -> Sakoe-Chiba DTW session comparison (ideal vs user aligned)
  GET  /sensor/sample  -> analyze bundled LTspice breath wave CSV
  GET  /sensor/wave    -> downsampled wave (~10 Hz) for browser sensor replay
  POST /sensor/analyze -> analyze uploaded LTspice .txt export or wave CSV

Runs with:
    uvicorn server:app --reload
or:
    python server.py
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import coach_service as svc
import pranayama_dtw as dtw
import sensor_service

# Paths resolved relative to this file
HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(HERE, "..", "frontend"))
MAX_UPLOAD_SIZE = 60 * 1024 * 1024  # 60 MB upload limit


# Fail loudly on startup if patterns are invalid or missing
def _verify_startup():
    if dtw.LOAD_PROBLEMS:
        raise RuntimeError(
            f"Startup check failed: LOAD_PROBLEMS is not empty:\n"
            + "\n".join(f"  - {p}" for p in dtw.LOAD_PROBLEMS)
        )
    expected = {
        "bhastrika", "kapalabhati", "nadi_shodhana", "anuloma_viloma",
        "bhramari", "ujjayi", "sama_vritti", "sheetali", "sheetkari", "surya_bhedana"
    }
    loaded = set(dtw.PATTERNS.keys())
    missing = expected - loaded
    if missing:
        raise RuntimeError(f"Startup check failed: Missing expected pranayama pattern(s): {missing}")


_verify_startup()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _verify_startup()
    yield


app = FastAPI(title="Smart Pranayama Breathing Coach API", lifespan=lifespan)

# CORS configuration
cors_env = os.getenv("CORS_ORIGINS", "*")
origins = [o.strip() for o in cors_env.split(",") if o.strip()]
if "*" in origins or origins == ["*"]:
    origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*", "null"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic request models
class Chunk(BaseModel):
    session_id: str
    pattern: str                 # standardized key e.g. "sama_vritti"
    t: list[float]               # seconds since the guide started
    values: list[float]          # breath signal (volume-like: goes UP while inhaling)
    guided: bool = True


class CompareRequest(BaseModel):
    pattern: str
    t: list[float]
    values: list[float]


class CycleReviewRequest(BaseModel):
    pattern: str
    cycle_num: int
    t: list[float]
    values: list[float]


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.ServiceError as e:
        raise HTTPException(e.status, e.detail)


# ----------------------------------------------------------------------------
# Core Coach Endpoints
# ----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "patterns": list(dtw.PATTERNS.keys()),
        "problems": dtw.LOAD_PROBLEMS,
    }


@app.get("/patterns")
def patterns():
    return svc.list_patterns()


@app.post("/evaluate")
def evaluate(c: Chunk):
    return _run(svc.evaluate_chunk, c.session_id, c.pattern, c.t, c.values, c.guided)


@app.post("/finish")
def finish(c: Chunk):
    return _run(svc.finish_session, c.session_id, c.pattern, c.t, c.values)


@app.post("/compare")
def compare(req: CompareRequest):
    return _run(svc.compare_session, req.pattern, req.t, req.values)


@app.post("/cycle-review")
def cycle_review(req: CycleReviewRequest):
    return _run(svc.cycle_review, req.pattern, req.cycle_num, req.t, req.values)


# ----------------------------------------------------------------------------
# Sensor Pipeline Endpoints
# ----------------------------------------------------------------------------
@app.get("/sensor/sample")
def sensor_sample():
    try:
        return sensor_service.bundled_sample()
    except Exception as e:
        raise HTTPException(500, f"Error analyzing bundled sample: {e}")


@app.get("/sensor/wave")
def sensor_wave():
    try:
        return sensor_service.get_sensor_wave()
    except Exception as e:
        raise HTTPException(500, f"Error retrieving sensor wave: {e}")


@app.post("/sensor/analyze")
async def sensor_analyze(
    request: Request,
    file: UploadFile = File(...),
    time_scale: float = Form(100.0),
):
    # Enforce 60 MB upload limit
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, "Uploaded file exceeds 60 MB limit")

    content = bytearray()
    chunk_size = 1024 * 1024
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(413, "Uploaded file exceeds 60 MB limit")

    filename = file.filename or "export.txt"

    try:
        # Offload heavy numerical analysis to thread pool to avoid blocking the event loop
        result = await run_in_threadpool(
            sensor_service.analyze_upload,
            bytes(content),
            filename,
            time_scale=time_scale,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Failed to analyze sensor data: {e}")


# ----------------------------------------------------------------------------
# Frontend Static Files & SPA Routing
# ----------------------------------------------------------------------------
@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import Response
    return Response(content=b"", media_type="image/x-icon")


@app.get("/")
def index():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.exists(index_file):
        raise HTTPException(404, "frontend/index.html not found")
    return FileResponse(index_file, media_type="text/html")


if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
