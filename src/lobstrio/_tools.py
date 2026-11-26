"""The tools an agent uses lobstr.io with, whatever its framework.

The same three tools as lobstr.io's MCP server (https://api.lobstr.io/v2/mcp): find an operation,
read its contract, run it; plus, on demand, one tool per operation with its options as arguments.
``lobstrio.langchain`` and ``lobstrio.crewai`` wrap them. Every tool answers compact JSON text,
errors included: an API error comes back to the agent as the API's error object (its message and
doc_url), never as an exception in the agent's loop. The calls go through the client, with its
retries and its idempotency keys.
"""

import copy
import json
import math
import re
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from ._base import normalize_fields, split_operation
from ._client import AsyncLobstr, Lobstr
from ._errors import LobstrError

__all__ = ["Tool", "LobstrTools", "FIND_OPERATIONS", "GET_OPERATION", "RUN_OPERATION"]

FIND_OPERATIONS = "lobstr_find_operations"
GET_OPERATION = "lobstr_get_operation"
RUN_OPERATION = "lobstr_run_operation"

# The tools' wording follows the MCP server's (lobstrio/platform, server/lobstrio/agents.py).
OPERATION_ID = 'The operation\'s id, source/resource/verb, as lobstr_find_operations gives it: "google_maps/reviews/list".'
QUERY = (
    'What you need, in words, as you would ask it: "reviews of a restaurant on google maps". '
    "Empty: every operation, one line each."
)
OPTIONS = (
    "The operation's input, its options as lobstr_get_operation gives them (`input`; those in "
    '`required` are required): {"query": "bakeries in Austin, TX"}. For the next page, the '
    "answer's `next` options, as they are."
)
FIELDS = (
    'Only these record fields, by path ("name", "address.full"); object, id and position always '
    "come. Left out: every field, which can be large."
)
LIMIT = "Only the first N records of the page (1 to 1000), for a search or a list; the call costs the same."

FIND_DESCRIPTION = (
    "Find the operations of lobstr.io that return the data you need, from what you would ask in "
    'words ("reviews of a restaurant on google maps", "places near a point"): the few that fit '
    "best, each with its id (source/resource/verb), its title, what it returns and its price per "
    "call. lobstr_get_operation gives one's options and record fields. An empty query lists every "
    "operation in one line."
)
GET_DESCRIPTION = (
    'Everything needed to run one operation, by its id from lobstr_find_operations ("google_maps/'
    'reviews/list"): when to use it, its options (`input`, a JSON schema, with their defaults), the '
    "fields of its records (the paths `fields` takes), its price per call and its pagination."
)
RUN_DESCRIPTION = """Run one lobstr.io operation and get one page of records in `results`, with `count`, `cost` (in credits), `has_more` and `next`. `options` is the operation's input (see lobstr_get_operation).

`fields`: the record fields you need, by path ("name", "rating", "phone", "address.full"); `object`, `id` and `position` always come. Ask only for what you need: every field of a full page is large (20 places with all their fields are about 70,000 characters, more than you may be able to read in one answer), the same page with four fields is a few thousand.

`limit`: only the first N records of the page, for a search or a list; the call costs the same.

One call is one page (20 places, 20 reviews). For more, call lobstr_run_operation again with the answer's `next` as it is (its operation, options, fields and limit) until you have enough or `next` is null: 50 places are 3 calls. Each call costs its operation's credits; a failed call is free."""

FIND_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": QUERY, "default": ""}},
    "additionalProperties": False,
}
GET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"operation": {"type": "string", "description": OPERATION_ID}},
    "required": ["operation"],
    "additionalProperties": False,
}
FIELDS_SCHEMA: Dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": FIELDS,
}
LIMIT_SCHEMA: Dict[str, Any] = {
    "type": "integer",
    "minimum": 1,
    "maximum": 1000,
    "description": LIMIT,
}
RUN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "description": OPERATION_ID},
        "options": {"type": "object", "description": OPTIONS},
        "fields": FIELDS_SCHEMA,
        "limit": LIMIT_SCHEMA,
    },
    "required": ["operation", "options"],
    "additionalProperties": False,
}

