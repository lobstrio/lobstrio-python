"""lobstr.io's tools for CrewAI: ``pip install "lobstrio[crewai]"``.

    from crewai import Agent
    from lobstrio.crewai import lobstr_tools

    agent = Agent(role=..., goal=..., backstory=..., tools=lobstr_tools())

The three tools of lobstr.io's MCP server (``lobstr_find_operations``, ``lobstr_get_operation``,
``lobstr_run_operation``), plus one tool per operation given (``google_maps_places_search``, its
arguments the operation's options). Each answers compact JSON; an API error comes back to the
agent as text (its message and doc_url), not as an exception.
"""

import copy
import json
from typing import Any, Dict, List, Optional, Sequence, Type

try:
    from crewai.tools import BaseTool
    from crewai.utilities.pydantic_schema_utils import create_model_from_schema
except ImportError as exc:  # pragma: no cover - the extra is not installed
    raise ImportError('lobstrio.crewai needs crewai: pip install "lobstrio[crewai]"') from exc

from pydantic import BaseModel, PrivateAttr

from ._client import Lobstr
from ._tools import LobstrTools, Tool, compact

__all__ = ["lobstr_tools", "LobstrTool"]


def _without_defaults(schema: Any) -> Any:
    """The schema without its defaults: CrewAI sends every field of its arguments model, and a
    default sent as if the agent had chosen it can change a call (``page`` beside a ``cursor``).
    The API applies its defaults itself."""
    if isinstance(schema, dict):
        schema.pop("default", None)
        for child in (schema.get("properties") or {}).values():
            _without_defaults(child)
        for key in ("items", "additionalProperties"):
            _without_defaults(schema.get(key))
        for key in ("anyOf", "oneOf", "allOf"):
            for child in schema.get(key) or ():
                _without_defaults(child)
    return schema


def _args_model(tool: Tool) -> Type[BaseModel]:
    name = "".join(part.title() for part in tool.name.split("_")) + "Args"
    schema = _without_defaults(copy.deepcopy(tool.parameters))
    return create_model_from_schema(schema, model_name=name, enrich_descriptions=True)


def _plain(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """The arguments as JSON values (a URL, a date as their text), those left out dropped."""
    values = json.loads(compact(arguments))
    return {key: value for key, value in values.items() if value is not None}


class LobstrTool(BaseTool):
    """One of lobstr.io's tools, as a CrewAI tool."""

    _tool: Tool = PrivateAttr()

    def __init__(self, tool: Tool, **kwargs: Any) -> None:
        super().__init__(
            name=tool.name, description=tool.description, args_schema=_args_model(tool), **kwargs
        )
        self._tool = tool

    def _run(self, **kwargs: Any) -> str:
        return self._tool(**_plain(kwargs))


def lobstr_tools(
    client: Optional[Lobstr] = None, operations: Optional[Sequence[str]] = None
) -> List[BaseTool]:
    """lobstr.io's tools for a CrewAI agent: the three, then one per operation given.

    Args:
        client: A ``lobstrio.Lobstr``; ``Lobstr()`` when left out (the key from
            ``LOBSTR_API_KEY``).
        operations: Operations to give a tool of their own, as ``google_maps/places/search``:
            named ``google_maps_places_search``, its arguments the operation's options, plus
            ``fields``, and ``limit`` for a search or a list. Their contracts are read now (no
            key needed); the catalog, at the first tool that needs it.
    """
    return [LobstrTool(tool) for tool in LobstrTools(client, operations).tools()]
