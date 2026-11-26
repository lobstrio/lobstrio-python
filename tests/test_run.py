import asyncio
import json
import uuid

import pytest

from _helpers import API_KEY, Mock, async_client, client, envelope, ok
from lobstrio import AsyncRun, AuthenticationError, Lobstr, Run, __version__
from lobstrio._base import DEFAULT_TIMEOUT


def test_run_sends_the_request_and_reads_the_answer():
    mock = Mock(ok(envelope(credits=1)))
    run = client(mock).run(
        "google_news/articles/search", {"query": "lobstr.io"}, limit=1, fields=["title", "url"]
    )
    request = mock.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.lobstr.io/v2/run"
    assert json.loads(request.content) == {
        "source": "google_news",
        "operation": "articles/search",
        "options": {"query": "lobstr.io"},
        "fields": ["title", "url"],
        "limit": 1,
    }
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["user-agent"].startswith(f"lobstrio-python/{__version__} (python/")
    assert uuid.UUID(request.headers["idempotency-key"]).version == 4
    assert isinstance(run, Run)
    assert run.id == "run_1"
    assert run.status == "succeeded"
    assert run.count == 1
    assert run.cost == {"credits": 1}
    assert run.balance == {"credits": 99}
    assert run.results == [{"object": "article", "id": "a1", "position": 1}]
    assert run.next is None and run.has_more is False
    assert run.operation_id == "google_news/articles/search"
    assert run.idempotency_key == request.headers["idempotency-key"]
    assert run.replayed is False
    assert run.to_dict()["request_id"] == "req_1"
    assert run["schema_url"].endswith("/google_news/articles/search")


def test_options_as_keyword_arguments_and_fields_as_a_string():
    mock = Mock(ok(envelope()))
    client(mock).run(
        "google_maps/places/search", {"query": "a"}, near={"lat": 1, "lng": 2}, fields="name, phone"
    )
    body = mock.bodies()[0]
    assert body["options"] == {"query": "a", "near": {"lat": 1, "lng": 2}}
    assert body["fields"] == ["name", "phone"]
    assert "limit" not in body and "timeout_s" not in body


def test_a_given_idempotency_key_is_sent_and_a_replay_is_read():
    mock = Mock(ok(envelope(), headers={"Idempotent-Replayed": "true"}))
    run = client(mock).run("google_news/articles/search", query="x", idempotency_key="my-key")
    assert mock.keys() == ["my-key"]
    assert run.replayed is True


def test_each_run_gets_its_own_key():
    mock = Mock(ok(envelope()), ok(envelope()))
    c = client(mock)
    c.run("google_news/articles/search", query="x")
    c.run("google_news/articles/search", query="x")
    first, second = mock.keys()
    assert first != second


def test_timeout_s_raises_the_http_timeout():
    mock = Mock(ok(envelope()), ok(envelope()))
    c = client(mock)
    c.run("google_news/articles/search", query="x")
    c.run("google_news/articles/search", query="x", timeout_s=200)
    assert mock.requests[0].extensions["timeout"]["read"] == DEFAULT_TIMEOUT
    assert mock.requests[1].extensions["timeout"]["read"] == 230  # timeout_s + 30 s
    assert mock.bodies()[1]["timeout_s"] == 200


def test_new_envelope_fields_are_attributes():
    mock = Mock(ok(envelope(warnings=["w"])))
    run = client(mock).run("google_news/articles/search", query="x")
    assert run.warnings == ["w"]
    with pytest.raises(AttributeError):
        run.not_a_field  # noqa: B018


@pytest.mark.parametrize("operation", ["google_maps", "places/search", "a/b/c/d", "", "a//b"])
def test_an_operation_is_source_resource_verb(operation):
    mock = Mock()
    with pytest.raises(ValueError, match="source/resource/verb"):
        client(mock).run(operation, {})
    assert mock.requests == []


def test_next_page_sends_next_as_it_is_with_a_new_key():
    nxt = {
        "source": "google_maps",
        "operation": "reviews/list",
        "options": {"place": "p", "cursor": "c2"},
        "limit": 2,
    }
    mock = Mock(
        ok(envelope("google_maps/reviews/list", next=nxt)),
        ok(envelope("google_maps/reviews/list", run_id="run_2")),
    )
    run = client(mock).run("google_maps/reviews/list", place="p", limit=2)
    page = run.next_page()
    assert mock.bodies()[1] == nxt
    assert page is not None and page.id == "run_2"
    assert len(set(mock.keys())) == 2
    assert page.next_page() is None
    assert len(mock.requests) == 2


def test_send_sends_a_raw_request():
    request = {"source": "google_news", "operation": "articles/search", "options": {"query": "x"}}
    mock = Mock(ok(envelope()))
    run = client(mock).send(request, idempotency_key="k")
    assert mock.bodies() == [request]
    assert mock.keys() == ["k"]
    assert run.id == "run_1"


def test_the_key_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("LOBSTR_API_KEY", "sk_from_env")
    mock = Mock(ok({"object": "balance", "credits": 5}))
    c = client(mock, api_key=None)
    c.balance()
    assert mock.requests[0].headers["authorization"] == "Bearer sk_from_env"


def test_a_missing_key_fails_at_the_first_call_that_needs_it(monkeypatch):
    monkeypatch.delenv("LOBSTR_API_KEY", raising=False)
    mock = Mock(ok({"object": "list", "data": [], "has_more": False}))
    c = client(mock, api_key=None)
    with pytest.raises(AuthenticationError) as info:
        c.run("google_news/articles/search", query="x")
    assert info.value.code == "missing_api_key"
    assert info.value.status is None
    assert mock.requests == []
    # The catalog needs no key.
    assert c.catalog.list()["data"] == []
    assert "authorization" not in mock.requests[0].headers


def test_the_catalog_never_gets_the_key():
    mock = Mock(ok({"object": "list", "data": [], "has_more": False}))
    client(mock).catalog.list()
    assert "authorization" not in mock.requests[0].headers


def test_the_key_is_never_in_a_repr():
    c = Lobstr(api_key=API_KEY)
    assert API_KEY not in repr(c)
    assert "api_key=set" in repr(c)
    c.close()


def test_async_run_and_next_page():
    nxt = {"source": "google_maps", "operation": "reviews/list", "options": {"cursor": "c"}}
    mock = Mock(
        ok(envelope("google_maps/reviews/list", next=nxt)),
        ok(envelope("google_maps/reviews/list", run_id="run_2")),
    )

    async def main():
        async with async_client(mock) as c:
            run = await c.run("google_maps/reviews/list", {"place": "p"})
            assert isinstance(run, AsyncRun)
            page = await run.next_page()
            assert page is not None and page.id == "run_2"
            assert await page.next_page() is None

    asyncio.run(main())
    assert mock.bodies()[1] == nxt
    assert mock.requests[0].headers["authorization"] == f"Bearer {API_KEY}"
