"""The agent tools' core, framework-free: what lobstrio.langchain and lobstrio.crewai wrap."""

import json
import subprocess
import sys

import httpx
import pytest

from _catalog import CONTRACTS, api, paths
from _helpers import async_client, client, envelope, error, ok
from lobstrio import Lobstr
from lobstrio._tools import (
    FIND_OPERATIONS,
    GET_OPERATION,
    RUN_OPERATION,
    LobstrTools,
    compact,
    is_list,
    ranked,
)

NEXT = {
    "source": "google_maps",
    "operation": "places/search",
    "options": {"query": "dentists in Austin, TX", "cursor": "c2"},
    "fields": ["name", "phone"],
    "limit": 5,
}


def places(n: int = 2):
    return [{"object": "place", "id": f"p{i}", "position": i, "name": f"P{i}"} for i in range(n)]


def test_the_three_tools_with_a_description_for_every_argument():
    tools = LobstrTools(client(api())).tools()
    assert [t.name for t in tools] == [FIND_OPERATIONS, GET_OPERATION, RUN_OPERATION]
    assert [t.name for t in tools] == [
        "lobstr_find_operations",
        "lobstr_get_operation",
        "lobstr_run_operation",
    ]
    for tool in tools:
        assert tool.description
        assert tool.parameters["type"] == "object"
        for name, schema in tool.parameters["properties"].items():
            assert schema.get("description"), (tool.name, name)
    run = tools[2].parameters
    assert run["required"] == ["operation", "options"]
    assert set(run["properties"]) == {"operation", "options", "fields", "limit"}
    assert "fields" in tools[2].description and "credits" in tools[2].description


def test_the_catalog_is_read_once_without_a_key():
    mock = api()
    tools = LobstrTools(client(mock))
    tools.find_operations("dentists")
    tools.find_operations("news about a brand")
    tools.find_operations("")
    assert paths(mock) == ["GET /v2/catalog"]
    assert "authorization" not in mock.requests[0].headers


def test_find_ranks_the_operations_by_the_query():
    answer = json.loads(LobstrTools(client(api())).find_operations("dentists in Austin, TX"))
    found = answer["operations"]
    assert found[0] == {
        "id": "google_maps/places/search",
        "title": "Search places",
        "summary": "One page of places for a Google Maps search, 20 at most.",
        "credits_per_call": 20,
    }
    assert "note" not in answer
    emails = json.loads(LobstrTools(client(api())).find_operations("emails of a company website"))
    assert emails["operations"][0]["id"] == "websites/contacts/get"


def test_an_empty_query_lists_every_operation_one_line_each():
    answer = json.loads(LobstrTools(client(api())).find_operations(""))
    assert answer["operations"] == [
        "google_maps/places/get: Get a place on google.com/maps, 1 credit a call",
        "google_maps/places/search: Search places on google.com/maps, 20 credits a call",
        "google_news/articles/search: Search articles on news.google.com, 1 credit a call",
        "websites/contacts/get: Get contacts on any website, 1 credit a call",
    ]
    assert answer["note"] == "Ask with words for the details of those that fit."


def test_words_that_match_nothing_list_every_operation_with_a_note():
    answer = json.loads(LobstrTools(client(api())).find_operations("zzz qqq"))
    assert len(answer["operations"]) == 4
    assert answer["note"].startswith("Nothing matched these words")


def test_ranking_keeps_the_close_ones_and_at_most_five():
    ops = [
        {"id": f"s{i}/things/search", "title": "Search things", "summary": "", "use_when": ""}
        for i in range(8)
    ]
    assert len(ranked(ops, "things")) == 5
    assert ranked(ops, "") == ops


def test_get_operation_gives_the_contract_compact():
    mock = api()
    text = LobstrTools(client(mock)).get_operation("google_maps/places/search")
    assert ": " not in text.split('"input"')[0]  # compact JSON, no spaces
    answer = json.loads(text)
    contract = CONTRACTS["google_maps/places/search"]
    assert answer == {
        "id": "google_maps/places/search",
        "title": "Search places",
        "summary": contract["summary"],
        "use_when": contract["use_when"],
        "input": contract["input"],
        "record_fields": [
            "object",
            "id",
            "name",
            "address (full, city)",
            "categories",
            "phone",
        ],
        "price": contract["price"],
        "pagination": contract["pagination"],
    }
    assert paths(mock) == ["GET /v2/catalog/google_maps/places/search"]


