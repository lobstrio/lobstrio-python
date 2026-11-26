import asyncio

import httpx
import pytest

from _helpers import Mock, async_client, client, envelope, error, ok
from lobstrio import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    InvalidRequestError,
    RateLimitError,
    SiteError,
)

OP = "google_news/articles/search"


def test_retry_after_header_is_waited_and_the_key_kept():
    mock = Mock(
        error(503, "site_challenge", "site_error", retryable=True, headers={"Retry-After": "3"}),
        ok(envelope()),
    )
    c = client(mock)
    run = c.run(OP, query="x")
    assert run.id == "run_1"
    assert c.sleeps == [3.0]
    first, second = mock.keys()
    assert first == second


def test_retry_after_in_the_body_wins():
    mock = Mock(
        error(
            429,
            "concurrency_limit",
            "rate_limit_error",
            retryable=True,
            retry_after=2,
            headers={"Retry-After": "9"},
        ),
        ok(envelope()),
    )
    c = client(mock)
    c.run(OP, query="x")
    assert c.sleeps == [2]


def test_idempotency_in_flight_waits_two_seconds():
    mock = Mock(
        error(409, "idempotency_in_flight", "invalid_request_error", retryable=True), ok(envelope())
    )
    c = client(mock)
    c.run(OP, query="x", idempotency_key="k")
    assert c.sleeps == [2.0]
    assert mock.keys() == ["k", "k"]


def test_retries_stop_at_max_retries():
    busy = error(500, "internal_error", "api_error", retryable=True)
    mock = Mock(busy, busy, busy, busy)
    c = client(mock, max_retries=2)
    with pytest.raises(APIError) as info:
        c.run(OP, query="x")
    assert info.value.code == "internal_error"
    assert len(mock.requests) == 3
    assert len(set(mock.keys())) == 1
    # Exponential backoff with jitter when no retry_after is given.
    assert 0.25 <= c.sleeps[0] <= 0.5 and 0.5 <= c.sleeps[1] <= 1.0


def test_a_failure_that_will_not_pass_is_not_retried():
    mock = Mock(error(422, "invalid_options", "invalid_request_error", param="options.query"))
    c = client(mock)
    with pytest.raises(InvalidRequestError):
        c.run(OP, query="")
    assert len(mock.requests) == 1 and c.sleeps == []


def test_a_retry_after_of_hours_is_not_waited():
    mock = Mock(
        error(429, "account_quota_exceeded", "rate_limit_error", retryable=True, retry_after=36000)
    )
    c = client(mock)
    with pytest.raises(RateLimitError) as info:
        c.run("leboncoin/phones/get", listing="1")
    assert info.value.retry_after == 36000
    assert len(mock.requests) == 1 and c.sleeps == []


def test_a_5xx_without_an_error_object_is_retried():
    mock = Mock(httpx.Response(502, text="<html>Bad gateway</html>"), ok(envelope()))
    c = client(mock)
    c.run(OP, query="x")
    assert len(mock.requests) == 2


def test_network_errors_and_timeouts_are_retried_on_a_run():
    mock = Mock(httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), ok(envelope()))
    c = client(mock)
    assert c.run(OP, query="x").id == "run_1"
    assert len(mock.requests) == 3
    assert len(set(mock.keys())) == 1


def test_a_timeout_raises_api_timeout_error():
    mock = Mock(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    c = client(mock, max_retries=1)
    with pytest.raises(APITimeoutError) as info:
        c.run(OP, query="x")
    assert isinstance(info.value, APIConnectionError)
    assert isinstance(info.value.__cause__, httpx.ReadTimeout)


def test_a_post_without_a_key_is_not_retried_after_a_network_error():
    mock = Mock(httpx.ReadTimeout("slow"))
    with pytest.raises(APITimeoutError) as info:
        client(mock).feedback("hello")
    assert info.value.code == "client_timeout"
    assert len(mock.requests) == 1

    mock = Mock(httpx.ConnectError("refused"))
    with pytest.raises(APIConnectionError) as info:
        client(mock).accounts.connect("x", {"auth_token": "t"})
    assert info.value.code == "connection_error"
    assert len(mock.requests) == 1


def test_a_post_without_a_key_is_retried_when_the_api_says_so():
    answer = ok({"object": "feedback", "id": "fbk_1", "created_at": "x", "kind": "other"})
    busy = error(503, "feedback_unavailable", "site_error", retryable=True, retry_after=5)
    mock = Mock(busy, answer)
    c = client(mock)
    assert c.feedback("hello")["id"] == "fbk_1"
    assert c.sleeps == [5]


def test_any_5xx_is_retried():
    mock = Mock(error(500, "internal_error", "api_error", retryable=False), ok(envelope()))
    client(mock).run(OP, query="x")
    assert len(mock.requests) == 2


def test_gets_are_retried():
    mock = Mock(
        error(503, "balance_unavailable", "site_error", retryable=True, retry_after=1),
        ok({"object": "balance", "credits": 3}),
    )
    c = client(mock)
    assert c.balance()["credits"] == 3
    assert c.sleeps == [1]


def test_max_retries_zero():
    mock = Mock(error(503, "service_busy", "site_error", retryable=True, retry_after=1))
    with pytest.raises(SiteError):
        client(mock, max_retries=0).run(OP, query="x")
    assert len(mock.requests) == 1


def test_async_retries_keep_the_key():
    mock = Mock(
        error(503, "site_unavailable", "site_error", retryable=True, retry_after=4),
        ok(envelope()),
    )

    async def main():
        c = async_client(mock)
        await c.run(OP, query="x")
        return c.sleeps

    assert asyncio.run(main()) == [4]
    assert len(set(mock.keys())) == 1
