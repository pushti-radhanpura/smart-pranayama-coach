"""
main.py
=======
Root entrypoint for deployment on cloud platforms:
Render, Railway, Fly.io, Heroku, Hugging Face Spaces, and Docker.
"""
from __future__ import annotations

import os
import sys

# Ensure backend and sensor_pipeline are in Python path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
SENSOR_DIR = os.path.join(ROOT_DIR, "sensor_pipeline")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if SENSOR_DIR not in sys.path:
    sys.path.insert(0, SENSOR_DIR)

from server import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Starting Smart Pranayama Coach on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
