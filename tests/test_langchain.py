"""lobstrio.langchain: the tools as LangChain StructuredTools (skipped without langchain-core)."""

import json

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import ToolMessage  # noqa: E402
from langchain_core.tools import BaseToolkit, StructuredTool  # noqa: E402
from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: E402

from _catalog import CONTRACTS, api  # noqa: E402
from _helpers import client, envelope, error, ok  # noqa: E402
from lobstrio import Lobstr  # noqa: E402
from lobstrio.langchain import LobstrToolkit  # noqa: E402

NAMES = ["lobstr_find_operations", "lobstr_get_operation", "lobstr_run_operation"]


def call(tool, args, call_id="call_1"):
    """Invokes a tool as an agent does, with a tool call: the answer is a ToolMessage."""
    return tool.invoke({"name": tool.name, "args": args, "id": call_id, "type": "tool_call"})


def test_the_toolkit_gives_the_three_tools_and_one_per_operation():
    toolkit = LobstrToolkit(client(api()), ["google_maps/places/search"])
    assert isinstance(toolkit, BaseToolkit)
    tools = toolkit.get_tools()
    assert [t.name for t in tools] == [*NAMES, "google_maps_places_search"]
    assert all(isinstance(t, StructuredTool) for t in tools)
    assert [t.name for t in LobstrToolkit(client=client(api())).get_tools()] == NAMES
    one = LobstrToolkit(client(api()), operations="google_maps/places/search").get_tools()
    assert one[-1].name == "google_maps_places_search"


def test_the_client_defaults_to_lobstr(monkeypatch):
    monkeypatch.setenv("LOBSTR_API_KEY", "sk_test_from_env")
    assert isinstance(LobstrToolkit().client, Lobstr)


def test_the_model_sees_the_apis_own_schemas():
    tools = LobstrToolkit(client(api()), ["google_maps/places/search"]).get_tools()
    run = convert_to_openai_tool(tools[2])["function"]
    assert run["name"] == "lobstr_run_operation"
    assert run["parameters"]["required"] == ["operation", "options"]
    assert run["parameters"]["properties"]["options"]["type"] == "object"
    assert run["parameters"]["properties"]["fields"]["description"]
    search = convert_to_openai_tool(tools[3])["function"]
    contract = CONTRACTS["google_maps/places/search"]
    assert search["description"] == f"{contract['summary']}\n\n{contract['use_when']}"
    assert search["parameters"]["required"] == ["query"]
    for name, schema in contract["input"]["properties"].items():
        assert search["parameters"]["properties"][name] == schema
    assert set(search["parameters"]["properties"]) == {
        *contract["input"]["properties"],
        "fields",
        "limit",
    }


def test_a_tool_call_runs_the_operation():
    mock = api(ok(envelope("google_maps/places/search", credits=20)))
    tools = LobstrToolkit(client(mock), ["google_maps/places/search"]).get_tools()
    message = call(tools[3], {"query": "dentists in Austin, TX", "limit": 3, "fields": ["name"]})
    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call_1"
    answer = json.loads(message.content)
    assert answer["cost"] == {"credits": 20} and answer["count"] == 1
    assert mock.bodies()[-1] == {
        "source": "google_maps",
        "operation": "places/search",
        "options": {"query": "dentists in Austin, TX"},
        "fields": ["name"],
        "limit": 3,
    }


def test_find_get_and_run_through_langchain():
    mock = api(ok(envelope("google_news/articles/search")))
    find, get, run = LobstrToolkit(client(mock)).get_tools()
    found = json.loads(call(find, {"query": "news about a brand"}).content)
    assert found["operations"][0]["id"] == "google_news/articles/search"
    assert json.loads(find.invoke({}))["note"]  # the query is optional
    contract = json.loads(call(get, {"operation": "google_maps/places/search"}).content)
    assert contract["input"]["required"] == ["query"]
    page = json.loads(
        call(run, {"operation": "google_news/articles/search", "options": {"query": "x"}}).content
    )
    assert page["results"][0]["id"] == "a1"


def test_an_api_error_comes_back_to_the_agent_as_text():
    mock = api(error(429, "concurrency_limit", "rate_limit_error", retry_after=90))
    run = LobstrToolkit(client(mock)).get_tools()[2]
    message = call(run, {"operation": "google_news/articles/search", "options": {"query": "x"}})
    assert message.status == "success"  # the loop goes on; the agent reads the error
    answer = json.loads(message.content)
    assert answer["error"]["code"] == "concurrency_limit"
    assert answer["error"]["doc_url"] == "https://docs.lobstr.io/errors#concurrency_limit"
    assert answer["error"]["retry_after"] == 90
