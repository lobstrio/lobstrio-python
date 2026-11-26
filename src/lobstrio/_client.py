"""``Lobstr`` and ``AsyncLobstr``: the clients of lobstr.io's API v2."""

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import (
    Any,
    AsyncIterator,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
    cast,
)

import httpx

from ._base import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    BaseClient,
    Timeout,
    build_request,
    clean_params,
    drop_none,
    normalize_call,
)
from ._errors import APIConnectionError, APIError, APITimeoutError, LobstrError, error_from_response
from ._generated import AsyncOperations, Balance, Feedback, SyncOperations
from ._resources import Accounts, AsyncAccounts, AsyncCatalog, AsyncRuns, Catalog, Runs
from ._run import AsyncRun, Run

__all__ = ["Lobstr", "AsyncLobstr"]

Record = Dict[str, Any]
Fields = Union[str, Sequence[str], None]
RunManyResult = Union[Run[Record], Exception]
AsyncRunManyResult = Union[AsyncRun[Record], Exception]


def _transport_error(exc: Exception, timeout: Any) -> APIConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        seconds = f" ({timeout:g} s)" if isinstance(timeout, (int, float)) else ""
        err: APIConnectionError = APITimeoutError(
            f"No answer within the client's timeout{seconds}: {type(exc).__name__}."
        )
    else:
        err = APIConnectionError(f"Could not reach the API: {type(exc).__name__}: {exc}")
    err.__cause__ = exc
    return err


def _parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        raise APIError(
            f"The API answered {response.status_code} with something that is not JSON.",
            status=response.status_code,
            request_id=response.headers.get("request-id"),
            body=response.text[:500],
        ) from None


def _replayed(response: httpx.Response) -> bool:
    return response.headers.get("idempotent-replayed", "").lower() == "true"


