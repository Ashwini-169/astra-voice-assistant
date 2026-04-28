from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional


def clean_content(text: str) -> str:
    """Strip navigation junk, keep only substantive lines."""
    lines = text.splitlines()
    kept = [l for l in lines if len(l.strip()) > 40]
    return "\n".join(kept[:80])


def summarize_fetch_result(
    exec_result: Dict[str, Any],
    user_query: str,
    source_url: str,
    llm_call: Callable[[str], str],
) -> Optional[str]:
    """Summarize fetched page content via LLM. Returns None if content is empty."""
    payload = exec_result.get("result", {})
    raw = ""
    if isinstance(payload, dict):
        raw = str(payload.get("content") or payload.get("text") or "")
    elif isinstance(payload, str):
        raw = payload

    content = clean_content(raw)
    if not content:
        return None

    # Extract domain from URL for source attribution
    domain = re.sub(r'^https?://(www\.)?', '', source_url).split('/')[0]

    prompt = (
        f"The user asked: {user_query}\n\n"
        f"Here is content from {source_url}:\n\n"
        f"{content[:4000]}\n\n"
        "Summarize the content with:\n"
        "• Key developments\n"
        "• Important facts\n"
        "• Numbers/statistics (if present)\n"
        "• Entities (people, countries, organizations)\n\n"
        "Use 6-10 bullet points depending on content richness. "
        "Do not limit artificially. "
        "Do not mention navigation, ads, or website structure. "
        "Format each point with • prefix.\n\n"
        "End with a blank line and: Source: <domain only>"
    )
    summary = llm_call(prompt)
    
    # Ensure source attribution is present
    if "Source:" not in summary and "source:" not in summary:
        summary = f"{summary}\n\nSource: {domain}"
    
    return summary


def final_response_from_result(exec_result: Dict[str, Any]) -> str:
    """Extract and format final response from tool execution result."""
    payload = exec_result.get("result", "")
    
    # Handle time tool responses
    if isinstance(payload, str) and ("timezone" in payload.lower() or "datetime" in payload.lower()):
        try:
            import json
            time_data = json.loads(payload)
            if "source" in time_data and "target" in time_data:
                # Time conversion response
                source_tz = time_data["source"]["timezone"]
                source_time = time_data["source"]["datetime"].split("T")[1].split("-")[0].split("+")[0][:5]
                target_tz = time_data["target"]["timezone"]
                target_time = time_data["target"]["datetime"].split("T")[1].split("-")[0].split("+")[0][:5]
                return f"🕒 Time conversion:\n\n{source_time} in {source_tz}\n= {target_time} in {target_tz}"
            elif "timezone" in time_data and "datetime" in time_data:
                # Current time response
                tz = time_data["timezone"]
                dt = time_data["datetime"]
                time_part = dt.split("T")[1].split("-")[0].split("+")[0][:5]
                date_part = dt.split("T")[0]
                return f"🕒 Current time in {tz}:\n\n{time_part}\n{date_part}"
        except Exception:  # pylint: disable=broad-except
            pass
    
    # Handle list responses (Obsidian file lists, etc.)
    if isinstance(payload, list):
        if len(payload) == 0:
            return "No items found."
        items = [f"• {item}" for item in payload[:20]]
        return "\n".join(items)
    
    # Handle dict responses
    if isinstance(payload, dict):
        return str(payload.get("answer") or payload.get("response") or payload)
    
    return str(payload)