# find_operations' ranking, as the MCP server's: words that say something, weighed by where they
# are and by how rare they are in the catalog.
_WORD = re.compile(r"[a-z0-9]+")
_COMMON = frozenset(
    {
        "the", "of", "on", "in", "for", "and", "an", "to", "with", "from", "by", "at", "or", "its",
        "it", "what", "get", "all", "my", "me", "find", "want", "need", "data", "list", "one",
        "page", "you", "your", "about", "they", "their", "them", "http", "https", "www", "com",
        "fr", "sale",
    }
)  # fmt: skip
_FOUND = 5  # at most, the operations find_operations details
_CLOSE = 0.4  # and only those scoring at least this share of the best one


def compact(value: Any) -> str:
    """JSON without spaces: a third of the size an indented answer takes in an agent's context."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _words(text: Any) -> Set[str]:
    """Lower-case words that say something, a plural read as its singular."""
    found = {
        w[:-1] if len(w) > 3 and w.endswith("s") else w
        for w in _WORD.findall(str(text or "").lower())
    }
    return {w for w in found if len(w) > 1 and w not in _COMMON}


def _texts(op: Mapping[str, Any]) -> Tuple[Tuple[int, Set[str]], ...]:
    site = op.get("site") or {}
    name = " ".join(
        str(part or "")
        for part in (
            str(op.get("id", "")).replace("_", " ").replace("/", " "),
            op.get("title"),
            site.get("title"),
            site.get("domain"),
        )
    )
    described = f"{op.get('summary') or ''} {' '.join(op.get('categories') or [])}"
    return ((3, _words(name)), (2, _words(described)), (1, _words(op.get("use_when"))))


def ranked(operations: Sequence[Mapping[str, Any]], query: str) -> List[Mapping[str, Any]]:
    """The few operations that fit the query best; the whole catalog for a query of no telling
    word. A word weighs by where it is (the id, the title and the site most, then the summary and
    the categories, then when to use it) and by how rare it is in the catalog."""
    asked, every = _words(query), list(operations)
    if not asked:
        return every
    said = {op["id"]: _texts(op) for op in every}
    carried = {
        w: sum(any(w in found for _, found in said[op["id"]]) for op in every) for w in asked
    }
    rare = {w: math.log(1 + len(every) / n) for w, n in carried.items() if n}

    def score(op: Mapping[str, Any]) -> float:
        return sum(
            max((weight for weight, found in said[op["id"]] if w in found), default=0) * rare[w]
            for w in rare
        )

    scored = sorted(((score(op), op) for op in every), key=lambda pair: -pair[0])
    best = scored[0][0] if scored else 0
    return [op for s, op in scored if s and s >= _CLOSE * best][:_FOUND]


def _credits(op: Mapping[str, Any]) -> Any:
    return (op.get("price") or {}).get("credits_per_call")


def line(op: Mapping[str, Any]) -> str:
    """An operation in one line: ``airbnb/homes/search: Search homes on airbnb.com, 1 credit a call``."""
    site = op.get("site") or {}
    credits = _credits(op)
    return (
        f"{op.get('id')}: {op.get('title')} on {site.get('domain') or site.get('title')}, "
        f"{credits} {'credit' if credits == 1 else 'credits'} a call"
    )


def record_fields(record: Mapping[str, Any]) -> List[str]:
    """The record's fields, every object with all of its own: ``seller (name, phones, address
    (city, postal_code))``, the very paths ``fields`` takes (``seller.phones``). A list's items
    come whole and are named alone."""

    def named(name: str, schema: Any) -> str:
        children = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
        if not children:
            return name
        return f"{name} ({', '.join(named(k, v) for k, v in children.items())})"

    return [named(k, v) for k, v in ((record or {}).get("properties") or {}).items()]


def is_list(contract: Mapping[str, Any]) -> bool:
    """A search or a list (it takes ``limit``), not an operation that answers one record."""
    pagination = contract.get("pagination") or {}
    return (pagination.get("page_size") or 0) > 1


def error_text(err: Exception) -> str:
    """An error as the agent reads it: the API's error object, its message and doc_url first."""
    if isinstance(err, LobstrError):
        fields = {
            "message": err.message,
            "doc_url": err.doc_url,
            "type": err.type,
            "code": err.code,
            "param": err.param,
            "retryable": err.retryable,
            "retry_after": err.retry_after,
            "request_id": err.request_id,
        }
        return compact({"error": {k: v for k, v in fields.items() if v is not None}})
    return compact({"error": {"message": str(err)}})


