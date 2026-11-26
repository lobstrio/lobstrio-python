"""Types written by hand: a run's request, and the catalog's answers (the OpenAPI gives them no schema)."""

from typing import Any, Dict, List, Optional, TypedDict

__all__ = [
    "Credits",
    "RunRequest",
    "RunCall",
    "CatalogOperation",
    "CatalogList",
    "OperationContract",
    "OperationStatus",
]


class Credits(TypedDict):
    """``cost`` and ``balance`` in a run's answer."""

    credits: float


class _RunRequestBase(TypedDict):
    source: str
    operation: str


class RunRequest(_RunRequestBase, total=False):
    """The body of ``POST /v2/run``, as ``next`` gives it: ``operation`` is ``resource/verb``."""

    options: Dict[str, Any]
    fields: Optional[List[str]]
    limit: Optional[int]
    timeout_s: Optional[int]


class _RunCallBase(TypedDict):
    operation: str


class RunCall(_RunCallBase, total=False):
    """One item of ``run_many``: ``operation`` is the full ``source/resource/verb``."""

    options: Dict[str, Any]
    fields: List[str]
    limit: int
    timeout_s: int
    idempotency_key: str


class CatalogSite(TypedDict, total=False):
    title: str
    domain: str
    color: str


class CatalogPrice(TypedDict, total=False):
    credits_per_call: float
    description: str
    free: List[str]


class _CatalogOperationBase(TypedDict):
    object: str
    id: str
    """``source/resource/verb``."""
    source: str
    operation: str
    title: str
    summary: str
    version: str


class CatalogOperation(_CatalogOperationBase, total=False):
    """An operation in the catalog (``GET /v2/catalog``)."""

    site: CatalogSite
    use_when: str
    categories: List[str]
    icon: str
    status: str
    price: CatalogPrice


class _CatalogListBase(TypedDict):
    object: str
    data: List[CatalogOperation]
    has_more: bool


class CatalogList(_CatalogListBase, total=False):
    """``GET /v2/catalog``: one page, ``data`` the operations."""

    next_cursor: Optional[str]


class OperationDeadline(TypedDict):
    default_s: int
    max_s: int


class OperationContract(CatalogOperation, total=False):
    """An operation's public contract (``GET /v2/catalog/{source}/{resource}/{verb}``)."""

    input: Dict[str, Any]
    record: Dict[str, Any]
    pagination: Optional[Dict[str, Any]]
    deadline: OperationDeadline
    errors: List[Dict[str, Any]]
    examples: List[Dict[str, Any]]
    changelog: List[Dict[str, Any]]
    health: Dict[str, Any]


class OperationStatusDay(TypedDict):
    date: str
    calls: int
    ok: int
    p50_ms: int


class OperationStatus(TypedDict):
    """How an operation's calls went, the last 90 days, a UTC day at a time."""

    object: str
    id: str
    calls: int
    ok: int
    success_rate: Optional[float]
    days: List[OperationStatusDay]
