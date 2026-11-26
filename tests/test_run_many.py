import asyncio
import json
import threading
import time

import httpx

from _helpers import Mock, async_client, client, envelope, error, ok
from lobstrio import AsyncRun, InvalidRequestError, Run

OP = "google_news/articles/search"


def by_query(request: httpx.Request) -> httpx.Response:
    """Answers each call after a delay that reverses their order, a run named after its query."""
    query = json.loads(request.content)["options"]["query"]
    if query == "bad":
        return error(422, "invalid_query", "invalid_request_error", param="options.query")
    time.sleep({"a": 0.06, "b": 0.03, "c": 0.0}.get(query, 0.0))
    return ok(envelope(OP, run_id=f"run_{query}"))


def test_run_many_keeps_the_order_and_returns_errors_in_place():
    mock = Mock(default=by_query)
    results = client(mock).run_many(
        [
            {"operation": OP, "options": {"query": "a"}},
            {"operation": OP, "options": {"query": "bad"}},
            (OP, {"query": "b"}),
            {"source": "google_news", "operation": "articles/search", "options": {"query": "c"}},
            {"operation": "not-an-operation"},
        ],
        concurrency=4,
    )
    assert [type(r).__name__ for r in results] == [
        "Run",
        "InvalidRequestError",
        "Run",
        "Run",
        "ValueError",
    ]
    assert results[0].id == "run_a" and results[2].id == "run_b" and results[3].id == "run_c"
    assert isinstance(results[1], InvalidRequestError)
    assert results[1].param == "options.query"
    assert len(mock.requests) == 4  # the malformed call never left


def test_run_many_runs_at_most_concurrency_calls_at_a_time():
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow(request: httpx.Request) -> httpx.Response:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.02)
        with lock:
            state["now"] -= 1
        return ok(envelope(OP))

    mock = Mock(default=slow)
    results = client(mock).run_many([(OP, {"query": str(i)}) for i in range(8)], concurrency=2)
    assert all(isinstance(r, Run) for r in results)
    assert state["peak"] == 2


def test_run_many_waits_on_concurrency_limit_with_the_same_key():
    busy = error(429, "concurrency_limit", "rate_limit_error", retryable=True, retry_after=3)
    mock = Mock(busy, busy, ok(envelope(OP)))
    c = client(mock, max_retries=0)
    [result] = c.run_many([{"operation": OP, "options": {"query": "a"}, "idempotency_key": "k1"}])
    assert isinstance(result, Run)
    assert mock.keys() == ["k1", "k1", "k1"]
    assert len(c.sleeps) == 2 and all(3 <= s <= 3.25 for s in c.sleeps)


def test_run_many_waits_on_concurrency_limit_up_to_100_times():
    busy = error(429, "concurrency_limit", "rate_limit_error", retryable=True, retry_after=1)
    mock = Mock(default=busy)
    c = client(mock, max_retries=0)
    [result] = c.run_many([(OP, {"query": "a"})])
    assert result.code == "concurrency_limit"
    assert len(mock.requests) == 101 and len(c.sleeps) == 100
    assert len(set(mock.keys())) == 1


def test_run_many_does_not_wait_on_other_rate_limits():
    mock = Mock(
        error(429, "account_rate_limited", "rate_limit_error", retryable=True, retry_after=5)
    )
    c = client(mock, max_retries=0)
    [result] = c.run_many([(OP, {"query": "a"})])
    assert result.code == "account_rate_limited"
    assert len(mock.requests) == 1


def test_run_many_of_nothing():
    assert client(Mock()).run_many([]) == []


def test_async_run_many_keeps_the_order():
    def answer(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["options"]["query"]
        if query == "bad":
            return error(402, "insufficient_credits", "billing_error")
        return ok(envelope(OP, run_id=f"run_{query}"))

    mock = Mock(default=answer)

    async def main():
        c = async_client(mock)
        return await c.run_many([(OP, {"query": q}) for q in ("a", "bad", "c")], concurrency=2)

    results = asyncio.run(main())
    assert isinstance(results[0], AsyncRun) and results[0].id == "run_a"
    assert results[1].code == "insufficient_credits"
    assert results[2].id == "run_c"