class Tool:
    """One tool, as any framework needs it: a name, a description, the JSON schema of its
    arguments, and the function that runs it with them (keyword arguments) and answers text."""

    def __init__(
        self, name: str, description: str, parameters: Dict[str, Any], func: Callable[..., str]
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func

    def __call__(self, **kwargs: Any) -> str:
        return self.func(**kwargs)

    def __repr__(self) -> str:
        return f"Tool(name={self.name!r})"


class LobstrTools:
    """lobstr.io's tools for an agent: the three of the MCP server, and one per operation asked.

    Args:
        client: A ``lobstrio.Lobstr``; ``Lobstr()`` when left out (the key from
            ``LOBSTR_API_KEY``).
        operations: Operations to give a tool of their own, as ``google_maps/places/search``.
    """

    def __init__(
        self, client: Optional[Lobstr] = None, operations: Optional[Sequence[str]] = None
    ) -> None:
        if isinstance(client, AsyncLobstr):
            raise TypeError("the tools take a lobstrio.Lobstr, not an AsyncLobstr")
        if isinstance(operations, str):
            operations = [operations]
        for operation in operations or ():
            split_operation(operation)  # a wrong id fails here, not in the agent's loop
        self.client = client if client is not None else Lobstr()
        self.operations: List[str] = [op.strip().strip("/") for op in operations or ()]
        self._catalog: Optional[List[Dict[str, Any]]] = None
        self._contracts: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # -- the catalog, read once

    def catalog(self) -> List[Dict[str, Any]]:
        """Every operation (``GET /v2/catalog``), kept for the tools' lifetime."""
        with self._lock:
            if self._catalog is None:
                self._catalog = list(self.client.catalog.list().get("data") or [])
            return self._catalog

    def contract(self, operation: str) -> Dict[str, Any]:
        """One operation's contract (``GET /v2/catalog/{source}/{resource}/{verb}``), kept."""
        operation = operation.strip().strip("/")
        with self._lock:
            found = self._contracts.get(operation)
        if found is None:
            found = dict(self.client.catalog.get(operation))
            with self._lock:
                self._contracts[operation] = found
        return found

    # -- the three tools

    def find_operations(self, query: str = "") -> str:
        """``lobstr_find_operations``: the operations that fit the query, compact JSON."""
        try:
            every = self.catalog()
        except LobstrError as err:
            return error_text(err)
        if not _words(query):
            return compact(
                {
                    "operations": [line(op) for op in every],
                    "note": "Ask with words for the details of those that fit.",
                }
            )
        found = ranked(every, query)
        if not found:
            return compact(
                {
                    "operations": [line(op) for op in every],
                    "note": "Nothing matched these words: here is every operation; ask with its words.",
                }
            )
        return compact(
            {
                "operations": [
                    {
                        "id": op.get("id"),
                        "title": op.get("title"),
                        "summary": op.get("summary"),
                        "credits_per_call": _credits(op),
                    }
                    for op in found
                ]
            }
        )

    def get_operation(self, operation: str) -> str:
        """``lobstr_get_operation``: one operation's contract, compact JSON."""
        try:
            contract = self.contract(operation)
        except (LobstrError, ValueError, TypeError) as err:
            return error_text(err)
        return compact(
            {
                "id": contract.get("id"),
                "title": contract.get("title"),
                "summary": contract.get("summary"),
                "use_when": contract.get("use_when"),
                "input": contract.get("input"),
                "record_fields": record_fields(contract.get("record") or {}),
                "price": contract.get("price"),
                "pagination": contract.get("pagination"),
            }
        )

    def run_operation(
        self,
        operation: str,
        options: Optional[Mapping[str, Any]] = None,
        fields: Union[None, str, Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> str:
        """``lobstr_run_operation``: one call, one page, compact JSON; ``next`` in this tool's
        own arguments."""
        return self._run(operation, options, fields, limit, own_tool=False)

    def _run(
        self,
        operation: str,
        options: Optional[Mapping[str, Any]],
        fields: Union[None, str, Sequence[str]],
        limit: Optional[int],
        *,
        own_tool: bool,
    ) -> str:
        try:
            run = self.client.run(
                operation,
                dict(options or {}),
                fields=normalize_fields(fields) or None,
                limit=limit or None,
            )
        except (LobstrError, ValueError, TypeError) as err:
            return error_text(err)
        return compact(
            {
                "count": run.count,
                "cost": run.cost,
                "has_more": run.has_more,
                "next": _next_call(run.next, own_tool=own_tool),
                "results": run.results,
            }
        )

    # -- one tool per operation

    def operation_tool(self, operation: str) -> Tool:
        """The tool of one operation, named ``google_maps_places_search``: its arguments are the
        operation's options (its ``input`` schema as it is), plus ``fields``, and ``limit`` for a
        search or a list."""
        contract = self.contract(operation)
        op_id = str(contract.get("id") or operation)
        listed = is_list(contract)
        parameters = copy.deepcopy(contract.get("input") or {"type": "object", "properties": {}})
        parameters.setdefault("type", "object")
        properties = parameters.setdefault("properties", {})
        properties["fields"] = copy.deepcopy(FIELDS_SCHEMA)
        if listed:
            properties["limit"] = copy.deepcopy(LIMIT_SCHEMA)
        description = "\n\n".join(
            part for part in (contract.get("summary"), contract.get("use_when")) if part
        )

        def run(**kwargs: Any) -> str:
            fields = kwargs.pop("fields", None)
            limit = kwargs.pop("limit", None) if listed else None
            options = {k: v for k, v in kwargs.items() if v is not None}
            return self._run(op_id, options, fields, limit, own_tool=True)

        return Tool(op_id.replace("/", "_"), description, parameters, run)

    def tools(self) -> List[Tool]:
        """The three tools, then one per operation given."""
        return [
            Tool(
                FIND_OPERATIONS, FIND_DESCRIPTION, copy.deepcopy(FIND_SCHEMA), self.find_operations
            ),
            Tool(GET_OPERATION, GET_DESCRIPTION, copy.deepcopy(GET_SCHEMA), self.get_operation),
            Tool(RUN_OPERATION, RUN_DESCRIPTION, copy.deepcopy(RUN_SCHEMA), self.run_operation),
            *(self.operation_tool(op) for op in self.operations),
        ]


def _next_call(nxt: Optional[Mapping[str, Any]], *, own_tool: bool) -> Optional[Dict[str, Any]]:
    """The API's ``next`` (a request body) as the tool's own arguments, to call it again with."""
    if not nxt:
        return None
    extra: Dict[str, Any] = {}
    if nxt.get("fields"):
        extra["fields"] = nxt["fields"]
    if nxt.get("limit"):
        extra["limit"] = nxt["limit"]
    options = dict(nxt.get("options") or {})
    if own_tool:
        return {**options, **extra}
    return {"operation": f"{nxt.get('source')}/{nxt.get('operation')}", "options": options, **extra}
