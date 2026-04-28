"""Multi-source search aggregator: combines results from multiple search engines."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def multi_source_search(
    query: str,
    execute_fn: Any,
    max_results_per_source: int = 5,
) -> List[Dict[str, str]]:
    """Execute search across multiple sources and aggregate results.
    
    Returns deduplicated list of {title, url, source} dicts.
    """
    from .types import PlannerAction
    
    # Define search sources
    sources = [
        ("browser-search", "search_web"),
        ("duckduckgo", "search"),
    ]
    
    async def _search_one(server: str, tool: str) -> List[Dict[str, str]]:
        """Search one source, return normalized results."""
        try:
            action = PlannerAction(
                server=server,
                tool=tool,
                arguments={"query": query, "limit": max_results_per_source},
            )
            result = await execute_fn(action)
            
            if not result.get("ok"):
                logger.warning("[multi-search] %s.%s failed: %s", server, tool, result.get("error"))
                return []
            
            payload = result.get("result", {})
            if isinstance(payload, dict):
                raw_results = payload.get("results", [])
            elif isinstance(payload, list):
                raw_results = payload
            else:
                return []
            
            normalized = []
            for r in raw_results:
                if isinstance(r, dict) and r.get("url"):
                    normalized.append({
                        "title": str(r.get("title", "")),
                        "url": str(r.get("url", "")),
                        "source": server,
                    })
            
            logger.info("[multi-search] %s.%s returned %d results", server, tool, len(normalized))
            return normalized
            
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[multi-search] %s.%s exception: %s", server, tool, exc)
            return []
    
    # Execute all searches in parallel
    tasks = [_search_one(server, tool) for server, tool in sources]
    results_per_source = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Flatten and deduplicate by URL
    seen_urls = set()
    aggregated = []
    
    for results in results_per_source:
        if isinstance(results, Exception):
            continue
        for item in results:
            url = item["url"]
            if url not in seen_urls:
                seen_urls.add(url)
                aggregated.append(item)
    
    logger.info("[multi-search] aggregated %d unique results from %d sources", 
                len(aggregated), len(sources))
    
    return aggregated
