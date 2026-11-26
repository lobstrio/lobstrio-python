import asyncio
import datetime
import json

import pytest

from _helpers import API_KEY, Mock, async_client, client, envelope, ok
from lobstrio import Run, RunLogPage

CATALOG = {
    "object": "list",
    "data": [{"object": "operation", "id": "google_news/articles/search"}],
    "has_more": False,
    "next_cursor": None,
}


def test_catalog_list_get_status():
    mock = Mock(
        ok(CATALOG),
        ok(CATALOG),
        ok({"id": "google_news/articles/search"}),
        ok({"object": "operation_status"}),
    )
    c = client(mock)
    assert c.catalog.list() == CATALOG  # one page, as the API answers it
    c.catalog.list(source="google_news")
    assert c.catalog.get("google_news/articles/search")["id"] == "google_news/articles/search"
    assert c.catalog.status("google_news/articles/search")["object"] == "operation_status"
    urls = [str(r.url) for r in mock.requests]
    assert urls == [
        "https://api.lobstr.io/v2/catalog",
        "https://api.lobstr.io/v2/catalog?source=google_news",
        "https://api.lobstr.io/v2/catalog/google_news/articles/search",
        "https://api.lobstr.io/v2/catalog/google_news/articles/search/status",
    ]
    with pytest.raises(ValueError):
        c.catalog.get("google_news")


def test_balance():
    mock = Mock(ok({"object": "balance", "credits": 42, "plan": None}))
    assert client(mock).balance()["credits"] == 42
    assert str(mock.requests[0].url) == "https://api.lobstr.io/v2/balance"
    assert mock.requests[0].headers["authorization"] == f"Bearer {API_KEY}"


def test_accounts():
    account = {"object": "account", "id": "acc_1", "site": "x", "name": "acme", "status": "ok"}
    mock = Mock(
        ok({"object": "list", "data": [account], "has_more": False}),
        ok(account),
        ok({"object": "account", "id": "acc_1", "deleted": True}),
        ok(account),
    )
    c = client(mock)
    assert c.accounts.list(site="x")["data"] == [account]
    assert c.accounts.get("acc_1")["name"] == "acme"
    assert c.accounts.delete("acc_1")["deleted"] is True
    c.accounts.connect("x", {"auth_token": "t", "ct0": "c"}, country="FR")
    methods_urls = [(r.method, str(r.url)) for r in mock.requests]
    assert methods_urls == [
        ("GET", "https://api.lobstr.io/v2/accounts?site=x"),
        ("GET", "https://api.lobstr.io/v2/accounts/acc_1"),
        ("DELETE", "https://api.lobstr.io/v2/accounts/acc_1"),
        ("POST", "https://api.lobstr.io/v2/accounts"),
    ]
    assert json.loads(mock.requests[3].content) == {
        "site": "x",
        "cookies": {"auth_token": "t", "ct0": "c"},
        "country": "FR",
    }


def test_runs_list_filters_and_dates_in_utc():
    mock = Mock(ok({"object": "list", "data": [], "has_more": False, "next": None}))
    tz = datetime.timezone(datetime.timedelta(hours=2))
    page = client(mock).runs.list(
        limit=5,
        operation="google_maps/reviews/list",
        status="succeeded",
        via="api",
        created_after=datetime.datetime(2026, 11, 9, 10, 0, 0, tzinfo=tz),
        created_before=datetime.datetime(2026, 11, 10, 8, 0, 0, 123),
    )
    assert isinstance(page, RunLogPage)
    params = dict(mock.requests[0].url.params)
    assert params == {
        "limit": "5",
        "operation": "google_maps/reviews/list",
        "status": "succeeded",
        "via": "api",
        "created_after": "2026-11-09T08:00:00Z",
        "created_before": "2026-11-10T08:00:00Z",
    }


def test_runs_iterate_follows_next_as_a_path():
    first = {
        "object": "list",
        "data": [{"id": "run_3"}, {"id": "run_2"}],
        "has_more": True,
        "next": "/v2/runs?limit=2&starting_after=run_2",
    }
    second = {"object": "list", "data": [{"id": "run_1"}], "has_more": False, "next": None}
    mock = Mock(ok(first), ok(second))
    runs = list(client(mock).runs.iterate(limit=2))
    assert [r["id"] for r in runs] == ["run_3", "run_2", "run_1"]
    assert str(mock.requests[1].url) == "https://api.lobstr.io/v2/runs?limit=2&starting_after=run_2"

    mock = Mock(ok(first))
    assert len(list(client(mock).runs.iterate(max_records=1))) == 1
    assert len(mock.requests) == 1


def test_runs_get_and_response():
    mock = Mock(ok({"object": "run", "id": "run_1", "status": "succeeded"}), ok(envelope()))
    c = client(mock)
    assert c.runs.get("run_1")["status"] == "succeeded"
    response = c.runs.response("run_1")
    assert isinstance(response, Run) and response.results
    assert [str(r.url) for r in mock.requests] == [
        "https://api.lobstr.io/v2/runs/run_1",
        "https://api.lobstr.io/v2/runs/run_1/response",
    ]


def test_feedback():
    mock = Mock(ok({"object": "feedback", "id": "fbk_1", "created_at": "x", "kind": "bug"}))
    client(mock).feedback(
        "A field is missing", kind="bug", operation="google_maps/reviews/list", run="run_1"
    )
    assert json.loads(mock.requests[0].content) == {
        "message": "A field is missing",
        "kind": "bug",
        "operation": "google_maps/reviews/list",
        "run": "run_1",
    }
    assert "idempotency-key" not in mock.requests[0].headers


def test_async_resources():
    mock = Mock(
        ok(CATALOG),
        ok({"object": "balance", "credits": 1}),
        ok({"object": "list", "data": [], "has_more": False}),
        ok({"object": "list", "data": [{"id": "run_1"}], "has_more": False, "next": None}),
        ok(envelope()),
    )

    async def main():
        c = async_client(mock)
        assert (await c.catalog.list())["data"] == CATALOG["data"]
        assert (await c.balance())["credits"] == 1
        assert (await c.accounts.list())["data"] == []
        assert [r["id"] async for r in c.runs.iterate()] == ["run_1"]
        assert (await c.runs.response("run_1")).id == "run_1"

    asyncio.run(main())
