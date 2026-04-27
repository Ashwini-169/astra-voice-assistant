from __future__ import annotations

from typing import Iterable, Tuple


def canonicalize_server_tool(server: str, tool: str) -> Tuple[str, str]:
    canonical_server = str(server or "").strip().lower()
    canonical_tool = str(tool or "").strip().lower()
    prefix = f"{canonical_server}."
    if canonical_server and canonical_tool.startswith(prefix):
        canonical_tool = canonical_tool[len(prefix) :]
    return canonical_server, canonical_tool


def build_tool_key(server: str, tool: str) -> str:
    canonical_server, canonical_tool = canonicalize_server_tool(server, tool)
    if not canonical_server:
        return canonical_tool
    if not canonical_tool:
        return canonical_server
    return f"{canonical_server}.{canonical_tool}"


def short_tool_name(tool: str) -> str:
    parts = [part for part in str(tool or "").strip().lower().split(".") if part]
    if not parts:
        return ""
    return parts[-1]


def infer_category(tool_name: str, arg_names: Iterable[str] | None = None) -> str:
    lowered = str(tool_name or "").strip().lower()
    args = {str(arg).strip().lower() for arg in (arg_names or []) if str(arg).strip()}

    if "timezone" in args or "time" in lowered or "timezone" in lowered or "date" in lowered:
        return "time"
    if "query" in args or "search" in lowered:
        return "search"
    if "url" in args or any(token in lowered for token in ("fetch", "read", "crawl", "open", "visit")):
        return "fetch"
    if any(token in lowered for token in ("summarize", "summary", "answer", "synthesize")):
        return "summarize"
    if "filepath" in args or any(token in lowered for token in ("save", "append", "store", "write")):
        return "storage"
    return "unknown"