class Lobstr(BaseClient, SyncOperations):
    """The client of lobstr.io's API v2.

    Args:
        api_key: Your API key; ``LOBSTR_API_KEY`` when left out. A call that needs it fails
            without it; the catalog does not.
        base_url: ``https://api.lobstr.io``.
        timeout: Seconds to wait for an answer (or an ``httpx.Timeout``).
        max_retries: Retries of a call that failed in a way that may pass (429, 5xx, a lost
            connection). A run keeps its ``Idempotency-Key`` across them: never charged twice.
        http_client: Your own ``httpx.Client``, for proxies or a custom transport.
        default_headers: Headers sent with every call.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.Client] = None,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
        )
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self.catalog = Catalog(self)
        self.accounts = Accounts(self)
        self.runs = Runs(self)
        self._init_operations()

    # -- lifecycle

    def close(self) -> None:
        """Closes the connections, unless the ``http_client`` was yours."""
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "Lobstr":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    # -- HTTP

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        auth: bool = True,
        idempotency_key: Optional[str] = None,
        idempotent: Optional[bool] = None,
        timeout: Optional[Timeout] = None,
    ) -> httpx.Response:
        headers = self._headers(auth=auth, idempotency_key=idempotency_key)
        if idempotent is None:
            idempotent = method in ("GET", "HEAD", "DELETE") or idempotency_key is not None
        wait_for = timeout if timeout is not None else self.timeout
        attempt = 0
        while True:
            try:
                response = self._http.request(
                    method,
                    self._url(path),
                    params=clean_params(params),
                    json=json,
                    headers=headers,
                    timeout=wait_for,
                )
            except httpx.TransportError as exc:
                err: LobstrError = _transport_error(exc, wait_for)
            else:
                if response.status_code < 400:
                    return response
                err = error_from_response(response.status_code, response.content, response.headers)
            wait = self._retry_wait(err, attempt, idempotent=idempotent)
            if wait is None:
                raise err
            attempt += 1
            self._sleep(wait)

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        return _parse_json(self._request(method, path, **kwargs))

    def _get(
        self, path: str, *, params: Optional[Mapping[str, Any]] = None, auth: bool = True
    ) -> Any:
        return self._request_json("GET", path, params=params, auth=auth)

    # -- runs

    def run(
        self,
        operation: str,
        options: Optional[Mapping[str, Any]] = None,
        /,
        *,
        fields: Fields = None,
        limit: Optional[int] = None,
        timeout_s: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        **option_kwargs: Any,
    ) -> Run[Record]:
        """Runs one operation: one page of a list, or one item, and what it cost.

        Args:
            operation: The full ``source/resource/verb``, as ``google_maps/places/search``.
            options: The operation's options, as a dict; or pass them as keyword arguments.
            fields: Only these fields of each record, by path (``["name", "address.city"]``).
            limit: Only the first 1 to 1,000 records of the page; the call costs the same.
            timeout_s: The most seconds the API waits for the site.
            idempotency_key: Sent as ``Idempotency-Key``; a UUID4 is made when left out, and kept
                across retries.
        """
        request = build_request(
            operation, options, option_kwargs, fields=fields, limit=limit, timeout_s=timeout_s
        )
        return self.send(request, idempotency_key=idempotency_key)

    def send(
        self, request: Mapping[str, Any], *, idempotency_key: Optional[str] = None
    ) -> Run[Record]:
        """Sends a request object as it is, such as a run's ``next``."""
        key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
        response = self._request(
            "POST",
            "/v2/run",
            json=dict(request),
            idempotency_key=key,
            timeout=self._run_timeout(request.get("timeout_s")),
        )
        return Run(
            _parse_json(response), client=self, idempotency_key=key, replayed=_replayed(response)
        )

    def iterate(
        self,
        operation: str,
        options: Optional[Mapping[str, Any]] = None,
        /,
        *,
        max_records: Optional[int] = None,
        max_pages: Optional[int] = None,
        fields: Fields = None,
        limit: Optional[int] = None,
        timeout_s: Optional[int] = None,
        **option_kwargs: Any,
    ) -> Iterator[Record]:
        """Yields the records of every page, following ``next`` until it is null or a cap is hit.

        Each page is one call, at the operation's price: ``max_records`` and ``max_pages`` stop
        it early. The first call is made when the first record is asked for.
        """
        request = build_request(
            operation, options, option_kwargs, fields=fields, limit=limit, timeout_s=timeout_s
        )
        return self._iterate(request, max_records, max_pages)

    def _iterate(
        self, request: Dict[str, Any], max_records: Optional[int], max_pages: Optional[int]
    ) -> Iterator[Record]:
        if (max_records is not None and max_records <= 0) or (
            max_pages is not None and max_pages <= 0
        ):
            return
        page: Optional[Run[Record]] = self.send(request)
        pages, seen = 1, 0
        while page is not None:
            for record in page.results:
                yield record
                seen += 1
                if max_records is not None and seen >= max_records:
                    return
            if max_pages is not None and pages >= max_pages:
                return
            page = page.next_page()
            pages += 1

    def run_many(self, calls: Iterable[Any], *, concurrency: int = 4) -> List[RunManyResult]:
        """Runs many calls in parallel, at most ``concurrency`` at a time.

        Each call is ``{"operation": "google_maps/places/search", "options": {...}}`` (plus
        ``fields``, ``limit``, ``timeout_s``, ``idempotency_key``), a request as ``next`` gives it,
        or a tuple ``(operation, options)``. A call answered ``429 concurrency_limit`` (your plan's
        calls at a time) waits its ``retry_after`` and goes again, up to 100 times, with its key. Returns one item per call, in their order: its ``Run``, or
        the error it raised. One failed call never raises.
        """
        items = list(calls)
        if concurrency < 1:
            raise ValueError("concurrency is at least 1")
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=min(concurrency, len(items))) as pool:
            return list(pool.map(self._run_one, items))

    def _run_one(self, call: Any) -> RunManyResult:
        try:
            request, key = normalize_call(call)
        except (TypeError, ValueError) as exc:
            return exc
        key = key or str(uuid.uuid4())
        waits = 0
        while True:
            try:
                return self.send(request, idempotency_key=key)
            except LobstrError as err:
                wait = self._concurrency_wait(err, waits)
                if wait is None:
                    return err
                waits += 1
                self._sleep(wait)

    # -- the other routes

    def balance(self) -> Balance:
        """The credits you can spend now, and your plan with the price of one credit on it."""
        return cast(Balance, self._get("/v2/balance"))

    def feedback(
        self,
        message: str,
        *,
        kind: Optional[str] = None,
        operation: Optional[str] = None,
        run: Optional[str] = None,
    ) -> Feedback:
        """Tells lobstr.io's team what went wrong or what you need. Free, 20 a day.

        Args:
            message: What happened and what you expected (cut at 2,000 characters).
            kind: ``bug``, ``missing_data``, ``idea`` or ``other``.
            operation: The operation concerned, as ``google_maps/reviews/list``.
            run: The id of the call concerned (``run_...``).
        """
        body = {"message": message, **drop_none(kind=kind, operation=operation, run=run)}
        answer = self._request_json("POST", "/v2/feedback", json=body, idempotent=False)
        return cast(Feedback, answer)


