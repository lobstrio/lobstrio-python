"""The API's routes beside ``POST /v2/run``: the catalog, connected accounts, your calls."""

import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterator,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Union,
    cast,
)
from urllib.parse import quote

from ._base import drop_none, operation_path, utc_string
from ._run import AsyncRun, Run

if TYPE_CHECKING:
    from ._client import AsyncLobstr, Lobstr
    from ._generated import Account, AccountList, DeletedAccount, RunLog
    from ._types import CatalogList, OperationContract, OperationStatus

__all__ = [
    "Catalog",
    "AsyncCatalog",
    "Accounts",
    "AsyncAccounts",
    "Runs",
    "AsyncRuns",
    "RunLogPage",
    "AsyncRunLogPage",
]

DateLike = Union[str, datetime.datetime, datetime.date]
Cookies = Union[Mapping[str, str], List[Mapping[str, Any]]]


def _id(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is a non-empty string, not {value!r}")
    return quote(value.strip(), safe="")


def _run_filters(
    limit: Optional[int],
    starting_after: Optional[str],
    operation: Optional[str],
    status: Optional[str],
    via: Optional[str],
    created_after: Optional[DateLike],
    created_before: Optional[DateLike],
) -> Dict[str, Any]:
    return drop_none(
        limit=limit,
        starting_after=starting_after,
        operation=operation,
        status=status,
        via=via,
        created_after=utc_string(created_after),
        created_before=utc_string(created_before),
    )


def _connect_body(
    site: str,
    cookies: Cookies,
    country: Optional[str],
    headers: Optional[Mapping[str, str]],
    account: Optional[str],
) -> Dict[str, Any]:
    if isinstance(cookies, Mapping):
        cookies = dict(cookies)
    else:
        cookies = [dict(cookie) for cookie in cookies]
    return {
        "site": site,
        "cookies": cookies,
        **drop_none(
            country=country,
            headers=dict(headers) if headers is not None else None,
            account=account,
        ),
    }


class _RunLogPageData:
    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data
        self.object: str = data.get("object", "list")
        self.data: "List[RunLog]" = list(data.get("data") or [])
        self.has_more: bool = bool(data.get("has_more"))
        #: The following page's path, whole; ``None`` after the last.
        self.next: Optional[str] = data.get("next")

    def to_dict(self) -> Dict[str, Any]:
        return self._data

    def __iter__(self) -> "Iterator[RunLog]":
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} runs={len(self.data)} has_more={self.has_more}>"


class RunLogPage(_RunLogPageData):
    """One page of ``GET /v2/runs``: ``data``, ``has_more``, ``next``, and ``next_page()``."""

    def __init__(self, data: Dict[str, Any], *, client: "Lobstr") -> None:
        super().__init__(data)
        self._client = client

    def next_page(self) -> "Optional[RunLogPage]":
        if not self.next:
            return None
        return RunLogPage(self._client._get(self.next), client=self._client)


class AsyncRunLogPage(_RunLogPageData):
    """One page of ``GET /v2/runs``, from ``AsyncLobstr``: ``await page.next_page()``."""

    def __init__(self, data: Dict[str, Any], *, client: "AsyncLobstr") -> None:
        super().__init__(data)
        self._client = client

    async def next_page(self) -> "Optional[AsyncRunLogPage]":
        if not self.next:
            return None
        return AsyncRunLogPage(await self._client._get(self.next), client=self._client)


class Catalog:
    """Every operation and its contract. These calls need no key."""

    def __init__(self, client: "Lobstr") -> None:
        self._client = client

    def list(self, *, source: Optional[str] = None) -> "CatalogList":
        """Every operation (``GET /v2/catalog``), or one source's (``source="google_maps"``).

        One page, as the API answers it: the operations in ``data``.
        """
        answer = self._client._get("/v2/catalog", params={"source": source}, auth=False)
        return cast("CatalogList", answer)

    def get(self, operation: str) -> "OperationContract":
        """One operation's contract: its options, its record, its errors, its price."""
        path = f"/v2/catalog/{operation_path(operation)}"
        return cast("OperationContract", self._client._get(path, auth=False))

    def status(self, operation: str) -> "OperationStatus":
        """How the operation's calls went, the last 90 days, a UTC day at a time."""
        path = f"/v2/catalog/{operation_path(operation)}/status"
        return cast("OperationStatus", self._client._get(path, auth=False))


