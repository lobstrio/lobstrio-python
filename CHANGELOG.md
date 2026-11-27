# Changelog

All notable changes to `lobstrio`, the Python SDK and CLI of lobstr.io's API v2, are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-11-27

### Added

- `yelp/reviews/list`: its typed method, options and record.

## [1.1.0] - 2026-11-26

lobstr.io's tools for agent frameworks, in the package itself, as extras.

### Added

- `lobstrio.langchain` (`pip install "lobstrio[langchain]"`, Python 3.11 or later):
  `LobstrToolkit(client=None, operations=None)` and its `get_tools()`, the three tools of lobstr.io's
  MCP server as LangChain `StructuredTool`s: `lobstr_find_operations`, `lobstr_get_operation`,
  `lobstr_run_operation`. `operations=["google_maps/places/search", ...]` adds one tool per operation,
  named `google_maps_places_search`, whose arguments are the operation's `input` schema from the
  catalog, as it is, plus `fields`, and `limit` for a search or a list.
- `lobstrio.crewai` (`pip install "lobstrio[crewai]"`, Python 3.10 to 3.13):
  `lobstr_tools(client=None, operations=None)`, the same tools as CrewAI tools. Their arguments are
  sent as the agent gave them: the schema's defaults are left to the API.
- Every tool answers compact JSON: `lobstr_run_operation` gives `count`, `cost`, `has_more`, `next`
  (the tool's own arguments for the following page) and `results`. An API error comes back to the
  agent as text, the API's error object with its message and `doc_url`, never as an exception in the
  agent's loop; the client's retries stay underneath. The catalog is read once per toolkit.
- CI: an `integrations` job runs the tools' tests with langchain-core and crewai, on Python 3.13.

### Changed

- The README, the docstrings and the CLI help give US examples: bakeries in Austin, TX, the reviews
  of Katz's Delicatessen in New York.

## [1.0.0] - 2026-11-26

The SDK of the API v2. It replaces the archived v1 CLI published as `lobstrio` 0.7.2.

### Added

- `Lobstr` and `AsyncLobstr`, on `httpx`: the key from `LOBSTR_API_KEY`, `base_url`, `timeout` (180 s;
  a run waits at least its `timeout_s` plus 30 s), `max_retries`.
- `run(operation, options, fields=, limit=, timeout_s=, idempotency_key=)`, returning a `Run` with the
  envelope's fields and `results`; `run.next_page()`; `send(request)` for a raw request such as `next`.
- `iterate(...)`: every page's records, with `max_records` and `max_pages`.
- `run_many(calls, concurrency=4)`: calls in parallel, waiting on `429 concurrency_limit` up to 100
  times with the same key, one run or error per call in their order.
- Typed methods for the 40 operations of the API, generated from its OpenAPI
  (`client.google_maps.places.search(...)` and `.iterate(...)`; an operation that answers one record
  takes no `limit` and has no `.iterate`), with a `TypedDict` for each operation's options and record
  in `lobstrio.types`, and `RunLog`, `RunLogList`, `Account`, `Balance`, `ConnectAccountParams`,
  `Feedback` for the other routes.
- `catalog.list()`, `catalog.get(op)`, `catalog.status(op)`, `balance()`, `accounts.list()`,
  `accounts.get(id)`, `accounts.delete(id)`, `accounts.connect(...)`, `runs.list(...)`,
  `runs.iterate(...)`, `runs.get(id)`, `runs.response(id)`, `feedback(...)`.
- Retries of retryable failures, after `retry_after` when given (over 60 s, raised instead), with the
  same `Idempotency-Key` across a run's retries; a POST without a key is not sent again after a lost
  connection.
- `LobstrError` and its subclasses by the API's error `type`, plus `APIConnectionError` and
  `APITimeoutError`.
- The `lobstr` command: `run`, `catalog`, `operation`, `balance`, `runs`, `feedback`; the same grammar
  as the Node SDK's.
- `scripts/generate.py`, which writes `src/lobstrio/_generated.py` from the OpenAPI, joining back the
  descriptions the OpenAPI cuts at a comma.

[1.2.0]: https://github.com/lobstrio/lobstrio-python/releases/tag/v1.2.0
[1.1.0]: https://github.com/lobstrio/lobstrio-python/releases/tag/v1.1.0
[1.0.0]: https://github.com/lobstrio/lobstrio-python/releases/tag/v1.0.0
