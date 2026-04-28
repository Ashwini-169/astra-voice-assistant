"""Lightweight vector store wrapper using local Qdrant.

Connection strategy:
1. Try HTTP mode (http://127.0.0.1:6333) — avoids file-lock conflicts when
   multiple processes (LLM service + orchestrator) access Qdrant simultaneously.
2. Fall back to local file mode (./qdrant_data) if HTTP is unavailable.
"""
import logging
import uuid
from typing import List

logger = logging.getLogger(__name__)

QDRANT_HTTP_URL = "http://127.0.0.1:6333"


def _make_client(collection_name: str, dim: int):
    """Return a QdrantClient, preferring HTTP over local file."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams

    # Try HTTP first (no file lock, safe for multi-process)
    try:
        client = QdrantClient(url=QDRANT_HTTP_URL, timeout=3.0)
        client.get_collections()  # probe — raises if unreachable
        logger.info("[vector-store] connected to Qdrant HTTP at %s", QDRANT_HTTP_URL)
    except Exception:  # pylint: disable=broad-except
        logger.info("[vector-store] Qdrant HTTP unavailable, using local file mode")
        client = QdrantClient(path="./qdrant_data")

    collections = [c.name for c in client.get_collections().collections]
    if collection_name not in collections:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
    return client


class VectorStore:
    def __init__(self, collection_name: str = "conversations", dim: int = 384):
        self.collection_name = collection_name
        self._dim = dim
        self._client = None  # lazy init to avoid startup lock contention

    def _get_client(self):
        if self._client is None:
            self._client = _make_client(self.collection_name, self._dim)
        return self._client

    def upsert(self, doc_id: str, vector: List[float], text: str) -> None:
        from qdrant_client.models import PointStruct
        try:
            clean_id = doc_id.replace("mem-", "")
            point_id = str(uuid.UUID(clean_id))
        except (ValueError, AttributeError):
            point_id = str(uuid.uuid4())

        try:
            self._get_client().upsert(
                collection_name=self.collection_name,
                points=[PointStruct(id=point_id, vector=vector, payload={"text": text})],
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("[vector-store] upsert failed: %s", exc)
            self._client = None  # reset so next call retries connection

    def search(self, vector: List[float], top_k: int = 3) -> List[str]:
        try:
            results = self._get_client().search(
                collection_name=self.collection_name,
                query_vector=vector,
                limit=top_k,
            )
            return [hit.payload.get("text", "") for hit in results]
        except Exception as exc:  # pylint: disable=broad-except
            # ISSUE 7 FIX: Handle concurrent access gracefully
            error_msg = str(exc).lower()
            if "already accessed" in error_msg or "lock" in error_msg:
                logger.warning("[vector-store] concurrent access detected, skipping search")
                return []  # Return empty instead of crashing
            logger.warning("[vector-store] search failed: %s", exc)
            self._client = None  # reset on error
            return []
