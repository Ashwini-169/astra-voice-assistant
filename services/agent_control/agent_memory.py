"""Agent session memory: short-term state + RAG retrieval for multi-turn chaining.

Stores structured intermediate results (search URLs, fetched content, entities)
so the planner can reference prior steps without re-executing them.
RAG retrieval pulls relevant long-term memories from Qdrant to enrich context.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    """Normalize URLs from search results.

    Fixes:
    - Missing scheme (//example.com → https://example.com)
    - DuckDuckGo redirect wrappers (extracts real URL from uddg param)
    - URL encoding
    """
    if not url or not isinstance(url, str):
        return ""

    url = url.strip()

    # Fix protocol-relative URLs
    if url.startswith("//"):
        url = "https:" + url

    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception:  # pylint: disable=broad-except
        return url

    # Extract real URL from DuckDuckGo redirect wrapper
    if "duckduckgo.com" in parsed.netloc and "/l/" in parsed.path:
        query_params = parse_qs(parsed.query)
        if "uddg" in query_params:
            real_url = unquote(query_params["uddg"][0])
            logger.debug("[url-normalize] extracted from DDG redirect: %s", real_url)
            return real_url

    return url


@dataclass
class AgentSessionMemory:
    """Short-term state for one agent turn.

    Populated incrementally as steps execute so the planner always has
    the latest intermediate results available in its prompt context.
    """

    # Entities extracted from the user query
    entities: Dict[str, Any] = field(default_factory=dict)

    # Last search results: list of {title, url} dicts
    last_search_results: List[Dict[str, str]] = field(default_factory=list)

    # URL selected for fetch (set after search step)
    selected_url: str = ""

    # Fetched page content (set after fetch step)
    fetched_content: str = ""

    # Structured metrics extracted from fetched content
    extracted_metrics: Dict[str, Any] = field(default_factory=dict)

    # Long-term memories retrieved from Qdrant (injected at session start)
    rag_context: str = ""

    def update_from_step(self, tool_category: str, tool_name: str, arguments: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Update session state after a tool executes."""
        payload = result.get("result", result)

        if tool_category == "search":
            raw_results = []
            if isinstance(payload, dict):
                raw_results = payload.get("results", [])
            elif isinstance(payload, list):
                raw_results = payload

            if raw_results:
                self.last_search_results = [
                    {"title": str(r.get("title", "")), "url": _normalize_url(str(r.get("url", "")))}
                    for r in raw_results
                    if isinstance(r, dict) and r.get("url")
                ]
            else:
                # Fallback: parse text blob (e.g. DuckDuckGo returns plain text)
                text_blob = ""
                if isinstance(payload, str):
                    text_blob = payload
                elif isinstance(payload, dict):
                    text_blob = str(payload.get("content", payload.get("text", "")))
                
                # Try "URL: https://..." format first (DuckDuckGo new format)
                urls = re.findall(r'URL:\s*(https?://[^\s\'"<>]+)', text_blob)
                # Fallback to bare URLs if no "URL:" prefix found
                if not urls:
                    urls = re.findall(r'https?://[^\s\'"<>]+', text_blob)
                # Also catch protocol-relative URLs
                if not urls:
                    urls = re.findall(r'//[^\s\'"<>]+', text_blob)
                self.last_search_results = [{"title": "", "url": _normalize_url(u)} for u in urls[:10]]

            # Auto-select first result as candidate URL
            if self.last_search_results and not self.selected_url:
                self.selected_url = self.last_search_results[0]["url"]
            logger.info("[agent-memory] search stored %d results, selected_url=%s",
                        len(self.last_search_results), self.selected_url)

        elif tool_category == "fetch":
            content = ""
            if isinstance(payload, dict):
                content = str(payload.get("content", payload.get("text", "")))
            elif isinstance(payload, str):
                content = payload
            self.fetched_content = content[:8000]  # cap to avoid prompt overflow
            logger.info("[agent-memory] fetch stored %d chars", len(self.fetched_content))

        elif tool_category == "time":
            if isinstance(payload, dict):
                self.entities.update({k: v for k, v in payload.items() if v})

    def best_fetch_url(self) -> str:
        """Return the best URL to fetch: explicit selection or first search result."""
        if self.selected_url:
            return self.selected_url
        if self.last_search_results:
            return self.last_search_results[0]["url"]
        return ""

    def to_context_block(self) -> str:
        """Render session state as a compact context block for the planner prompt."""
        parts: List[str] = []

        if self.rag_context and self.rag_context != "(no long-term memories)":
            parts.append(f"LONG-TERM MEMORY (from past conversations):\n{self.rag_context}")

        if self.entities:
            parts.append(f"EXTRACTED ENTITIES: {self.entities}")

        if self.last_search_results:
            urls = "\n".join(
                f"  [{i+1}] {r['title']} — {r['url']}"
                for i, r in enumerate(self.last_search_results[:5])
            )
            parts.append(f"SEARCH RESULTS (use these URLs for fetch_content):\n{urls}")

        if self.selected_url:
            parts.append(f"SELECTED URL FOR FETCH: {self.selected_url}")

        if self.fetched_content:
            preview = self.fetched_content[:600]
            parts.append(f"FETCHED CONTENT (first 600 chars):\n{preview}")

        if self.extracted_metrics:
            parts.append(f"EXTRACTED METRICS: {self.extracted_metrics}")

        return "\n\n".join(parts) if parts else ""


def load_rag_context(query: str) -> str:
    """Retrieve relevant long-term memories from Qdrant for the current query.

    Returns formatted memory string or '(no long-term memories)'.
    Non-fatal — returns empty string on any error.
    """
    try:
        from memory.memory_manager import MemoryManager
        mm = MemoryManager()
        memories = mm.retrieve(query, top_k=3)
        return mm.format_memories(memories)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[agent-memory] RAG retrieval failed (non-fatal): %s", exc)
        return "(no long-term memories)"


def save_agent_result(query: str, response: str) -> None:
    """Persist agent turn to long-term memory for future RAG retrieval.

    Non-fatal — logs warning on failure.
    """
    try:
        from memory.memory_manager import MemoryManager
        mm = MemoryManager()
        mm.add_interaction(query, response)
        logger.info("[agent-memory] saved turn to long-term memory")
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[agent-memory] memory save failed (non-fatal): %s", exc)
