"""A run: the answer of one call to ``POST /v2/run``, its records in ``results``."""

from typing import TYPE_CHECKING, Any, Dict, Generic, List, Optional, TypeVar

if TYPE_CHECKING:
    from ._client import AsyncLobstr, Lobstr
    from ._types import Credits, RunRequest

__all__ = ["Run", "AsyncRun"]

R = TypeVar("R")


class _RunData(Generic[R]):
    """The envelope's fields as attributes, and the records in ``results``.

    A field the API adds later is read as an attribute too (``run.new_field``), or with
    ``run["new_field"]``; ``to_dict()`` gives the whole answer as it came.
    """

    object: str
    id: str
    created_at: Optional[str]
    livemode: Optional[bool]
    source: str
    operation: str
    version: Optional[str]
    schema_url: Optional[str]
    options: Dict[str, Any]
    fields: Optional[List[str]]
    limit: Optional[int]
    status: str
    count: int
    has_more: bool
    next: "Optional[RunRequest]"
    cost: "Credits"
    balance: "Optional[Credits]"
    duration_ms: Optional[int]
    request_id: Optional[str]
    results: List[R]

    _ENVELOPE = (
        "object",
        "id",
        "created_at",
        "livemode",
        "source",
        "operation",
        "version",
        "schema_url",
        "options",
        "fields",
        "limit",
        "status",
        "count",
        "has_more",
        "next",
        "cost",
        "balance",
        "duration_ms",
        "request_id",
    )

    def __init__(
        self,
        data: Dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        replayed: bool = False,
    ) -> None:
        self._data = data
        for name in self._ENVELOPE:
            setattr(self, name, data.get(name))
        self.results = list(data.get("results") or [])
        if self.count is None:
            self.count = len(self.results)
        self.has_more = bool(self.has_more)
        if self.cost is None:
            self.cost = {"credits": 0}
        #: The ``Idempotency-Key`` this call was sent with.
        self.idempotency_key = idempotency_key
        #: True when the API answered a replay of an earlier call with the same key.
        self.replayed = replayed

    @property
    def operation_id(self) -> str:
        """The full operation, ``source/resource/verb``."""
        return f"{self.source}/{self.operation}"

    def to_dict(self) -> Dict[str, Any]:
        """The whole answer, as the API sent it."""
        return self._data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not set above: fields the API added after this version.
        data = self.__dict__.get("_data")
        if data is not None and not name.startswith("_") and name in data:
            return data[name]
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def __repr__(self) -> str:
        credits = self.cost.get("credits") if isinstance(self.cost, dict) else None
        return (
            f"<{type(self).__name__} {self.id} {self.source}/{self.operation} "
            f"status={self.status} count={self.count} has_more={self.has_more} "
            f"credits={credits}>"
        )


class Run(_RunData[R]):
    """One call's answer. ``next_page()`` sends ``next`` as it is, and is ``None`` at the end."""

    def __init__(
        self,
        data: Dict[str, Any],
        *,
        client: "Lobstr",
        idempotency_key: Optional[str] = None,
        replayed: bool = False,
    ) -> None:
        super().__init__(data, idempotency_key=idempotency_key, replayed=replayed)
        self._client = client

    def next_page(self, *, idempotency_key: Optional[str] = None) -> "Optional[Run[R]]":
        """The following page: one more call, at the operation's price. ``None`` after the last."""
        if not self.next:
            return None
        return self._client.send(self.next, idempotency_key=idempotency_key)


class AsyncRun(_RunData[R]):
    """One call's answer, from ``AsyncLobstr``: ``await run.next_page()``."""

    def __init__(
        self,
        data: Dict[str, Any],
        *,
        client: "AsyncLobstr",
        idempotency_key: Optional[str] = None,
        replayed: bool = False,
    ) -> None:
        super().__init__(data, idempotency_key=idempotency_key, replayed=replayed)
        self._client = client

    async def next_page(self, *, idempotency_key: Optional[str] = None) -> "Optional[AsyncRun[R]]":
        """The following page: one more call, at the operation's price. ``None`` after the last."""
        if not self.next:
            return None
        return await self._client.send(self.next, idempotency_key=idempotency_key)
