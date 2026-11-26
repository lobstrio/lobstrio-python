"""Mocked HTTP for the tests: queued answers, and every request kept."""

import json
import threading
from typing import Any, Callable, Dict, List, Optional, Union

import httpx

from lobstrio import AsyncLobstr, Lobstr

API_KEY = "sk_test_never_printed"

Answer = Union[httpx.Response, Exception, Callable[[httpx.Request], httpx.Response]]


def envelope(
    operation: str = "google_news/articles/search",
    results: Optional[List[Dict[str, Any]]] = None,
    *,
    next: Optional[Dict[str, Any]] = None,
    credits: float = 1,
    run_id: str = "run_1",
    **extra: Any,
) -> Dict[str, Any]:
    source, rest = operation.split("/", 1)
    results = results if results is not None else [{"object": "article", "id": "a1", "position": 1}]
    return {
        "object": "run",
        "id": run_id,
        "created_at": "2026-11-26T10:00:00Z",
        "livemode": True,
        "source": source,
        "operation": rest,
        "version": "2.0.1",
        "schema_url": f"https://api.lobstr.io/v2/catalog/{operation}",
        "options": {},
        "fields": None,
        "limit": None,
        "status": "succeeded",
        "count": len(results),
        "has_more": next is not None,
        "next": next,
        "cost": {"credits": credits},
        "balance": {"credits": 99},
        "duration_ms": 12,
        "request_id": "req_1",
        "results": results,
        **extra,
    }


def error(
    status: int,
    code: str,
    type_: str,
    *,
    retryable: bool = False,
    retry_after: Optional[int] = None,
    headers: Optional[Dict[str, str]] = None,
    param: Optional[str] = None,
) -> httpx.Response:
    body: Dict[str, Any] = {
        "type": type_,
        "code": code,
        "message": f"The message of {code}.",
        "param": param,
        "doc_url": f"https://docs.lobstr.io/errors#{code}",
        "request_id": f"req_{code}",
        "retryable": retryable,
    }
    if retry_after is not None:
        body["retry_after"] = retry_after
    return httpx.Response(status, json={"error": body}, headers=headers or {})


class Mock:
    """A transport that answers from a queue (or a function) and keeps the requests."""

    def __init__(self, *answers: Answer, default: Optional[Answer] = None) -> None:
        self.answers: List[Answer] = list(answers)
        self.default = default
        self.requests: List[httpx.Request] = []
        self.lock = threading.Lock()

    def answer(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
            item = self.answers.pop(0) if self.answers else self.default
        if item is None:
            raise AssertionError(f"no answer queued for {request.method} {request.url}")
        if callable(item) and not isinstance(item, httpx.Response):
            item = item(request)
        if isinstance(item, Exception):
            raise item
        return item

    def handler(self, request: httpx.Request) -> httpx.Response:
        return self.answer(request)

    async def async_handler(self, request: httpx.Request) -> httpx.Response:
        return self.answer(request)

    def bodies(self) -> List[Any]:
        return [json.loads(r.content) if r.content else None for r in self.requests]

    def keys(self) -> List[Optional[str]]:
        return [r.headers.get("idempotency-key") for r in self.requests]


def client(mock: Mock, **kwargs: Any) -> Lobstr:
    kwargs.setdefault("api_key", API_KEY)
    c = Lobstr(http_client=httpx.Client(transport=httpx.MockTransport(mock.handler)), **kwargs)
    c.sleeps = []  # type: ignore[attr-defined]
    c._sleep = c.sleeps.append  # type: ignore[assignment,attr-defined]
    return c


def async_client(mock: Mock, **kwargs: Any) -> AsyncLobstr:
    kwargs.setdefault("api_key", API_KEY)
    transport = httpx.MockTransport(mock.async_handler)
    c = AsyncLobstr(http_client=httpx.AsyncClient(transport=transport), **kwargs)
    sleeps: List[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    c.sleeps = sleeps  # type: ignore[attr-defined]
    c._sleep = fake_sleep  # type: ignore[assignment,method-assign]
    return c


def ok(data: Any, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(200, json=data, headers=headers or {})
