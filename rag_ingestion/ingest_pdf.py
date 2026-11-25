import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from functools import lru_cache
import requests
from pinecone import Pinecone
from langchain_openai import OpenAIEmbeddings
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.document_loaders.parsers import PyPDFParser
from langchain_core.documents.base import Blob
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv

from collections import defaultdict
from hashlib import sha256

# Load the project-level .env before creating the Pinecone client.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

pc = Pinecone()
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")
if not INDEX_NAME:
    raise SystemExit("Set PINECONE_INDEX_NAME in the project .env before ingesting.")

# text-embedding-3-small produces 1536 dimensions, matching the current index.
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small"
index = pc.Index(INDEX_NAME)

@lru_cache
def get_embeddings_client() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=EMBEDDING_MODEL)

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embed — used by /ingest."""
    return get_embeddings_client().embed_documents(texts)

def build_headers() -> dict[str, str]:
    contact_email = os.environ.get("INGEST_CONTACT_EMAIL", "").strip()
    if not contact_email:
        raise SystemExit(
            "Set INGEST_CONTACT_EMAIL to a monitored email address before running ingestion."
        )

    return {
        "User-Agent": f"PreparednessRAG/1.0 (+mailto:{contact_email})",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.8",
    }


SCRIPT_DIR = Path(__file__).resolve().parent
SNAPSHOT_HEADER = re.compile(r"\A\s*<!--(.*?)-->\s*", re.DOTALL)


def snapshot_for(record: dict, url: str) -> Path | None:
    """Saved copy of a page that blocks direct requests (see manifest `snapshot_paths`).

    Paths are relative to this script's folder. A listed path that does not exist is an
    error, not a silent fallback to the live URL (which is known to be blocked).
    """
    rel = (record.get("snapshot_paths") or {}).get(url)
    if not rel:
        return None
    path = SCRIPT_DIR / rel
    if not path.is_file():
        raise FileNotFoundError(f"snapshot listed but missing: {rel}")
    return path


def load_snapshot(path: Path, url: str) -> list[Document]:
    """Read a snapshot, dropping its provenance header so it is not embedded."""
    text = path.read_text(encoding="utf-8")
    header = SNAPSHOT_HEADER.match(text)
    metadata = {"source": url, "snapshot_file": path.name}
    if header:
        fetched = re.search(r"fetched:\s*(\S+)", header.group(1))
        if fetched:
            metadata["snapshot_fetched"] = fetched.group(1)
        text = text[header.end():]
    return [Document(page_content=text, metadata=metadata)]


def load_source_url(url: str, record: dict, headers: dict[str, str]):
    snapshot = snapshot_for(record, url)
    if snapshot is not None:
        docs = load_snapshot(snapshot, url)
    elif urlparse(url).path.lower().endswith(".pdf"):
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        blob = Blob.from_data(
            data=response.content,
            mime_type="application/pdf",
            metadata={"source": url},
        )
        docs = PyPDFParser().parse(blob)
    else:
        docs = WebBaseLoader(
            web_path=url,
            header_template=headers,
            requests_kwargs={"timeout": 30},
            raise_for_status=True,
        ).load()

    # Add the manifest tags to each page/document.
    manifest_fields = (
        "id",
        "title",
        "source_agency",
        "hazard",
        "audience",
        "jurisdiction",
        "publication_date",
        "date_precision",
        "accessed_date",
        # WP-4 fields; present only on records that have been updated so far.
        "scope",
        "jurisdiction_code",
        "volatility",
        "review_by",
        "phase",
        "language",
    )
    shared_metadata = {
        key: record[key]
        for key in manifest_fields
        if key in record
    }

    for doc in docs:
        doc.metadata.update(shared_metadata)
        doc.metadata["source_url"] = url

    return docs


def classify_error(exc: Exception) -> tuple[str, int | None]:
    response = getattr(exc, "response", None)
    status_code = response.status_code if response is not None else None

    if isinstance(exc, requests.HTTPError):
        if status_code in (401, 403):
            return "blocked", status_code
        if status_code in (404, 410):
            return "not_found", status_code
        if status_code == 429 or (status_code is not None and status_code >= 500):
            return "retryable_error", status_code
        return "http_error", status_code

    if isinstance(exc, FileNotFoundError):
        return "snapshot_missing", status_code
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return "retryable_error", status_code
    if isinstance(exc, requests.RequestException):
        return "request_error", status_code
    return "processing_error", status_code


def make_status(
    record: dict,
    url: str,
    status: str,
    document_count: int = 0,
    error: Exception | None = None,
) -> dict:
    _, http_status = classify_error(error) if error is not None else (status, None)
    result = {
        "record_id": record.get("id"),
        "source_url": url,
        "status": status,
        "document_count": document_count,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if http_status is not None:
        result["http_status"] = http_status
    if error is not None:
        # Avoid putting exception text in the report; request errors can contain
        # signed URLs or other query-string values.
        result["error_type"] = type(error).__name__
    return result


def inspect_chunks(chunks):
    usable_chunks = []
    blank_count = 0
    whitespace_heavy = []
    total_chars = 0
    total_bytes = 0
    total_whitespace = 0

    for chunk_number, chunk in enumerate(chunks):
        text = chunk.page_content
        if not text.strip():
            blank_count += 1
            continue

        usable_chunks.append(chunk)
        # Count only spaces and tabs here; line breaks are usually meaningful.
        whitespace_count = sum(char in " \t" for char in text)
        total_chars += len(text)
        total_bytes += len(text.encode("utf-8"))
        total_whitespace += whitespace_count

        ratio = whitespace_count / len(text)
        if ratio > 0.40:
            whitespace_heavy.append((chunk_number, ratio))

    average_chars = total_chars / len(usable_chunks) if usable_chunks else 0
    whitespace_ratio = total_whitespace / total_chars if total_chars else 0
    print(
        f"Chunks: {len(chunks)} total, {blank_count} blank removed, "
        f"{len(usable_chunks)} usable | {total_chars:,} chars "
        f"({total_bytes:,} UTF-8 bytes), {average_chars:.0f} chars/chunk, "
        f"{whitespace_ratio:.0%} spaces/tabs overall."
    )

    if whitespace_heavy:
        print(f"Chunks over 40% spaces/tabs (review these): {len(whitespace_heavy)}")
        for chunk_number, ratio in whitespace_heavy[:10]:
            print(f"  chunk {chunk_number}: {ratio:.0%} spaces/tabs")
        if len(whitespace_heavy) > 10:
            print(f"  ...and {len(whitespace_heavy) - 10} more")

    return usable_chunks


def normalize_extracted_text(text: str) -> str:
    """Remove PDF/HTML layout spacing while preserving paragraph breaks."""
    text = text.replace("\u00a0", " ").replace("\t", " ")
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main(only: set[str] | None = None):
    headers = build_headers()
    script_dir = SCRIPT_DIR
    manifest_path = script_dir / "sources.jsonl"
    records = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if only:
        unknown = only - {record.get("id") for record in records}
        if unknown:
            raise SystemExit(f"--only names records not in sources.jsonl: {sorted(unknown)}")
        records = [record for record in records if record.get("id") in only]
        print(f"Ingesting {len(records)} record(s): {', '.join(sorted(only))}")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    status_path = script_dir / f"ingestion_status_{run_id}.jsonl"
    documents = []
    url_count = 0
    failure_count = 0

    with status_path.open("w", encoding="utf-8") as status_file:
        for record in records:
            urls = record.get("content_urls") or [record["source_url"]]
            for url in urls:
                url_count += 1
                try:
                    loaded_docs = load_source_url(url, record, headers)
                    documents.extend(loaded_docs)
                    from_snapshot = snapshot_for(record, url) is not None
                    if not loaded_docs:
                        status = "empty"
                    else:
                        status = "success_snapshot" if from_snapshot else "success"
                    status_record = make_status(
                        record,
                        url,
                        status,
                        document_count=len(loaded_docs),
                    )
                    print(f"{status.upper():16} {url} ({len(loaded_docs)} document(s))")
                except Exception as exc:
                    status, _ = classify_error(exc)
                    failure_count += 1
                    status_record = make_status(record, url, status, error=exc)
                    print(f"{status.upper():16} {url} ({type(exc).__name__})")

                status_file.write(json.dumps(status_record, ensure_ascii=False) + "\n")
                status_file.flush()

    print(f"\nLoaded documents: {len(documents)}")
    print(f"URLs processed: {url_count}  |  URLs with errors: {failure_count}")
    print(f"URL status report: {status_path}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    for document in documents:
        document.page_content = normalize_extracted_text(document.page_content)

    chunks = splitter.split_documents(documents)
    chunks = inspect_chunks(chunks)
    texts = [chunk.page_content for chunk in chunks]
    embeddings = embed_texts(texts) if texts else []
    return chunks, embeddings

def upsert_to_vectordb(chunks, embedded):
    source_chunk_counts = defaultdict(int)
    vectors = []

    for chunk, embedding in zip(chunks, embedded, strict=True):
        metadata = chunk.metadata
        document_id = str(metadata.get("id", "unknown"))
        source_url = str(metadata.get("source_url", ""))

        chunk_index = source_chunk_counts[(document_id, source_url)]
        source_chunk_counts[(document_id, source_url)] += 1

        vector_metadata = {
            "document_id": document_id,
            "chunk_index": chunk_index,
            "source": source_url,
            "source_url": source_url,
            "text": chunk.page_content,
        }

        # Keep Pinecone-compatible metadata; omit nulls and nested objects.
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)) or (
                isinstance(value, list) and all(isinstance(item, str) for item in value)
            ):
                vector_metadata[key] = value

        url_hash = sha256(source_url.encode("utf-8")).hexdigest()[:12]
        vectors.append({
            "id": f"{document_id}:{url_hash}:{chunk_index}",
            "values": embedding,
            "metadata": vector_metadata,
        })

    # Pinecone dense-vector storage is approximately ID bytes + metadata bytes
    # + 4 bytes per embedding dimension; this is an estimate, not a billing figure.
    estimated_bytes = sum(
        len(vector["id"].encode("utf-8"))
        + len(json.dumps(vector["metadata"], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        + len(vector["values"]) * 4
        for vector in vectors
    )
    print(
        f"Approx. Pinecone storage for this upsert: {estimated_bytes:,} bytes "
        f"({estimated_bytes / (1024 ** 2):.2f} MiB)."
    )

    if vectors:
        response = index.upsert(
            vectors=vectors,
            batch_size=50,
            max_concurrency=4,
        )
        upserted_count = response.upserted_count
        print(
            f"Upserted {upserted_count}/{len(vectors)} vectors to index "
            f"{INDEX_NAME!r} (default namespace)."
        )
        if response.failed_item_count:
            print(f"Warning: {response.failed_item_count} vector(s) failed to upsert.")
    else:
        print("No vectors to upsert.")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest sources.jsonl into Pinecone.")
    parser.add_argument(
        "--only",
        help="Comma-separated record ids to ingest (default: every record).",
    )
    args = parser.parse_args()
    only = {item.strip() for item in args.only.split(",") if item.strip()} if args.only else None
    chunks, embeddings = main(only)
    #print(f"length of chunks:{len(chunks)},length of embeddings{len(embeddings)}")
    upsert_to_vectordb(chunks, embeddings)
