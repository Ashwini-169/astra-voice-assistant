from services.agent_control.execution_result import execution_ok, execution_status
from services.agent_control.identity import infer_category
from services.agent_control.intent_router import route_intent
from services.agent_control.resolution import build_tool_index, resolve_action_server
from services.agent_control.response_utils import final_response_from_result
from services.agent_control.security import enforce_security
from services.agent_control.transitions import get_allowed_categories
from services.agent_control.types import CatalogTool, PlannerAction, ToolSchema
from services.agent_control.validation import (
    VALIDATION_MISSING_ARGS,
    VALIDATION_VALID,
    validate_action,
)


def test_route_intent_news_prefers_search_tools():
    schemas = {
        "duckduckgo.search": ToolSchema(name="search", server="duckduckgo", required_args=["query"], optional_args=[]),
        "duckduckgo.fetch": ToolSchema(name="fetch", server="duckduckgo", required_args=["url"], optional_args=[]),
    }

    out = route_intent("latest ai news", schemas)
    assert out == ["duckduckgo.search"]


def test_route_intent_time_prefers_time_tools():
    schemas = {
        "time.get_current_time": ToolSchema(name="get_current_time", server="time", required_args=[], optional_args=["timezone"]),
        "duckduckgo.search": ToolSchema(name="search", server="duckduckgo", required_args=["query"], optional_args=[]),
    }

    out = route_intent("Tell me the time in Kolkata zone", schemas)
    assert out == ["time.get_current_time"]


def test_route_intent_url_prefers_fetch_tools():
    schemas = {
        "fetch.fetch": ToolSchema(name="fetch", server="fetch", required_args=["url"], optional_args=[]),
        "duckduckgo.search": ToolSchema(name="search", server="duckduckgo", required_args=["query"], optional_args=[]),
    }

    out = route_intent("read and fetch detail https://cursor.com/pricing", schemas)
    assert out == ["fetch.fetch"]


def test_validate_action_missing_required_args():
    schema_map = {
        "duckduckgo.fetch": ToolSchema(name="fetch", server="duckduckgo", required_args=["url"], optional_args=[]),
    }
    action = PlannerAction(tool="fetch", server="duckduckgo", arguments={})

    result = validate_action(action, schema_map, {"duckduckgo.fetch"})
    assert result.status == VALIDATION_MISSING_ARGS
    assert result.missing == ["url"]


def test_validate_action_valid():
    schema_map = {
        "duckduckgo.fetch": ToolSchema(name="fetch", server="duckduckgo", required_args=["url"], optional_args=[]),
    }
    action = PlannerAction(tool="fetch", server="duckduckgo", arguments={"url": "https://example.com"})

    result = validate_action(action, schema_map, {"duckduckgo.fetch"})
    assert result.status == VALIDATION_VALID


def test_validate_action_accepts_canonicalized_full_tool_name():
    schema_map = {
        "search_ai_news_server.duckduckgo.search": ToolSchema(
            name="duckduckgo.search",
            server="search_ai_news_server",
            required_args=["query"],
            optional_args=[],
        ),
    }
    action = PlannerAction(
        tool="search_ai_news_server.duckduckgo.search",
        server="search_ai_news_server",
        arguments={"query": "latest ai news"},
    )

    result = validate_action(action, schema_map, {"search_ai_news_server.duckduckgo.search"})
    assert result.status == VALIDATION_VALID


def test_infer_category_uses_tool_identity_not_exact_tool_name():
    assert infer_category("duckduckgo.search") == "search"
    assert infer_category("duckduckgo.read_page", ["url"]) == "fetch"
    assert infer_category("news_summarizer") == "summarize"
    assert infer_category("get_current_time") == "time"


def test_get_allowed_categories_uses_prior_tool_category():
    state = {
        "steps": [
            {
                "tool": {
                    "server": "search_ai_news_server",
                    "name": "duckduckgo.search",
                    "full": "search_ai_news_server.duckduckgo.search",
                    "short": "search",
                    "category": "search",
                }
            }
        ]
    }

    assert get_allowed_categories(state) == ["fetch", "summarize"]


def test_final_response_from_result_accepts_string_payload():
    assert final_response_from_result({"ok": True, "result": "plain text"}) == "plain text"


def test_execution_status_uses_ok_when_status_is_missing():
    result = {"ok": True, "result": "plain text"}

    assert execution_ok(result) is True
    assert execution_status(result) == "ok"


def test_resolve_action_server_prefers_index_over_planner_server():
    schema_map = {
        "browser-search.search_web": ToolSchema(
            name="search_web",
            server="browser-search",
            required_args=["query"],
            optional_args=["limit"],
        ),
    }
    action = PlannerAction(
        tool="search_web",
        server="cloud-search",
        arguments={"query": "latest ai news"},
    )

    resolved = resolve_action_server(
        action,
        tool_index=build_tool_index(schema_map),
        catalog=[],
        candidate_keys={"browser-search.search_web"},
    )

    assert resolved.server == "browser-search"
    assert resolved.tool == "search_web"


def test_resolve_action_server_falls_back_to_catalog_scan():
    action = PlannerAction(
        tool="search_web",
        server="cloud-search",
        arguments={"query": "latest ai news"},
    )
    catalog = [
        CatalogTool(
            server="browser-search",
            tool="search_web",
            description="",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            health={"availability": 1.0, "latency": 0.0, "failure_rate": 0.0},
        )
    ]

    resolved = resolve_action_server(
        action,
        tool_index={},
        catalog=catalog,
        candidate_keys={"browser-search.search_web"},
    )

    assert resolved.server == "browser-search"
    assert resolved.tool == "search_web"


def test_security_blocks_localhost_url():
    action = PlannerAction(tool="fetch", server="duckduckgo", arguments={"url": "http://localhost:8000"})
    ok, message = enforce_security(action, {"duckduckgo"})
    assert ok is False
    assert "unsafe" in str(message)
