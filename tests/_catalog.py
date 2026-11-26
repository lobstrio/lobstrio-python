"""A small catalog for the agent tools' tests: four operations, as the API describes them, and a
mocked API that answers the catalog from them and each run from a queue."""

from typing import Any, Dict, List

import httpx

from _helpers import Mock, error, ok


def _item(op: str, site: Dict[str, str], title: str, summary: str, use_when: str, credits: int):
    source, operation = op.split("/", 1)
    return {
        "object": "operation",
        "id": op,
        "source": source,
        "operation": operation,
        "site": site,
        "title": title,
        "summary": summary,
        "use_when": use_when,
        "categories": ["Lead generation"],
        "price": {"credits_per_call": credits},
    }


MAPS = {"title": "Google Maps", "domain": "google.com/maps"}
PLACES_SEARCH = _item(
    "google_maps/places/search",
    MAPS,
    "Search places",
    "One page of places for a Google Maps search, 20 at most.",
    'You need the places Google Maps lists for a search such as "dentist" near a point.',
    20,
)
PLACES_GET = _item(
    "google_maps/places/get",
    MAPS,
    "Get a place",
    "One Google Maps place whole.",
    "You have a place and need its full profile.",
    1,
)
CONTACTS_GET = _item(
    "websites/contacts/get",
    {"title": "Websites", "domain": "any website"},
    "Get contacts",
    "The emails, phones and profiles a website shows.",
    "You have a company's website and need how to reach it: emails, phones.",
    1,
)
NEWS_SEARCH = _item(
    "google_news/articles/search",
    {"title": "Google News", "domain": "news.google.com"},
    "Search articles",
    "The news articles Google News shows for a query.",
    "You need the news about a brand or a topic.",
    1,
)
CATALOG = {
    "object": "list",
    "data": [PLACES_GET, PLACES_SEARCH, NEWS_SEARCH, CONTACTS_GET],
    "has_more": False,
}

PLACE_RECORD = {
    "type": "object",
    "properties": {
        "object": {"const": "place"},
        "id": {"type": "string"},
        "name": {"type": "string"},
        "address": {
            "type": "object",
            "properties": {"full": {"type": "string"}, "city": {"type": "string"}},
        },
        "categories": {"type": "array", "items": {"type": "string"}},
        "phone": {"type": ["string", "null"]},
    },
}
CONTRACTS: Dict[str, Dict[str, Any]] = {
    "google_maps/places/search": {
        **PLACES_SEARCH,
        "input": {
            "type": "object",
            "required": ["query"],
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "What you would type in Google Maps."},
                "near": {
                    "type": "object",
                    "required": ["lat", "lng"],
                    "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}},
                    "description": "The point to search around.",
                },
                "page": {"type": "integer", "minimum": 1, "default": 1, "description": "A page."},
                "cursor": {"type": "string", "description": "Where to go on, from `next`."},
            },
        },
        "record": PLACE_RECORD,
        "pagination": {"style": "page", "page_size": 20, "next": {"cursor": "<opaque>"}},
        "price": {"credits_per_call": 20, "description": "20 credits a call."},
    },
    "google_maps/places/get": {
        **PLACES_GET,
        "input": {
            "type": "object",
            "required": ["place"],
            "additionalProperties": False,
            "properties": {"place": {"type": "string", "description": "The place's id."}},
        },
        "record": PLACE_RECORD,
        "pagination": {"style": "none", "page_size": 1},
        "price": {"credits_per_call": 1, "description": "1 credit a call."},
    },
    "websites/contacts/get": {
        **CONTACTS_GET,
        "input": {
            "type": "object",
            "required": ["website"],
            "additionalProperties": False,
            "properties": {
                "website": {
                    "type": "string",
                    "format": "uri",
                    "description": "The site's address.",
                },
                "max_pages": {"type": "integer", "default": 4, "description": "Pages read."},
            },
        },
        "record": {"type": "object", "properties": {"emails": {"type": "array"}}},
        "pagination": None,
        "price": {"credits_per_call": 1, "description": "1 credit a call."},
    },
}


def api(*runs: Any) -> Mock:
    """A mocked API: the catalog from the fixtures, each ``POST /v2/run`` from ``runs``."""
    queue: List[Any] = list(runs)

    def answer(request: httpx.Request) -> Any:
        path = request.url.path
        if request.method == "GET" and path == "/v2/catalog":
            return ok(CATALOG)
        if request.method == "GET" and path.startswith("/v2/catalog/"):
            op = path[len("/v2/catalog/") :]
            if op in CONTRACTS:
                return ok(CONTRACTS[op])
            return error(404, "unknown_operation", "invalid_request_error", param="operation")
        if request.method == "POST" and path == "/v2/run":
            if not queue:
                raise AssertionError("no run queued")
            return queue.pop(0)
        raise AssertionError(f"unexpected {request.method} {path}")

    return Mock(default=answer)


def paths(mock: Mock) -> List[str]:
    return [f"{r.method} {r.url.path}" for r in mock.requests]
