#!/usr/bin/env python3
"""
Ingests every .txt file in northwind-sample-docs/ via POST /ingest.

For each file:
  - reads the full text
  - assigns a stable document_id (the filename without its extension)
  - POSTs to /ingest
  - prints the chunk count returned

At the end, prints the total vector count currently in the Pinecone index
(via GET /health/pinecone) — this reflects everything in the store, not
just what this run added.

Usage:
    python3 ingest_all_docs.py
"""

import glob
import os
import sys

import requests

DOCS_DIR = "northwind-sample-docs"
INGEST_URL = "http://127.0.0.1:8000/ingest"
HEALTH_URL = "http://127.0.0.1:8000/health/pinecone"


def document_id_from_path(path: str) -> str:
    """Stable, deterministic id from the filename — e.g.
    'doc1_handbook.txt' -> 'doc1_handbook'. The same file always maps to
    the same document_id, so re-running this script upserts (overwrites)
    the same vectors instead of creating duplicates."""
    return os.path.splitext(os.path.basename(path))[0]


def main() -> None:
    txt_files = sorted(glob.glob(os.path.join(DOCS_DIR, "*.txt")))

    if not txt_files:
        print(f"No .txt files found in '{DOCS_DIR}/' (checked from: {os.getcwd()})", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(txt_files)} file(s) in {DOCS_DIR}/\n")

    total_chunks_this_run = 0
    failures = []

    for path in txt_files:
        document_id = document_id_from_path(path)
        text = open(path, encoding="utf-8").read()

        if not text.strip():
            print(f"  SKIP  {path} — file is empty")
            continue

        payload = {
            "document_id": document_id,
            "text": text,
            "source": os.path.basename(path),
        }

        try:
            resp = requests.post(INGEST_URL, json=payload, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  FAIL  {path} — {e}")
            failures.append(path)
            continue

        data = resp.json()
        chunks = data.get("chunks_indexed", 0)
        total_chunks_this_run += chunks
        print(f"  OK    {path} -> document_id='{document_id}', chunks_indexed={chunks}")

    print(f"\nFiles processed: {len(txt_files)}  |  Failed: {len(failures)}")
    print(f"Chunks ingested this run: {total_chunks_this_run}")

    # Total in the index overall — may differ from the sum above if the
    # index already held vectors, or if a document_id was re-ingested
    # (upserts overwrite existing chunk ids, they don't add duplicates).
    try:
        health = requests.get(HEALTH_URL, timeout=30).json()
        print(f"Total vectors currently in the index: {health.get('vector_count')}")
    except requests.RequestException as e:
        print(f"Could not reach /health/pinecone to get the index total: {e}")


if __name__ == "__main__":
    main()