def test_get_operation_errors_come_back_as_text():
    tools = LobstrTools(client(api()))
    answer = json.loads(tools.get_operation("google_maps/places/serch"))
    assert answer["error"]["code"] == "unknown_operation"
    assert answer["error"]["message"] == "The message of unknown_operation."
    assert answer["error"]["doc_url"] == "https://docs.lobstr.io/errors#unknown_operation"
    bad = json.loads(tools.get_operation("google_maps"))
    assert "source/resource/verb" in bad["error"]["message"]


def test_run_operation_answers_one_page_with_next_in_its_own_arguments():
    mock = api(ok(envelope("google_maps/places/search", places(2), next=NEXT, credits=20)))
    text = LobstrTools(client(mock)).run_operation(
        "google_maps/places/search",
        {"query": "dentists in Austin, TX"},
        fields=["name", "phone"],
        limit=5,
    )
    assert mock.bodies() == [
        {
            "source": "google_maps",
            "operation": "places/search",
            "options": {"query": "dentists in Austin, TX"},
            "fields": ["name", "phone"],
            "limit": 5,
        }
    ]
    answer = json.loads(text)
    assert text == compact(answer)
    assert answer == {
        "count": 2,
        "cost": {"credits": 20},
        "has_more": True,
        "next": {
            "operation": "google_maps/places/search",
            "options": {"query": "dentists in Austin, TX", "cursor": "c2"},
            "fields": ["name", "phone"],
            "limit": 5,
        },
        "results": places(2),
    }


def test_run_operation_without_fields_or_limit_and_the_last_page():
    mock = api(ok(envelope("google_news/articles/search")))
    answer = json.loads(
        LobstrTools(client(mock)).run_operation("google_news/articles/search", {"query": "x"})
    )
    assert mock.bodies()[0] == {
        "source": "google_news",
        "operation": "articles/search",
        "options": {"query": "x"},
    }
    assert answer["next"] is None and answer["has_more"] is False


def test_an_api_error_comes_back_as_the_apis_answer_never_raised():
    mock = api(error(402, "insufficient_credits", "billing_error"))
    answer = json.loads(
        LobstrTools(client(mock)).run_operation("google_maps/places/search", {"query": "x"})
    )
    assert answer == {
        "error": {
            "type": "billing_error",
            "code": "insufficient_credits",
            "message": "The message of insufficient_credits.",
            "param": None,
            "doc_url": "https://docs.lobstr.io/errors#insufficient_credits",
            "request_id": "req_insufficient_credits",
            "retryable": False,
        }
    }


def test_an_sdk_error_comes_back_in_the_same_shape():
    mock = api(httpx.ConnectError("refused"))
    tools = LobstrTools(client(mock, max_retries=0))
    lost = json.loads(tools.run_operation("google_news/articles/search", {"query": "x"}))
    assert lost["error"]["type"] == "api_error"
    assert lost["error"]["code"] == "connection_error"
    assert lost["error"]["message"].startswith("Could not reach the API")
    wrong = json.loads(tools.run_operation("places/search", {"query": "x"}))
    assert list(wrong["error"]) == ["message"]
    assert "source/resource/verb" in wrong["error"]["message"]


def test_arguments_an_agent_got_wrong_are_left_out():
    mock = api(ok(envelope("google_news/articles/search")))
    tools = LobstrTools(client(mock))
    tools.run_operation("google_news/articles/search", "lobstr", fields="title", limit=0)  # type: ignore[arg-type]
    assert mock.bodies()[-1] == {
        "source": "google_news",
        "operation": "articles/search",
        "options": {},
    }


def test_the_sdk_retries_underneath_with_the_same_key():
    mock = api(
        error(503, "site_unavailable", "site_error", retryable=True, retry_after=1),
        ok(envelope("google_news/articles/search")),
    )
    c = client(mock)
    answer = json.loads(LobstrTools(c).run_operation("google_news/articles/search", {"query": "x"}))
    assert answer["count"] == 1
    first, second = mock.keys()
    assert first == second and first is not None
    assert c.sleeps == [1]  # type: ignore[attr-defined]


