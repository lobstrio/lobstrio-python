"""The errors the SDK raises: the API's error object, as an exception."""

import email.utils
import json
import time
from typing import Any, Dict, Mapping, Optional, Type

__all__ = [
    "LobstrError",
    "InvalidRequestError",
    "AuthenticationError",
    "BillingError",
    "RateLimitError",
    "SiteError",
    "APIError",
    "APIConnectionError",
    "APITimeoutError",
]


class LobstrError(Exception):
    """An error from lobstr.io's API, or from the way to it.

    Every field of the API's error object is an attribute: ``status`` (the HTTP status, ``None``
    when no answer came), ``type``, ``code``, ``message``, ``param``, ``doc_url``, ``request_id``,
    ``retryable`` and ``retry_after`` (seconds). A failed call costs nothing.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        type: Optional[str] = None,
        code: Optional[str] = None,
        param: Optional[str] = None,
        doc_url: Optional[str] = None,
        request_id: Optional[str] = None,
        retryable: bool = False,
        retry_after: Optional[float] = None,
        body: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.type = type
        self.code = code
        self.param = param
        self.doc_url = doc_url
        self.request_id = request_id
        self.retryable = retryable
        self.retry_after = retry_after
        self.body = body
        self.headers: Dict[str, str] = dict(headers or {})

    def __str__(self) -> str:
        head = " ".join(str(part) for part in (self.status, self.code) if part is not None)
        text = f"{head}: {self.message}" if head else self.message
        if self.request_id:
            text += f" (request_id: {self.request_id})"
        return text

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(status={self.status!r}, code={self.code!r}, "
            f"message={self.message!r}, param={self.param!r}, request_id={self.request_id!r})"
        )


class InvalidRequestError(LobstrError):
    """The call is not one the API takes: 400, 404, 409, 410, 422."""


class AuthenticationError(LobstrError):
    """No API key, or a wrong one: 401."""


class BillingError(LobstrError):
    """The call costs more credits than are left: 402."""


class RateLimitError(LobstrError):
    """Too many calls at a time, or an account's own pace: 429. ``retry_after`` says when."""


class SiteError(LobstrError):
    """The site did not answer as it should: 408, 424, 503."""


class APIError(LobstrError):
    """Something broke on lobstr.io's side: 500 and other 5xx."""


class APIConnectionError(LobstrError):
    """No answer came: the connection failed or was cut (``code`` is ``connection_error``)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("type", "api_error")
        kwargs.setdefault("code", "connection_error")
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class APITimeoutError(APIConnectionError):
    """No answer within the client's timeout (``code`` is ``client_timeout``)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        kwargs.setdefault("code", "client_timeout")
        super().__init__(message, **kwargs)


_BY_TYPE: Dict[str, Type[LobstrError]] = {
    "invalid_request_error": InvalidRequestError,
    "authentication_error": AuthenticationError,
    "billing_error": BillingError,
    "rate_limit_error": RateLimitError,
    "site_error": SiteError,
    "api_error": APIError,
}


def type_for_status(status: int) -> str:
    """The error ``type`` the API gives a status, for an answer without an error object."""
    if status == 401:
        return "authentication_error"
    if status == 402:
        return "billing_error"
    if status == 429:
        return "rate_limit_error"
    if status in (408, 424, 503):
        return "site_error"
    if status >= 500:
        return "api_error"
    return "invalid_request_error"


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Reads a ``Retry-After`` header: seconds, or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        parsed = email.utils.parsedate_tz(value)
        if parsed is None:
            return None
        seconds = email.utils.mktime_tz(parsed) - time.time()
    return max(seconds, 0.0)


def error_class(type_: Optional[str], status: int) -> Type[LobstrError]:
    """The class of a type, or of the status when the type is one this version does not know."""
    if type_ in _BY_TYPE:
        return _BY_TYPE[type_]
    return _BY_TYPE[type_for_status(status)]


def error_from_response(status: int, content: bytes, headers: Mapping[str, str]) -> LobstrError:
    """Builds the exception for an answer with a 4xx or 5xx status."""
    body: Any = None
    try:
        body = json.loads(content) if content else None
    except ValueError:
        body = None
    obj = body.get("error") if isinstance(body, dict) else None
    header_wait = parse_retry_after(headers.get("retry-after"))
    request_id = headers.get("request-id")
    if not isinstance(obj, dict):
        # No error object: a proxy's page, or an empty answer.
        text = content.decode("utf-8", "replace").strip()[:500] if content else ""
        type_ = type_for_status(status)
        return error_class(type_, status)(
            f"HTTP {status}: {text}" if text else f"HTTP {status}",
            status=status,
            type=type_,
            code=f"http_{status}",
            request_id=request_id,
            retryable=status >= 500,
            retry_after=header_wait,
            body=body if body is not None else (text or None),
            headers=headers,
        )
    type_ = obj.get("type") if isinstance(obj.get("type"), str) else type_for_status(status)
    retry_after = obj.get("retry_after")
    if not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool):
        retry_after = header_wait
    retryable = obj.get("retryable")
    return error_class(type_, status)(
        str(obj.get("message") or f"HTTP {status}"),
        status=status,
        type=type_,
        code=obj.get("code") if isinstance(obj.get("code"), str) else f"http_{status}",
        param=obj.get("param"),
        doc_url=obj.get("doc_url"),
        request_id=obj.get("request_id") or request_id,
        retryable=retryable if isinstance(retryable, bool) else status >= 500,
        retry_after=retry_after,
        body=body,
        headers=headers,
    )


def missing_api_key() -> AuthenticationError:
    return AuthenticationError(
        "No API key: pass api_key to Lobstr() or set the LOBSTR_API_KEY environment variable. "
        "It is on https://app.lobstr.io/dashboard/api",
        type="authentication_error",
        code="missing_api_key",
        doc_url="https://docs.lobstr.io/errors#missing_api_key",
    )
