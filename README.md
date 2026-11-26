# lobstrio

[![PyPI](https://img.shields.io/pypi/v/lobstrio?color=blue)](https://pypi.org/project/lobstrio/)
[![Python](https://img.shields.io/pypi/pyversions/lobstrio)](https://pypi.org/project/lobstrio/)
[![Downloads](https://img.shields.io/pypi/dm/lobstrio)](https://pypistats.org/packages/lobstrio)
[![Tests](https://github.com/lobstrio/lobstrio-python/actions/workflows/test.yml/badge.svg)](https://github.com/lobstrio/lobstrio-python/actions/workflows/test.yml)
[![License](https://img.shields.io/github/license/lobstrio/lobstrio-python)](LICENSE)

The official Python SDK and CLI for [lobstr.io](https://www.lobstr.io?utm_source=lobstrio-python&utm_medium=readme)'s API v2: structured data from
websites (Google Maps places and their reviews, Google News, Airbnb, realtor.com, X and more), one call
per page, the same fields every time.

```bash
pip install lobstrio
```

Python 3.9 or later. One dependency, `httpx`.

## The key

Your API key is in the app, under [API](https://app.lobstr.io/dashboard/api). The client reads it from
`LOBSTR_API_KEY`, or takes it as `Lobstr(api_key=...)`.

```bash
export LOBSTR_API_KEY="..."
```

The catalog needs no key.

## One call

```python
from lobstrio import Lobstr

client = Lobstr()
run = client.run("google_maps/places/search", {"query": "bakeries in Austin, TX"}, limit=5)

print(run.status, run.count, run.cost["credits"], run.balance["credits"])
for place in run.results:
    print(place["name"], place["phone"])
```

An operation is `source/resource/verb`. A call answers one page: its records in `results`, with the
envelope around them (`id`, `status`, `count`, `has_more`, `next`, `cost`, `balance`, `request_id`...).
Options go as a dict or as keyword arguments; `fields` keeps only some fields of each record, `limit`
the first records of the page. `client.catalog.list()["data"]` lists the operations,
`client.catalog.get(op)` gives one's options, record, errors and price.

## Every page

A search or a list answers one page; `next` is the whole call for the following one.

```python
page = run.next_page()  # None after the last page

for review in client.iterate(
    "google_maps/reviews/list", {"place": "ChIJCar0f49ZwokR6ozLV-dHNTE"}, max_records=100
):
    print(review["rating"]["value"], review["text"])
```

`iterate` follows `next` until it is null or `max_records` or `max_pages` is reached. Each page is a
call, at the operation's price.

## Typed methods

Every operation is also a method, with its options as typed arguments and its records as a
`TypedDict`, generated from the [OpenAPI](https://api.lobstr.io/v2/openapi.json):

```python
run = client.google_maps.places.search(query="bakeries in Austin, TX", limit=5)
for place in client.google_maps.reviews.list.iterate(
    place="ChIJCar0f49ZwokR6ozLV-dHNTE", max_records=50
):
    ...

from lobstrio.types import GoogleMapsPlacesSearchRecord
```

An operation that answers one record (a get, a check) takes no `limit` and has no `.iterate`.

## Many calls

```python
results = client.run_many(
    [
        {"operation": "google_maps/places/search", "options": {"query": "bakeries in Austin, TX"}},
        {"operation": "google_maps/places/search", "options": {"query": "bakeries in Dallas, TX"}},
    ],
    concurrency=4,
)
for item in results:
    print(item if isinstance(item, Exception) else item.count)
```

The calls run in parallel, at most `concurrency` at a time. A call over your plan's calls at a time
(`429 concurrency_limit`) waits its `retry_after` and goes again, up to 100 times, with the same
`Idempotency-Key`. You get one item per call, in their order: its run, or the error it raised.

## Async

```python
from lobstrio import AsyncLobstr

async with AsyncLobstr() as client:
    run = await client.google_news.articles.search(query="electric cars", limit=10)
    async for article in client.iterate("google_news/articles/search", {"query": "lobstr.io"}):
        ...
```

## Errors

```python
from lobstrio import LobstrError, InvalidRequestError, RateLimitError

try:
    client.run("google_maps/places/get", {"place": "nowhere"})
except InvalidRequestError as e:
    print(e.status, e.code, e.message, e.param, e.doc_url, e.request_id)
```

Every error is a `LobstrError` with the API's error object as attributes: `status`, `type`, `code`,
`message`, `param`, `doc_url`, `request_id`, `retryable`, `retry_after`. One subclass by `type`:
`InvalidRequestError`, `AuthenticationError`, `BillingError`, `RateLimitError`, `SiteError`, `APIError`;
plus `APIConnectionError` and `APITimeoutError` when no answer came. A failed call costs nothing.

Calls that may pass later (`retryable`, a 429 with `retry_after`, 5xx, a lost connection) are retried
twice (`max_retries`), after `retry_after` when the API gives it; a `retry_after` over a minute (a
day's quota) is raised at once. Every run is sent with an `Idempotency-Key`, the same across its
retries: a retry is never charged twice. A POST without one (`feedback`, `accounts.connect`) is not
sent again after a lost connection. The client waits 180 s for an answer (`timeout`), and a run at
least its `timeout_s` plus 30 s.

## The CLI

```bash
lobstr run google_maps/places/search query="bakeries in Austin, TX" near.lat=30.27 near.lng=-97.74 --limit 5 --fields name,phone
lobstr run google_maps/reviews/list place=ChIJCar0f49ZwokR6ozLV-dHNTE --all --max-records 50 --jsonl
lobstr catalog maps
lobstr operation google_maps/places/search
lobstr balance
lobstr runs --limit 5
lobstr feedback "reviews/list misses the owner's answer" --operation google_maps/reviews/list
```

Options are `key=value`: the value is read as JSON when it is valid JSON (`5`, `true`, `["a","b"]`),
else as a string; dotted keys nest. The output is pretty JSON on a terminal and compact JSON when
piped; `--jsonl` prints the records only, one a line. `--all` follows every page and prints their
records as one array; `--max-records` stops it (and implies it). On a terminal, a line on stderr says
what the call cost. An API error prints its message and `doc_url` on stderr and exits with 1; a
mistake in the command line exits with 2.

## Agent frameworks

The tools of lobstr.io's [MCP server](https://github.com/lobstrio/lobstr-mcp), for an agent written
in Python: `lobstr_find_operations` (the operations that fit a need, with their price),
`lobstr_get_operation` (one's options and record fields) and `lobstr_run_operation` (one call, one
page; `next` to go on). `operations=[...]` adds a tool per operation, named
`google_maps_places_search`, its arguments the operation's options plus `fields` (and `limit` for a
search or a list). Each tool answers compact JSON; an API error comes back to the agent as text, with
its `doc_url`, not as an exception. The catalog is read once; a call costs its operation's credits.

LangChain (Python 3.11 or later):

```bash
pip install "lobstrio[langchain]" langchain langchain-anthropic
```

```python
from langchain.agents import create_agent
from lobstrio.langchain import LobstrToolkit

tools = LobstrToolkit(operations=["google_maps/places/search"]).get_tools()
agent = create_agent("anthropic:claude-sonnet-5-5", tools)
agent.invoke({"messages": [{"role": "user", "content": "Five dentists in Austin, TX, with their phone"}]})
```

CrewAI (Python 3.10 to 3.13):

```bash
pip install "lobstrio[crewai]"
```

```python
from crewai import Agent, Crew, Task
from lobstrio.crewai import lobstr_tools

analyst = Agent(
    role="News analyst",
    goal="Say what the news says about a brand",
    backstory="You read the news for a living.",
    tools=lobstr_tools(operations=["google_news/articles/search"]),
)
task = Task(
    description="What does the news say about Tesla this week?",
    expected_output="Five bullet points, each with its source",
    agent=analyst,
)
Crew(agents=[analyst], tasks=[task]).kickoff()
```

Both take `client=Lobstr(...)` for a key or settings of your own. More, with the OpenAI Agents SDK
too: [lobstrio/examples](https://github.com/lobstrio/examples).

## Links

- Docs: https://docs.lobstr.io?utm_source=lobstrio-python&utm_medium=readme
- Every operation: https://api.lobstr.io/v2/catalog.md
- MCP server: https://github.com/lobstrio/lobstr-mcp
- Changelog: [CHANGELOG.md](CHANGELOG.md)

Apache 2.0, LOBSTR SAS.
