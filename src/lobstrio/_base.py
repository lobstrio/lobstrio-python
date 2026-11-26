"""What the sync and async clients share: configuration, requests, the retry policy."""

import datetime
import os
import platform
import random
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import quote

import httpx

from ._errors import APIConnectionError, LobstrError, missing_api_key
from ._version import __version__

DEFAULT_BASE_URL = "https://api.lobstr.io"
# Seconds: above every operation's own deadline, with room for the network. A run waits at least
# its timeout_s plus RUN_TIMEOUT_MARGIN.
DEFAULT_TIMEOUT = 180.0
RUN_TIMEOUT_MARGIN = 30.0
DEFAULT_MAX_RETRIES = 2
# A retry_after longer than this is not waited for: the error is raised at once
# (an account's day of calls starts over at midnight UTC, hours away).
MAX_RETRY_WAIT = 60.0
# run_many waits on concurrency_limit up to this many times per call, with the same key.
MAX_CONCURRENCY_WAITS = 100

USER_AGENT = (
    f"lobstrio-python/{__version__} "
    f"(python/{platform.python_version()}; {platform.system().lower()}-{platform.machine().lower()})"
)

Timeout = Union[float, httpx.Timeout]


def split_operation(operation: str) -> Tuple[str, str]:
    """``google_maps/places/search`` -> ``("google_maps", "places/search")``."""
    if not isinstance(operation, str):
        raise TypeError(
            f"operation must be a string, as google_maps/places/search, not {operation!r}"
        )
    parts = operation.strip().strip("/").split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"operation is the full source/resource/verb, as google_maps/places/search: "
            f"{operation!r} is not one. Lobstr().catalog.list() lists them."
        )
    return parts[0], f"{parts[1]}/{parts[2]}"


def operation_path(operation: str) -> str:
    source, rest = split_operation(operation)
    return "/".join(quote(part, safe="") for part in [source, *rest.split("/")])


def normalize_fields(fields: Union[None, str, Sequence[str]]) -> Optional[list]:
    if fields is None:
        return None
    if isinstance(fields, str):
        return [part.strip() for part in fields.split(",") if part.strip()]
    return list(fields)


def build_request(
    operation: str,
    options: Optional[Mapping[str, Any]],
    option_kwargs: Mapping[str, Any],
    *,
    fields: Union[None, str, Sequence[str]] = None,
    limit: Optional[int] = None,
    timeout_s: Optional[int] = None,
) -> Dict[str, Any]:
    """The body of ``POST /v2/run`` for an operation and its options."""
    source, op = split_operation(operation)
    if options is not None and not isinstance(options, Mapping):
        raise TypeError(f"options must be a dict, not {type(options).__name__}")
    merged: Dict[str, Any] = dict(options or {})
    merged.update(option_kwargs)
    request: Dict[str, Any] = {"source": source, "operation": op, "options": merged}
    field_list = normalize_fields(fields)
    if field_list is not None:
        request["fields"] = field_list
    if limit is not None:
        request["limit"] = limit
    if timeout_s is not None:
        request["timeout_s"] = timeout_s
    return request


def normalize_call(call: Any) -> Tuple[Dict[str, Any], Optional[str]]:
    """One item of ``run_many`` -> (the request to send, its idempotency key or None).

    An item is ``{"operation": "google_maps/places/search", "options": {...}, ...}``, a request
    as ``next`` gives it (``source`` + ``operation``), or a tuple ``(operation, options)``.
    """
    if isinstance(call, (tuple, list)):
        if not 1 <= len(call) <= 2:
            raise ValueError("a call as a tuple is (operation, options)")
        options = call[1] if len(call) == 2 else None
        return build_request(call[0], options, {}), None
    if not isinstance(call, Mapping):
        raise TypeError(f"a call is a dict or an (operation, options) tuple, not {call!r}")
    if "source" in call:
        return dict(call), None
    item = dict(call)
    key = item.pop("idempotency_key", None)
    operation = item.pop("operation", None)
    if operation is None:
        raise ValueError(f"a call needs its operation, as google_maps/places/search: {call!r}")
    options = item.pop("options", None)
    fields = item.pop("fields", None)
    limit = item.pop("limit", None)
    timeout_s = item.pop("timeout_s", None)
    if item:
        raise ValueError(
            f"unknown keys in a call: {sorted(item)}; a call takes operation, options, "
            "fields, limit, timeout_s, idempotency_key"
        )
    request = build_request(operation, options, {}, fields=fields, limit=limit, timeout_s=timeout_s)
    return request, key


