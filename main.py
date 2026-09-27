"""RAG PIPELINE

Run: uvicorn main:app --port 8000 --reload"""

from py_compile import PyCompileError
import time
from pathlib import Path
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
from functools import lru_cache
from pinecone import Pinecone
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Load .env from this folder so the key is found regardless of shell working directory.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH)

# Reuse one client so TLS handshakes are not repeated on every request.
app = FastAPI()
client = OpenAI()  # Reads OPENAI_API_KEY from the environment; never hardcode keys.
pc = Pinecone()
Index_Name = os.getenv("PINECONE_INDEX_NAME")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL")
index = pc.Index(Index_Name)


# Stage 4 default — strong general model; swap at request time for the live demo.
DEFAULT_MODEL = "gpt-4o-mini"

# Stage 5 — per-1K-token input/output USD (derived from OpenAI list prices).
MODEL_PRICES_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "o3-mini": (0.0011, 0.0044),
}


def pinecone_health() -> dict:
    names = pc.list_indexes().names()
    if Index_Name not in names:
        return {"reachable": False, "index_name": Index_Name,
                 "error": "index not found — has it been created?"}
    stats = index.describe_index_stats()
    return {
        "reachable": True,
        "index_name": Index_Name,
        "dimension": stats.get("dimension"),
        "vector_count": stats.get("total_vector_count"),
        "embedding_model": EMBEDDING_MODEL,
    }

# - Chunk with RecursiveCharacterTextSplitter - chunk_size ~800, overlap ~100 (make these configurable)
 # --- RECURSIVE splitting (try \n\n, then \n, then '. ', then ' ') ---
def chunk_text(text, chunk_size=800, overlap=100):
    recursive_splitter = RecursiveCharacterTextSplitter(
    chunk_size=chunk_size,
    chunk_overlap=overlap,
    separators=["\n\n", "\n", ". ", " ", ""],
)
    chunks_recursive = recursive_splitter.split_text(text)
    return chunks_recursive


@lru_cache
def get_embeddings_client() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model=EMBEDDING_MODEL)

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embed — used by /ingest."""
    return get_embeddings_client().embed_documents(texts)

def embed_query(text: str) -> list[float]:
    """Single embed, correct flat shape — used by /ask."""
    return get_embeddings_client().embed_query(text)


class Answer(BaseModel):
    """Structured model output — this is what turns a chatbot into a component."""

    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    sources_needed: bool


class AskRequest(BaseModel):
    """Typed request body so bad input is rejected before we spend tokens."""

    question: str
    force_bad: bool = False  # Stage 3 demo knob — first attempt breaks schema on purpose.
    model: str | None = None  # Stage 4 — optional override to swap models live.


class AskResponse(BaseModel):
    """Typed response so callers always get the same shape back."""

    answer: Answer
    tokens_used: int
    model: str
    latency_ms: int
    cost_usd: float

class IngestRequest(BaseModel):
    document_id: str
    text: str
    source: str | None = None

def compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Turn real usage into dollars — same prompt, different model, different cost."""

    prices = MODEL_PRICES_PER_1K.get(model, MODEL_PRICES_PER_1K[DEFAULT_MODEL])
    input_per_1k, output_per_1k = prices
    return (prompt_tokens / 1000 * input_per_1k) + (completion_tokens / 1000 * output_per_1k)


def call_model_structured(question: str, model: str) -> tuple[Answer, int, int, int]:
    """
    Stage 2 center: OpenAI structured output forces exactly the Answer schema.
    Returns parsed answer plus token counts from billing metadata.
    """

    completion = client.chat.completions.parse(
        model=model,
        messages=[{"role": "user", "content": question}],
        response_format=Answer,
    )

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("Model returned no parseable structured output")

    usage = completion.usage
    total = usage.total_tokens if usage else 0
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    return parsed, total, prompt_tokens, completion_tokens


def call_model_unsafe(question: str, model: str) -> tuple[Answer, int, int, int]:
    """
    Stage 3 demo path: free-form JSON call, then validate locally.
    The bad instruction makes confidence a string so Pydantic rejects it reliably.
    """

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{question}\n\n"
                    "Reply with ONLY a JSON object using keys answer, confidence, sources_needed. "
                    "Set confidence to the string 'very high' (not a number)."
                ),
            }
        ],
    )

    raw = completion.choices[0].message.content or ""
    # Guardrail: refuse malformed output instead of passing it through to clients.
    answer = Answer.model_validate_json(raw)

    usage = completion.usage
    total = usage.total_tokens if usage else 0
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    return answer, total, prompt_tokens, completion_tokens

@app.get("/health/pinecone")
def health_pinecone():
    return pinecone_health()