class AsyncLobstr(BaseClient, AsyncOperations):
    """The async client of lobstr.io's API v2: the same as ``Lobstr``, with ``await``.

    Args:
        api_key: Your API key; ``LOBSTR_API_KEY`` when left out. A call that needs it fails
            without it; the catalog does not.
        base_url: ``https://api.lobstr.io``.
        timeout: Seconds to wait for an answer (or an ``httpx.Timeout``).
        max_retries: Retries of a call that failed in a way that may pass (429, 5xx, a lost
            connection). A run keeps its ``Idempotency-Key`` across them: never charged twice.
        http_client: Your own ``httpx.AsyncClient``, for proxies or a custom transport.
        default_headers: Headers sent with every call.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.AsyncClient] = None,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(
            api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=default_headers,
        )
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=timeout)
        self.catalog = AsyncCatalog(self)
        self.accounts = AsyncAccounts(self)
        self.runs = AsyncRuns(self)
        self._init_operations()

    # -- lifecycle

    async def close(self) -> None:
        """Closes the connections, unless the ``http_client`` was yours."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AsyncLobstr":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    # -- HTTP

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        auth: bool = True,
        idempotency_key: Optional[str] = None,
        idempotent: Optional[bool] = None,
        timeout: Optional[Timeout] = None,
    ) -> httpx.Response:
        headers = self._headers(auth=auth, idempotency_key=idempotency_key)
        if idempotent is None:
            idempotent = method in ("GET", "HEAD", "DELETE") or idempotency_key is not None
        wait_for = timeout if timeout is not None else self.timeout
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method,
                    self._url(path),
                    params=clean_params(params),
                    json=json,
                    headers=headers,
                    timeout=wait_for,
                )
            except httpx.TransportError as exc:
                err: LobstrError = _transport_error(exc, wait_for)
            else:
                if response.status_code < 400:
                    return response
                err = error_from_response(response.status_code, response.content, response.headers)
            wait = self._retry_wait(err, attempt, idempotent=idempotent)
            if wait is None:
                raise err
            attempt += 1
            await self._sleep(wait)

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        return _parse_json(await self._request(method, path, **kwargs))

    async def _get(
        self, path: str, *, params: Optional[Mapping[str, Any]] = None, auth: bool = True
    ) -> Any:
        return await self._request_json("GET", path, params=params, auth=auth)

    # -- runs

    async def run(
        self,
        operation: str,
        options: Optional[Mapping[str, Any]] = None,
        /,
        *,
        fields: Fields = None,
        limit: Optional[int] = None,
        timeout_s: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        **option_kwargs: Any,
    ) -> AsyncRun[Record]:
        """Runs one operation: one page of a list, or one item, and what it cost.

        Args:
            operation: The full ``source/resource/verb``, as ``google_maps/places/search``.
            options: The operation's options, as a dict; or pass them as keyword arguments.
            fields: Only these fields of each record, by path (``["name", "address.city"]``).
            limit: Only the first 1 to 1,000 records of the page; the call costs the same.
            timeout_s: The most seconds the API waits for the site.
            idempotency_key: Sent as ``Idempotency-Key``; a UUID4 is made when left out, and kept
                across retries.
        """
        request = build_request(
            operation, options, option_kwargs, fields=fields, limit=limit, timeout_s=timeout_s
        )
        return await self.send(request, idempotency_key=idempotency_key)

    async def send(
        self, request: Mapping[str, Any], *, idempotency_key: Optional[str] = None
    ) -> AsyncRun[Record]:
        """Sends a request object as it is, such as a run's ``next``."""
        key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
        response = await self._request(
            "POST",
            "/v2/run",
            json=dict(request),
            idempotency_key=key,
            timeout=self._run_timeout(request.get("timeout_s")),
        )
        return AsyncRun(
            _parse_json(response), client=self, idempotency_key=key, replayed=_replayed(response)
        )

    def iterate(
        self,
        operation: str,
        options: Optional[Mapping[str, Any]] = None,
        /,
        *,
        max_records: Optional[int] = None,
        max_pages: Optional[int] = None,
        fields: Fields = None,
        limit: Optional[int] = None,
        timeout_s: Optional[int] = None,
        **option_kwargs: Any,
    ) -> AsyncIterator[Record]:
        """Yields the records of every page (``async for``), following ``next`` until it is null
        or a cap is hit.

        Each page is one call, at the operation's price: ``max_records`` and ``max_pages`` stop
        it early. The first call is made when the first record is asked for.
        """
        request = build_request(
            operation, options, option_kwargs, fields=fields, limit=limit, timeout_s=timeout_s
        )
        return self._iterate(request, max_records, max_pages)

    async def _iterate(
        self, request: Dict[str, Any], max_records: Optional[int], max_pages: Optional[int]
    ) -> AsyncIterator[Record]:
        if (max_records is not None and max_records <= 0) or (
            max_pages is not None and max_pages <= 0
        ):
            return
        page: Optional[AsyncRun[Record]] = await self.send(request)
        pages, seen = 1, 0
        while page is not None:
            for record in page.results:
                yield record
                seen += 1
                if max_records is not None and seen >= max_records:
                    return
            if max_pages is not None and pages >= max_pages:
                return
            page = await page.next_page()
            pages += 1

    async def run_many(
        self, calls: Iterable[Any], *, concurrency: int = 4
    ) -> List[AsyncRunManyResult]:
        """Runs many calls in parallel, at most ``concurrency`` at a time.

        Each call is ``{"operation": "google_maps/places/search", "options": {...}}`` (plus
        ``fields``, ``limit``, ``timeout_s``, ``idempotency_key``), a request as ``next`` gives it,
        or a tuple ``(operation, options)``. A call answered ``429 concurrency_limit`` (your plan's
        calls at a time) waits its ``retry_after`` and goes again, up to 100 times, with its key. Returns one item per call, in their order: its run, or
        the error it raised. One failed call never raises.
        """
        items = list(calls)
        if concurrency < 1:
            raise ValueError("concurrency is at least 1")
        semaphore = asyncio.Semaphore(concurrency)

        async def one(call: Any) -> AsyncRunManyResult:
            async with semaphore:
                return await self._run_one(call)

        return list(await asyncio.gather(*(one(call) for call in items)))

    async def _run_one(self, call: Any) -> AsyncRunManyResult:
        try:
            request, key = normalize_call(call)
        except (TypeError, ValueError) as exc:
            return exc
        key = key or str(uuid.uuid4())
        waits = 0
        while True:
            try:
                return await self.send(request, idempotency_key=key)
            except LobstrError as err:
                wait = self._concurrency_wait(err, waits)
                if wait is None:
                    return err
                waits += 1
                await self._sleep(wait)

    # -- the other routes

    async def balance(self) -> Balance:
        """The credits you can spend now, and your plan with the price of one credit on it."""
        return cast(Balance, await self._get("/v2/balance"))

    async def feedback(
        self,
        message: str,
        *,
        kind: Optional[str] = None,
        operation: Optional[str] = None,
        run: Optional[str] = None,
    ) -> Feedback:
        """Tells lobstr.io's team what went wrong or what you need. Free, 20 a day.

        Args:
            message: What happened and what you expected (cut at 2,000 characters).
            kind: ``bug``, ``missing_data``, ``idea`` or ``other``.
            operation: The operation concerned, as ``google_maps/reviews/list``.
            run: The id of the call concerned (``run_...``).
        """
        body = {"message": message, **drop_none(kind=kind, operation=operation, run=run)}
        answer = await self._request_json("POST", "/v2/feedback", json=body, idempotent=False)
        return cast(Feedback, answer)
