"""
Generate vector embeddings for all documents using Gemini embedding API.
Run: python scripts/generate_embeddings.py
"""
import sys, os, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

from database import SessionLocal
from models.document import Document
from sqlalchemy import text as sql_text
from config import GEMINI_API_KEY


def embed_text(text: str) -> list[float]:
    """Generate embedding using Google Gemini embedding API."""
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    # Truncate to ~8000 tokens worth of text
    truncated = text[:30000]
    result = client.models.embed_content(
        model="models/text-embedding-004",
        contents=truncated,
    )
    return result.embeddings[0].values


def main():
    db = SessionLocal()

    # Get docs with extracted text but no embedding
    docs = db.execute(sql_text("""
        SELECT id, filename, extracted_text, notes
        FROM documents
        WHERE extracted_text IS NOT NULL
        AND length(extracted_text) > 50
        AND embedding IS NULL
        ORDER BY created_at DESC
        LIMIT 500
    """)).fetchall()

    print(f"Found {len(docs)} documents needing embeddings", flush=True)

    embedded = 0
    errors = 0
    for doc in docs:
        try:
            # Combine notes + extracted text for richer embedding
            text = ""
            if doc.notes:
                text += doc.notes + "\n\n"
            text += doc.extracted_text[:25000]

            vector = embed_text(text)

            # Store as pgvector format
            vector_str = "[" + ",".join(str(v) for v in vector) + "]"
            db.execute(sql_text(
                "UPDATE documents SET embedding = :vec WHERE id = :id"
            ), {"vec": vector_str, "id": str(doc.id)})
            db.commit()
            embedded += 1

            if embedded % 25 == 0:
                print(f"  Embedded {embedded}/{len(docs)}...", flush=True)

        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.warning(f"  Failed {doc.filename}: {e}")
            db.rollback()

        # Rate limit - Gemini has limits
        import time
        time.sleep(0.2)

    print(f"\nDone: {embedded} embedded, {errors} errors", flush=True)
    db.close()


if __name__ == "__main__":
    main()
