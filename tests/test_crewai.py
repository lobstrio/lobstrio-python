"""lobstrio.crewai: the tools as CrewAI tools (skipped without crewai)."""

import json
import os

import pytest

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
pytest.importorskip("crewai")

from crewai.tools import BaseTool  # noqa: E402

from _catalog import api  # noqa: E402
from _helpers import client, envelope, error, ok  # noqa: E402
from lobstrio.crewai import LobstrTool, lobstr_tools  # noqa: E402

NAMES = ["lobstr_find_operations", "lobstr_get_operation", "lobstr_run_operation"]


def invoke(tool, args):
    """Calls a tool the way a CrewAI agent does: its arguments validated, then run."""
    return tool.to_structured_tool().invoke(args)


def test_the_three_tools_and_one_per_operation():
    tools = lobstr_tools(client(api()), ["google_maps/places/search", "websites/contacts/get"])
    assert [t.name for t in tools] == [*NAMES, "google_maps_places_search", "websites_contacts_get"]
    assert all(isinstance(t, BaseTool) and isinstance(t, LobstrTool) for t in tools)
    schema = tools[3].args_schema.model_json_schema()
    assert schema["required"] == ["query"]
    assert {"query", "near", "page", "cursor", "fields", "limit"} <= set(schema["properties"])
    assert "limit" not in tools[4].args_schema.model_json_schema()["properties"]
    assert tools[2].args_schema.model_json_schema()["required"] == ["operation", "options"]


def test_an_operation_tool_sends_only_what_the_agent_gave():
    mock = api(ok(envelope("google_maps/places/search", credits=20)))
    search = lobstr_tools(client(mock), ["google_maps/places/search"])[3]
    answer = json.loads(
        invoke(search, {"query": "dentists in Austin, TX", "near": {"lat": 30.27, "lng": -97.74}})
    )
    assert answer["cost"] == {"credits": 20}
    # No `page` (its default) beside what the agent chose: the API applies its defaults.
    assert mock.bodies()[-1] == {
        "source": "google_maps",
        "operation": "places/search",
        "options": {"query": "dentists in Austin, TX", "near": {"lat": 30.27, "lng": -97.74}},
    }


def test_a_url_option_is_sent_as_text():
    mock = api(ok(envelope("websites/contacts/get")))
    contacts = lobstr_tools(client(mock), ["websites/contacts/get"])[3]
    invoke(contacts, {"website": "https://www.example-dentist.com/", "fields": ["emails"]})
    assert mock.bodies()[-1] == {
        "source": "websites",
        "operation": "contacts/get",
        "options": {"website": "https://www.example-dentist.com/"},
        "fields": ["emails"],
    }


def test_find_and_run_through_crewai():
    mock = api(ok(envelope("google_news/articles/search")))
    find, get, run = lobstr_tools(client(mock))
    assert json.loads(invoke(find, {}))["note"]
    found = json.loads(invoke(find, {"query": "news about a brand"}))
    assert found["operations"][0]["id"] == "google_news/articles/search"
    assert json.loads(invoke(get, {"operation": "google_maps/places/get"}))["input"]
    page = json.loads(
        invoke(
            run, {"operation": "google_news/articles/search", "options": {"query": "x"}, "limit": 1}
        )
    )
    assert page["count"] == 1
    assert mock.bodies()[-1]["limit"] == 1


def test_an_api_error_comes_back_to_the_agent_as_text():
    mock = api(error(401, "invalid_api_key", "authentication_error"))
    run = lobstr_tools(client(mock))[2]
    answer = json.loads(run.run(operation="google_news/articles/search", options={"query": "x"}))
    assert answer["error"]["code"] == "invalid_api_key"
    assert answer["error"]["doc_url"] == "https://docs.lobstr.io/errors#invalid_api_key"
