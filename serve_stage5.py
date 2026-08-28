"""Stage 5 server — full system with cost readout (same as main.py).

Run: uvicorn serve_stage5:app --port 8000 --reload
     or: uvicorn main:app --port 8000 --reload
"""

from main import app  # noqa: F401
