"""lobstr.io's tools for LangChain: ``pip install "lobstrio[langchain]"``.

    from lobstrio.langchain import LobstrToolkit

    tools = LobstrToolkit(operations=["google_maps/places/search"]).get_tools()

The three tools of lobstr.io's MCP server (``lobstr_find_operations``, ``lobstr_get_operation``,
``lobstr_run_operation``), plus one tool per operation given (``google_maps_places_search``, its
arguments the operation's options). Each answers compact JSON; an API error comes back to the
agent as text (its message and doc_url), not as an exception.
"""

from typing import Any, List, Optional, Sequence

try:
    from langchain_core.tools import BaseTool, BaseToolkit, StructuredTool
except ImportError as exc:  # pragma: no cover - the extra is not installed
    raise ImportError(
        'lobstrio.langchain needs langchain-core: pip install "lobstrio[langchain]"'
    ) from exc

from pydantic import ConfigDict, PrivateAttr

from ._client import Lobstr
from ._tools import LobstrTools, Tool

__all__ = ["LobstrToolkit"]


def _structured(tool: Tool) -> StructuredTool:
    # A JSON-schema args_schema is used as it is: what the model sees is the API's own schema.
    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.parameters,
        func=tool.func,
    )


class LobstrToolkit(BaseToolkit):
    """lobstr.io's tools for a LangChain agent.

    Args:
        client: A ``lobstrio.Lobstr``; ``Lobstr()`` when left out (the key from
            ``LOBSTR_API_KEY``).
        operations: Operations to give a tool of their own, as ``google_maps/places/search``:
            named ``google_maps_places_search``, its arguments the operation's options, plus
            ``fields``, and ``limit`` for a search or a list.

    The catalog is read once, at the first tool that needs it, and kept for the toolkit's
    lifetime; ``get_tools()`` reads the contract of each operation given (no key needed).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: Optional[Any] = None
    operations: Optional[List[str]] = None
    _tools: LobstrTools = PrivateAttr()

    def __init__(
        self,
        client: Optional[Lobstr] = None,
        operations: Optional[Sequence[str]] = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(operations, str):
            operations = [operations]
        super().__init__(
            client=client, operations=list(operations) if operations else None, **kwargs
        )

    def model_post_init(self, __context: Any) -> None:
        self._tools = LobstrTools(self.client, self.operations)
        self.client = self._tools.client

    def get_tools(self) -> List[BaseTool]:
        """The three tools, then one per operation given."""
        return [_structured(tool) for tool in self._tools.tools()]