class AsyncCatalog:
    """Every operation and its contract. These calls need no key."""

    def __init__(self, client: "AsyncLobstr") -> None:
        self._client = client

    async def list(self, *, source: Optional[str] = None) -> "CatalogList":
        """Every operation (``GET /v2/catalog``), or one source's (``source="google_maps"``).

        One page, as the API answers it: the operations in ``data``.
        """
        answer = await self._client._get("/v2/catalog", params={"source": source}, auth=False)
        return cast("CatalogList", answer)

    async def get(self, operation: str) -> "OperationContract":
        """One operation's contract: its options, its record, its errors, its price."""
        path = f"/v2/catalog/{operation_path(operation)}"
        return cast("OperationContract", await self._client._get(path, auth=False))

    async def status(self, operation: str) -> "OperationStatus":
        """How the operation's calls went, the last 90 days, a UTC day at a time."""
        path = f"/v2/catalog/{operation_path(operation)}/status"
        return cast("OperationStatus", await self._client._get(path, auth=False))


class Accounts:
    """Your accounts on the sites whose operations need one (``x``, ``sales_navigator``...)."""

    def __init__(self, client: "Lobstr") -> None:
        self._client = client

    def list(self, *, site: Optional[str] = None) -> "AccountList":
        """Your connected accounts (in ``data``), or one site's (``site="x"``)."""
        return cast("AccountList", self._client._get("/v2/accounts", params={"site": site}))

    def get(self, account_id: str) -> "Account":
        return cast("Account", self._client._get(f"/v2/accounts/{_id(account_id, 'account_id')}"))

    def delete(self, account_id: str) -> "DeletedAccount":
        """Removes the account, its session with it."""
        path = f"/v2/accounts/{_id(account_id, 'account_id')}"
        return cast("DeletedAccount", self._client._request_json("DELETE", path))

    def connect(
        self,
        site: str,
        cookies: Cookies,
        *,
        country: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        account: Optional[str] = None,
    ) -> "Account":
        """Connects an account from the cookies of a browser logged in to the site.

        Args:
            site: ``facebook``, ``leboncoin``, ``sales_navigator`` or ``x``.
            cookies: ``{name: value}``, or a cookie editor's export (a list of
                ``{name, value, domain}``).
            country: The country you use the account from (``FR``, ``US``). Left out, the
                country of the IP this call comes from.
            headers: The headers the same browser sends the site (user-agent, accept-language...).
            account: Connecting again an account already connected: its id.
        """
        body = _connect_body(site, cookies, country, headers, account)
        return cast(
            "Account",
            self._client._request_json("POST", "/v2/accounts", json=body, idempotent=False),
        )


class AsyncAccounts:
    """Your accounts on the sites whose operations need one (``x``, ``sales_navigator``...)."""

    def __init__(self, client: "AsyncLobstr") -> None:
        self._client = client

    async def list(self, *, site: Optional[str] = None) -> "AccountList":
        """Your connected accounts (in ``data``), or one site's (``site="x"``)."""
        answer = await self._client._get("/v2/accounts", params={"site": site})
        return cast("AccountList", answer)

    async def get(self, account_id: str) -> "Account":
        path = f"/v2/accounts/{_id(account_id, 'account_id')}"
        return cast("Account", await self._client._get(path))

    async def delete(self, account_id: str) -> "DeletedAccount":
        """Removes the account, its session with it."""
        path = f"/v2/accounts/{_id(account_id, 'account_id')}"
        return cast("DeletedAccount", await self._client._request_json("DELETE", path))

    async def connect(
        self,
        site: str,
        cookies: Cookies,
        *,
        country: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        account: Optional[str] = None,
    ) -> "Account":
        """Connects an account from the cookies of a browser logged in to the site.

        Args:
            site: ``facebook``, ``leboncoin``, ``sales_navigator`` or ``x``.
            cookies: ``{name: value}``, or a cookie editor's export (a list of
                ``{name, value, domain}``).
            country: The country you use the account from (``FR``, ``US``). Left out, the
                country of the IP this call comes from.
            headers: The headers the same browser sends the site (user-agent, accept-language...).
            account: Connecting again an account already connected: its id.
        """
        body = _connect_body(site, cookies, country, headers, account)
        answer = await self._client._request_json(
            "POST", "/v2/accounts", json=body, idempotent=False
        )
        return cast("Account", answer)


