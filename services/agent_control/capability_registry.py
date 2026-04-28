"""Capability registry: maps abstract capabilities to tool categories and priority.

This is the single source of truth for tool selection policy.
No tool names are hardcoded — everything resolves at runtime from the live catalog.

Priority rules are loaded from mcp_config.json (optional) under a
"capability_policy" key. If absent, defaults are used.

Schema (mcp_config.json):
{
  "capability_policy": {
    "search":    [{"server": "duckduckgo", "priority": 0.95}, ...],
    "fetch":     [{"server": "duckduckgo", "priority": 0.95}, ...],
    "storage":   [{"server": "obsidian",   "priority": 0.95}, ...],
    "time":      [{"server": "time",       "priority": 1.0},  ...],
    "summarize": []
  }
}
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "mcp_config.json"

# Default priority order per category (server name prefix match, no tool names)
_DEFAULT_POLICY: Dict[str, List[Dict]] = {
    "search":    [{"server": "duckduckgo", "priority": 0.95},
                  {"server": "browser-search", "priority": 0.80}],
    "fetch":     [{"server": "duckduckgo", "priority": 0.95},
                  {"server": "browser-search", "priority": 0.80},
                  {"server": "fetch", "priority": 0.70}],
    "storage":   [{"server": "obsidian", "priority": 0.95},
                  {"server": "file", "priority": 0.60}],
    "time":      [{"server": "time", "priority": 1.0}],
    "summarize": [],
    "unknown":   [],
}


@lru_cache(maxsize=1)
def _load_policy() -> Dict[str, List[Dict]]:
    """Load capability policy from mcp_config.json if present, else use defaults.

    Cached at module level — call _load_policy.cache_clear() after config changes.
    """
    try:
        if _CONFIG_PATH.exists():
            raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            policy = raw.get("capability_policy")
            if isinstance(policy, dict) and policy:
                logger.info("[capability-registry] loaded policy from %s", _CONFIG_PATH)
                merged = dict(_DEFAULT_POLICY)
                merged.update(policy)
                return merged
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[capability-registry] failed to load policy (using defaults): %s", exc)
    return dict(_DEFAULT_POLICY)


def priority_for_tool(server: str, category: str) -> float:
    """Return configured priority for a tool given its server and category.

    Higher = preferred. Falls back to 0.5 if not in policy.
    """
    policy = _load_policy()
    rules = policy.get(category, [])
    server_lower = server.lower()
    for rule in rules:
        rule_server = str(rule.get("server", "")).lower()
        if rule_server and server_lower.startswith(rule_server):
            return float(rule.get("priority", 0.5))
    return 0.5


def category_chain(starting_category: str) -> List[str]:
    """Return the expected execution chain for a starting category.

    Drives transitions.py without hardcoding tool names.
    """
    chains: Dict[str, List[str]] = {
        "search":   ["search", "fetch", "summarize", "storage"],
        "fetch":    ["fetch", "summarize", "storage"],
        "time":     ["time", "search", "fetch", "summarize", "storage"],
        "storage":  ["storage"],
        "summarize": ["summarize", "storage"],
        "unknown":  ["search", "fetch", "summarize", "storage", "time"],
    }
    return chains.get(starting_category, ["search", "fetch", "summarize", "storage"])