def utc_string(value: Union[None, str, datetime.datetime, datetime.date]) -> Optional[str]:
    """A date for the API's filters, UTC as ``2026-11-09T08:00:00Z``; a naive datetime is UTC."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return value.replace(microsecond=0).isoformat() + "Z"
    if isinstance(value, datetime.date):
        return f"{value.isoformat()}T00:00:00Z"
    raise TypeError(f"a date is a str or a datetime, not {type(value).__name__}")


def clean_params(params: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not params:
        return None
    return {key: value for key, value in params.items() if value is not None}


def drop_none(**values: Any) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class BaseClient:
    def __init__(
        self,
        api_key: Optional[str],
        *,
        base_url: str,
        timeout: Timeout,
        max_retries: int,
        default_headers: Optional[Mapping[str, str]],
    ) -> None:
        if api_key is None:
            api_key = os.environ.get("LOBSTR_API_KEY")
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self._default_headers = dict(default_headers or {})

    def __repr__(self) -> str:
        key = "set" if self._api_key else "missing"
        return f"<{type(self).__name__} base_url={self.base_url!r} api_key={key}>"

    def _headers(self, *, auth: bool, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        headers.update(self._default_headers)
        if auth:
            # The catalog's calls (auth=False) neither need the key nor get it.
            if not self._api_key:
                raise missing_api_key()
            headers["Authorization"] = f"Bearer {self._api_key}"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return self.base_url + ("" if path.startswith("/") else "/") + path

    def _run_timeout(self, timeout_s: Any) -> Timeout:
        # A call may wait up to its timeout_s on the API's side: never hang up before it.
        if isinstance(timeout_s, (int, float)) and isinstance(self.timeout, (int, float)):
            return max(float(self.timeout), float(timeout_s) + RUN_TIMEOUT_MARGIN)
        return self.timeout

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.5 * (2**attempt)) * random.uniform(0.5, 1.0)

    @staticmethod
    def _is_retryable(err: LobstrError) -> bool:
        if err.retryable or err.code == "idempotency_in_flight":
            return True
        status = err.status or 0
        return (status == 429 and err.retry_after is not None) or status >= 500

    def _retry_wait(self, err: LobstrError, attempt: int, *, idempotent: bool) -> Optional[float]:
        """Seconds to wait before retrying, or None to raise ``err``.

        Retried: a lost connection or a client timeout when the call is safe to send again (a GET,
        a DELETE, a run with its key; never a POST without one), the error's own ``retryable``, a
        429 with ``retry_after``, ``409 idempotency_in_flight``, any 5xx. A ``retry_after`` above
        a minute (a day's quota) is raised, not waited on.
        """
        if attempt >= self.max_retries:
            return None
        if isinstance(err, APIConnectionError):
            return self._backoff(attempt) if idempotent else None
        if not self._is_retryable(err):
            return None
        wait = err.retry_after
        if wait is None:
            wait = 2.0 if err.code == "idempotency_in_flight" else self._backoff(attempt)
        if wait > MAX_RETRY_WAIT:
            return None
        return float(wait)

    @staticmethod
    def _concurrency_wait(err: Exception, waits: int) -> Optional[float]:
        """run_many: how long to wait on ``concurrency_limit`` before trying again, or None."""
        if not isinstance(err, LobstrError) or err.code != "concurrency_limit":
            return None
        if waits >= MAX_CONCURRENCY_WAITS:
            return None
        wait = float(err.retry_after) if err.retry_after is not None else 1.0
        return wait + random.uniform(0.0, 0.25)
