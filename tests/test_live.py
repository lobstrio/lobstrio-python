"""The live smoke test: real calls, skipped unless LOBSTR_API_KEY is set.

The run costs 1 credit (google_news/articles/search, 1 credit a call; an empty search is free).
The other calls are free.
"""

import os

import pytest

from lobstrio import Lobstr, Run

pytestmark = pytest.mark.skipif(
    not os.environ.get("LOBSTR_API_KEY"), reason="LOBSTR_API_KEY is not set"
)


@pytest.fixture(scope="module")
def client():
    with Lobstr() as c:
        yield c


def test_catalog(client):
    operations = client.catalog.list()
    assert any(op["id"] == "google_news/articles/search" for op in operations)


def test_balance(client):
    balance = client.balance()
    assert balance["object"] == "balance"
    assert isinstance(balance["credits"], (int, float))


def test_run(client):
    run = client.run("google_news/articles/search", {"query": "lobstr.io"}, limit=1)
    assert isinstance(run, Run)
    assert run.status == "succeeded"
    assert run.source == "google_news" and run.operation == "articles/search"
    assert run.count == len(run.results) <= 1
    assert run.cost["credits"] <= 1
    assert run.next is None  # Google News answers its whole search in one call
    kept = client.runs.get(run.id)
    assert kept["id"] == run.id and kept["status"] == "succeeded"
