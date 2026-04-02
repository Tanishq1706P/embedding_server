import os
import logging
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from pymongo import MongoClient
import httpx

# ---------------- INIT ----------------
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Embeddings Batch Processor (Fixed)")

# ---------------- ENV ----------------
MAIN_SERVER_URL = os.getenv("MAIN_SERVER_URL", "http://localhost:8000").strip()
API_KEY = os.getenv("MAIN_API_KEY")

if not API_KEY:
    raise RuntimeError("MAIN_API_KEY not set in .env")

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "email_summarizer")

COLLECTION_EMAILS = "emails"
COLLECTION_SUMMARIES = "summaries"

# ---------------- DB ----------------
client = MongoClient(MONGO_URI)
db = client[MONGO_DB]

# ---------------- MODEL ----------------
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

# ---------------- REQUEST ----------------
class BatchRequest(BaseModel):
    collection: str = COLLECTION_EMAILS
    limit: int = 10
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or specific domains
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- MAIN ----------------
@app.post("/process-batch")
async def process_batch(req: BatchRequest):
    if not MONGO_URI:
        raise HTTPException(500, "MONGO_URI not set")

    # ---- Fetch emails ----
    emails = list(db[req.collection].find({}, {"id": 1}).limit(req.limit))

    if not emails:
        return {"status": "no_emails"}

    logger.info(f"Processing {len(emails)} emails")

    # ---- Call main server ----
    headers = {
        "X-API-Key": API_KEY.strip(),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300) as client_http:
        resp = await client_http.post(
            f"{MAIN_SERVER_URL}/batch_summarize",
            json={"collection": req.collection, "limit": len(emails)},
            headers=headers,
        )

    if resp.status_code != 200:
        raise HTTPException(500, f"Main server failed: {resp.text}")

    batch_result = resp.json()

    # ---- Collect successful email_ids ----
    success_ids = [
        r.get("email_id")
        for r in batch_result.get("results", [])
        if r.get("success") and r.get("email_id")
    ]

    # ---- Delete processed emails ----
    if success_ids:
        db[req.collection].delete_many({"id": {"$in": success_ids}})
        logger.info(f"Deleted {len(success_ids)} processed emails")

    if not success_ids:
        return {
            "status": "no_success",
            "batch_result": batch_result
        }

    # ---- Fetch summaries (FIXED QUERY) ----
    summaries = list(
        db[COLLECTION_SUMMARIES].find(
            {"summary_result.email_id": {"$in": success_ids}}
        )
    )

    if not summaries:
        return {
            "status": "no_summaries_found",
            "batch_result": batch_result
        }

    # ---- Prepare texts ----
    texts = []
    email_map = {}

    for doc in summaries:
        summary_data = doc.get("summary_result", {})

        email_id = summary_data.get("email_id")
        summary = summary_data.get("summary")
        subject = summary_data.get("subject") or ""

        # Skip invalid records
        if not email_id or not summary:
            logger.warning(f"Skipping invalid summary doc: {doc.get('_id')}")
            continue

        text = f"Subject: {subject}\nSummary: {summary}"

        texts.append(text)
        email_map[email_id] = doc["_id"]

    if not texts:
        return {
            "status": "no_valid_summaries",
            "batch_result": batch_result
        }

    # ---- Generate embeddings ----
    embeddings = model.encode(texts, batch_size=16, show_progress_bar=False)

    # ---- Update DB ----
    for email_id, embedding in zip(email_map.keys(), embeddings):
        db[COLLECTION_SUMMARIES].update_one(
            {"_id": email_map[email_id]},
            {"$set": {"vector_embedding": embedding.tolist()}}
        )

    logger.info(f"Updated {len(embeddings)} embeddings")

    return {
        "status": "success",
        "processed": len(emails),
        "embeddings_updated": len(embeddings),
        "batch_result": batch_result,
    }


# ---------------- HEALTH ----------------
@app.get("/health")
async def health():
    return {
        "status": "ready updated",
        "model_loaded": True,
        "api_key_loaded": bool(API_KEY)
    }
from fastapi import Query


@app.post("/embed-text")
async def embed_text(text: str = Query(..., description="The text to embed")):
    """
    Accepts a single string as input and returns its vector embedding.
    """
    if not text.strip():
        raise HTTPException(400, "Text cannot be empty")

    # Generate embedding
    embedding = model.encode([text], batch_size=1, show_progress_bar=False)[0]

    return {
        "text": text,
        "embedding": embedding.tolist(),
        "dim": len(embedding)
    }