def test_a_tool_per_operation_takes_its_options_as_arguments():
    tools = LobstrTools(
        client(api()), ["google_maps/places/search", "websites/contacts/get"]
    ).tools()
    assert [t.name for t in tools[3:]] == ["google_maps_places_search", "websites_contacts_get"]
    search, contacts = tools[3], tools[4]
    contract = CONTRACTS["google_maps/places/search"]
    assert search.description == f"{contract['summary']}\n\n{contract['use_when']}"
    assert search.parameters["required"] == ["query"]
    assert list(search.parameters["properties"]) == [
        "query",
        "near",
        "page",
        "cursor",
        "fields",
        "limit",
    ]
    assert search.parameters["properties"]["near"] == contract["input"]["properties"]["near"]
    # A get answers one record: fields, no limit.
    assert list(contacts.parameters["properties"]) == ["website", "max_pages", "fields"]
    # The contract itself is left as it was.
    assert "fields" not in contract["input"]["properties"]


def test_a_tool_per_operation_runs_and_gives_next_in_its_own_arguments():
    mock = api(ok(envelope("google_maps/places/search", places(1), next=NEXT, credits=20)))
    search = LobstrTools(client(mock), ["google_maps/places/search"]).tools()[3]
    answer = json.loads(
        search(query="dentists in Austin, TX", near=None, fields=["name", "phone"], limit=5)
    )
    assert paths(mock) == ["GET /v2/catalog/google_maps/places/search", "POST /v2/run"]
    assert mock.bodies()[-1] == {
        "source": "google_maps",
        "operation": "places/search",
        "options": {"query": "dentists in Austin, TX"},
        "fields": ["name", "phone"],
        "limit": 5,
    }
    assert answer["next"] == {
        "query": "dentists in Austin, TX",
        "cursor": "c2",
        "fields": ["name", "phone"],
        "limit": 5,
    }


def test_a_get_tool_drops_a_limit_it_does_not_take():
    mock = api(ok(envelope("websites/contacts/get")))
    contacts = LobstrTools(client(mock), ["websites/contacts/get"]).tools()[3]
    answer = json.loads(contacts(website="https://example-dentist.com", limit=3, max_pages=None))
    assert answer["next"] is None
    assert mock.bodies()[-1] == {
        "source": "websites",
        "operation": "contacts/get",
        "options": {"website": "https://example-dentist.com"},
    }


@pytest.mark.parametrize(
    "pagination, listed",
    [
        (None, False),
        ({"style": "none", "page_size": 1}, False),
        ({"style": "none", "page_size": 100}, True),
        ({"style": "cursor", "page_size": 20}, True),
        ({"style": "page", "page_size": 42}, True),
    ],
)
def test_a_list_is_an_operation_that_answers_pages(pagination, listed):
    assert is_list({"pagination": pagination}) is listed


def test_contracts_are_read_once():
    mock = api(ok(envelope("google_maps/places/search")))
    tools = LobstrTools(client(mock), ["google_maps/places/search"])
    tools.tools()
    tools.tools()
    tools.get_operation("google_maps/places/search")
    assert paths(mock) == ["GET /v2/catalog/google_maps/places/search"]


def test_wrong_operations_fail_when_the_tools_are_made():
    with pytest.raises(ValueError):
        LobstrTools(client(api()), ["google_maps/places"])
    with pytest.raises(Exception) as info:
        LobstrTools(client(api()), ["google_maps/places/serch"]).tools()
    assert getattr(info.value, "code", None) == "unknown_operation"


def test_the_client_defaults_to_lobstr_and_must_be_sync(monkeypatch):
    monkeypatch.setenv("LOBSTR_API_KEY", "sk_test_from_env")
    assert isinstance(LobstrTools().client, Lobstr)
    with pytest.raises(TypeError):
        LobstrTools(async_client(api()))  # type: ignore[arg-type]


def test_the_base_install_imports_no_agent_framework():
    code = (
        "import sys, lobstrio, lobstrio._tools; "
        "print(sorted({m.split('.')[0] for m in sys.modules} & {'langchain', 'langchain_core', 'crewai'}))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


@pytest.mark.parametrize(
    "module, framework, extra",
    [
        ("lobstrio.langchain", "langchain_core", "langchain"),
        ("lobstrio.crewai", "crewai", "crewai"),
    ],
)
def test_an_integration_without_its_framework_says_what_to_install(module, framework, extra):
    code = (
        f"import sys; sys.modules[{framework!r}] = None\n"
        f"try:\n    import {module}\nexcept ImportError as e:\n    print(e)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert f'pip install "lobstrio[{extra}]"' in out.stdout