# - Accept JSON with text + document_id (and optional metadata like source filename)
# - Embed each chunk with text-embedding-3-small 
# - Upsert into the vector store with metadata: document_id, chunk_index, source
# - Return JSON: document_id, chunks_indexed, status



@app.post("/ingest")
def ingest(req: IngestRequest):
    if not req.document_id.strip() or not req.text.strip():
        raise HTTPException(
            status_code=400,
            detail="Both 'document_id' and 'text' are required and cannot be empty.",
        )

    chunks = chunk_text(req.text)
    embeddings = embed_texts(chunks)   # one batch call instead of one-per-chunk

    vectors = [
        {
            "id": f"{req.document_id}-{i}",
            "values": embedding,
            "metadata": {
                "document_id": req.document_id,
                "chunk_index": i,
                "source": req.source or "",
                "text": chunk,   # kept for retrieval-time context — see note below
            },
        }
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]

    index.upsert(vectors=vectors)

    return {
        "document_id": req.document_id,
        "chunks_indexed": len(chunks),
        "status": "success",
    }
    

# Before changing POST /ask, add GET /debug/retrieve?q=... (or a small script) that:
# 1. Embeds the question
# 2. Returns top-5 chunks with similarity scores and document_id metadata
# 3. Does NOT call the LLM

# I will use this to verify retrieval before wiring generation.


class RetrievedChunk(BaseModel):
    document_id: str
    chunk_index: int
    score: float
    source: str | None = None
    text: str

class RetrieveDebugResponse(BaseModel):
    query: str
    results: list[RetrievedChunk]


@app.get("/debug/retrieve", response_model=RetrieveDebugResponse)
def retrieve(q: str):
    q_embedding = embed_query(q)
    results = index.query(vector=q_embedding, top_k=5, include_metadata=True)

    chunks = [
        RetrievedChunk(
            document_id=m["metadata"]["document_id"],
            chunk_index=m["metadata"]["chunk_index"],
            score=m["score"],
            source=m["metadata"].get("source"),
            text=m["metadata"]["text"],
        )
        for m in results["matches"]
    ]

    return RetrieveDebugResponse(query=q, results=chunks)


# Upgrade POST /ask to use retrieval-augmented generation:
# 1. Embed the question
# 2. Retrieve top-k chunks (start with k=5)
# 3. Build a grounding prompt: answer ONLY from context, cite document_id for each chunk used, refuse if context is insufficient
# 4. Call the existing Session 1 generation path
# 5. Preserve tokens_used and cost_usd in the response where possible
# 6. Include retrieved chunk IDs in the response JSON

# Show me the grounding prompt template you used.

@app.post("/ask")
def ask(body: AskRequest) -> AskResponse:
    """Answer one question with structured output, guardrails, and cost visibility."""
    q_embedding = embed_query(body.question)  # single embed instead of batch
    results = index.query(vector=q_embedding, top_k=5, include_metadata=True)

    # if not results["matches"] or results["matches"][0]["score"] < 0.75:
    #     return {"answer": "I don't have enough information to answer that.", "citations": []}

    context = "\n\n".join(m["metadata"]["text"] for m in results["matches"])
    sources = [m["metadata"]["document_id"] for m in results["matches"]]
    # chunk_ids = [m["metadata"]["chunk_index"] for m in results["matches"]]
    
    model = body.model or DEFAULT_MODEL
    last_error: str | None = None
    
    prompt = f"""Answer using ONLY the context below. Cite the document_id for any claim.
    If the context doesn't contain the answer, say you don't know.

    Context:
    {context}
    
    Sources: {sources}

    Question: {body.question}"""

    # Stage 3: one retry keeps the logic legible while still protecting callers.
    for attempt in range(2):
        try:
            start = time.perf_counter()

            # First attempt with force_bad uses the unsafe path; retry uses structured output.
            use_bad_path = body.force_bad and attempt == 0
            if use_bad_path:
                answer, tokens_used, prompt_tokens, completion_tokens = call_model_unsafe(
                    prompt, model
                )
            else:
                answer, tokens_used, prompt_tokens, completion_tokens = call_model_structured(
                    prompt, model
                )

            latency_ms = int((time.perf_counter() - start) * 1000)
            cost_usd = compute_cost_usd(model, prompt_tokens, completion_tokens)

            return AskResponse(
                answer=answer,
                tokens_used=tokens_used,
                model=model,
                latency_ms=latency_ms,
                cost_usd=round(cost_usd, 6),
            )
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            continue

    # Clean failure — never leak a half-parsed response to the client.
    raise HTTPException(
        status_code=502,
        detail=f"Model response failed schema validation after retry: {last_error}",
    )

