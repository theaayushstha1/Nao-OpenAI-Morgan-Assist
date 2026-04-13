"""RAG tool: embed -> Pinecone top-k -> structured results."""
from __future__ import annotations

import logging

from pinecone import Pinecone
from openai import OpenAI

from agents import function_tool
from server import config

logger = logging.getLogger(__name__)

_pc = Pinecone(api_key=config.PINECONE_API_KEY) if config.PINECONE_API_KEY else None
try:
    _index = _pc.Index(config.PINECONE_INDEX_NAME) if _pc else None
except Exception as exc:
    logger.warning("Pinecone index init failed (will be None): %s", exc)
    _index = None

_client = OpenAI(api_key=config.OPENAI_API_KEY)


def _embed(text: str) -> list[float]:
    r = _client.embeddings.create(model="text-embedding-3-small", input=text)
    return r.data[0].embedding


def _search_impl(query: str, top_k: int = 5) -> list[dict]:
    if _index is None:
        return []
    emb = _embed(query)
    res = _index.query(
        vector=emb, top_k=top_k, namespace=config.PINECONE_NAMESPACE,
        include_metadata=True,
    )
    return [
        {"text": m.metadata.get("text", ""), "score": float(m.score)}
        for m in res.matches
    ]


@function_tool
def pinecone_search(query: str, top_k: int = 5) -> list[dict]:
    """Search the Morgan State CS knowledge base. Returns top_k passages with scores."""
    return _search_impl(query, top_k)
