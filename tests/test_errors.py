import email.utils
import time

import httpx
import pytest

from _helpers import Mock, client, error
from lobstrio import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BillingError,
    InvalidRequestError,
    LobstrError,
    RateLimitError,
    SiteError,
)
from lobstrio._errors import error_from_response, parse_retry_after


@pytest.mark.parametrize(
    "status, code, type_, cls",
    [
        (400, "invalid_limit", "invalid_request_error", InvalidRequestError),
        (404, "unknown_operation", "invalid_request_error", InvalidRequestError),
        (409, "idempotency_conflict", "invalid_request_error", InvalidRequestError),
        (410, "response_expired", "invalid_request_error", InvalidRequestError),
        (422, "invalid_options", "invalid_request_error", InvalidRequestError),
        (401, "invalid_api_key", "authentication_error", AuthenticationError),
        (402, "insufficient_credits", "billing_error", BillingError),
        (429, "concurrency_limit", "rate_limit_error", RateLimitError),
        (408, "timeout", "site_error", SiteError),
        (424, "unexpected_answer", "site_error", SiteError),
        (503, "site_challenge", "site_error", SiteError),
        (500, "internal_error", "api_error", APIError),
    ],
)
def test_each_type_has_its_class(status, code, type_, cls):
    mock = Mock(error(status, code, type_, param="options.place"))
    with pytest.raises(cls) as info:
        client(mock, max_retries=0).run("google_maps/places/get", place="x")
    err = info.value
    assert isinstance(err, LobstrError)
    assert err.status == status
    assert err.type == type_
    assert err.code == code
    assert err.message == f"The message of {code}."
    assert err.param == "options.place"
    assert err.doc_url == f"https://docs.lobstr.io/errors#{code}"
    assert err.request_id == f"req_{code}"


def test_the_class_follows_the_status_when_the_type_is_unknown():
    assert (
        type(
            error_from_response(422, b'{"error": {"type": "new", "code": "x", "message": "m"}}', {})
        )
        is InvalidRequestError
    )
    forbidden = error_from_response(
        403, b'{"error": {"code": "profile_protected", "message": "m"}}', {}
    )
    assert type(forbidden) is InvalidRequestError and forbidden.type == "invalid_request_error"
    assert type(error_from_response(502, b"<html>", {})) is APIError
    assert type(error_from_response(504, b"", {})) is APIError
    assert type(error_from_response(503, b"", {})) is SiteError


def test_an_answer_without_an_error_object():
    err = error_from_response(
        502, b"<html>Bad gateway</html>", httpx.Headers({"request-id": "req_9"})
    )
    assert err.status == 502 and err.code == "http_502" and err.type == "api_error"
    assert err.retryable is True
    assert err.request_id == "req_9"
    assert err.message == "HTTP 502: <html>Bad gateway</html>"
    assert error_from_response(404, b"", {}).message == "HTTP 404"
    assert error_from_response(429, b"", {}).retryable is False


def test_retry_after_comes_from_the_body_then_the_header():
    body = b'{"error": {"type": "rate_limit_error", "code": "concurrency_limit", "message": "m", "retryable": true}}'
    err = error_from_response(429, body, httpx.Headers({"Retry-After": "7"}))
    assert err.retry_after == 7 and err.retryable is True


def test_retry_after_as_an_http_date():
    when = email.utils.formatdate(time.time() + 30, usegmt=True)
    assert 25 <= parse_retry_after(when) <= 31
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_str_and_repr():
    err = error_from_response(
        422,
        b'{"error": {"type": "invalid_request_error", "code": "invalid_options", "message": "query takes text", "request_id": "req_1", "retryable": false}}',
        {},
    )
    assert str(err) == "422 invalid_options: query takes text (request_id: req_1)"
    assert "InvalidRequestError(status=422, code='invalid_options'" in repr(err)


def test_client_side_errors_are_lobstr_errors():
    assert issubclass(APITimeoutError, APIConnectionError)
    assert issubclass(APIConnectionError, LobstrError)
    assert APIConnectionError("x").retryable is True
    assert APIConnectionError("x").code == "connection_error"
    assert APITimeoutError("x").code == "client_timeout"
    assert APITimeoutError("x").type == "api_error"
