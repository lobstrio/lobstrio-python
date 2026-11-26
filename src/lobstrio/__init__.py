"""The official Python SDK of lobstr.io's API v2: structured data from websites, one call per page.

    from lobstrio import Lobstr

    client = Lobstr()  # the key from LOBSTR_API_KEY
    run = client.run("google_maps/places/search", {"query": "bakeries in Austin, TX"})
    print(run.cost["credits"], run.results[0]["name"])

Docs: https://docs.lobstr.io
"""

from ._client import AsyncLobstr, Lobstr
from ._errors import (
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
from ._generated import OPERATIONS
from ._resources import AsyncRunLogPage, RunLogPage
from ._run import AsyncRun, Run
from ._version import __version__

__all__ = [
    "Lobstr",
    "AsyncLobstr",
    "Run",
    "AsyncRun",
    "RunLogPage",
    "AsyncRunLogPage",
    "OPERATIONS",
    "LobstrError",
    "InvalidRequestError",
    "AuthenticationError",
    "BillingError",
    "RateLimitError",
    "SiteError",
    "APIError",
    "APIConnectionError",
    "APITimeoutError",
    "__version__",
]
