"""Deterministic MCP agent control-plane package."""

import logging

logger = logging.getLogger(__name__)


async def run_phase2_agent_loop(*args, **kwargs):
    from .control_plane import run_phase2_agent_loop as _run_phase2_agent_loop

    try:
        return await _run_phase2_agent_loop(*args, **kwargs)
    except Exception:  # pylint: disable=broad-except
        logger.exception("run_phase2_agent_loop crashed")
        raise

__all__ = ["run_phase2_agent_loop"]