class Runs:
    """Your calls to ``POST /v2/run``, kept 90 days: what was sent and what was answered."""

    def __init__(self, client: "Lobstr") -> None:
        self._client = client

    def list(
        self,
        *,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
        operation: Optional[str] = None,
        status: Optional[str] = None,
        via: Optional[str] = None,
        created_after: Optional[DateLike] = None,
        created_before: Optional[DateLike] = None,
    ) -> RunLogPage:
        """One page of your calls, newest first (``limit`` 1 to 100, 20 by default).

        ``status`` is ``succeeded`` or ``failed``; ``via`` is ``api``, ``mcp``, ``app`` or ``sdk``; dates
        are UTC (a naive datetime is read as UTC).
        """
        params = _run_filters(
            limit, starting_after, operation, status, via, created_after, created_before
        )
        return RunLogPage(self._client._get("/v2/runs", params=params), client=self._client)

    def iterate(
        self,
        *,
        max_records: Optional[int] = None,
        limit: Optional[int] = None,
        operation: Optional[str] = None,
        status: Optional[str] = None,
        via: Optional[str] = None,
        created_after: Optional[DateLike] = None,
        created_before: Optional[DateLike] = None,
    ) -> "Iterator[RunLog]":
        """Every call that fits the filters, page after page, newest first."""
        if max_records is not None and max_records <= 0:
            return
        page: Optional[RunLogPage] = self.list(
            limit=limit,
            operation=operation,
            status=status,
            via=via,
            created_after=created_after,
            created_before=created_before,
        )
        seen = 0
        while page is not None:
            for item in page.data:
                yield item
                seen += 1
                if max_records is not None and seen >= max_records:
                    return
            page = page.next_page() if page.has_more else None

    def get(self, run_id: str) -> "RunLog":
        """One call: what was sent and what was answered, records aside."""
        return cast("RunLog", self._client._get(f"/v2/runs/{_id(run_id, 'run_id')}"))

    def response(self, run_id: str) -> "Run[Dict[str, Any]]":
        """A call's whole answer, records included, while your plan keeps answers."""
        data = self._client._get(f"/v2/runs/{_id(run_id, 'run_id')}/response")
        return Run(data, client=self._client)


class AsyncRuns:
    """Your calls to ``POST /v2/run``, kept 90 days: what was sent and what was answered."""

    def __init__(self, client: "AsyncLobstr") -> None:
        self._client = client

    async def list(
        self,
        *,
        limit: Optional[int] = None,
        starting_after: Optional[str] = None,
        operation: Optional[str] = None,
        status: Optional[str] = None,
        via: Optional[str] = None,
        created_after: Optional[DateLike] = None,
        created_before: Optional[DateLike] = None,
    ) -> AsyncRunLogPage:
        """One page of your calls, newest first (``limit`` 1 to 100, 20 by default).

        ``status`` is ``succeeded`` or ``failed``; ``via`` is ``api``, ``mcp``, ``app`` or ``sdk``; dates
        are UTC (a naive datetime is read as UTC).
        """
        params = _run_filters(
            limit, starting_after, operation, status, via, created_after, created_before
        )
        data = await self._client._get("/v2/runs", params=params)
        return AsyncRunLogPage(data, client=self._client)

    async def iterate(
        self,
        *,
        max_records: Optional[int] = None,
        limit: Optional[int] = None,
        operation: Optional[str] = None,
        status: Optional[str] = None,
        via: Optional[str] = None,
        created_after: Optional[DateLike] = None,
        created_before: Optional[DateLike] = None,
    ) -> "AsyncIterator[RunLog]":
        """Every call that fits the filters, page after page, newest first."""
        if max_records is not None and max_records <= 0:
            return
        page: Optional[AsyncRunLogPage] = await self.list(
            limit=limit,
            operation=operation,
            status=status,
            via=via,
            created_after=created_after,
            created_before=created_before,
        )
        seen = 0
        while page is not None:
            for item in page.data:
                yield item
                seen += 1
                if max_records is not None and seen >= max_records:
                    return
            page = await page.next_page() if page.has_more else None

    async def get(self, run_id: str) -> "RunLog":
        """One call: what was sent and what was answered, records aside."""
        return cast("RunLog", await self._client._get(f"/v2/runs/{_id(run_id, 'run_id')}"))

    async def response(self, run_id: str) -> "AsyncRun[Dict[str, Any]]":
        """A call's whole answer, records included, while your plan keeps answers."""
        data = await self._client._get(f"/v2/runs/{_id(run_id, 'run_id')}/response")
        return AsyncRun(data, client=self._client)
