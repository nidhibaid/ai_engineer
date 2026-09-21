"""Stage 5 server — full system with cost readout (same as main.py).

Run from repo root:
  uv run uvicorn stages.serve_stage5:app --port 8000 --reload
  uv run uvicorn main:app --port 8000 --reload
"""

import sys
from pathlib import Path

# main.py lives in the repo root, one level above this file.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from main import app  # noqa: E402, F401
