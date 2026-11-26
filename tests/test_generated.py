import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

import lobstrio
import lobstrio._generated
from _helpers import Mock, async_client, client, envelope, ok
from lobstrio import types

ROOT = Path(__file__).resolve().parent.parent


def load_generator():
    spec = importlib.util.spec_from_file_location("generate", ROOT / "scripts" / "generate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate"] = module
    spec.loader.exec_module(module)
    return module


def test_every_operation_is_a_method_on_both_clients():
    sync, asyn = client(Mock()), async_client(Mock())
    assert len(lobstrio.OPERATIONS) == len(set(lobstrio.OPERATIONS)) >= 40
    assert list(lobstrio.OPERATIONS) == sorted(lobstrio.OPERATIONS)
    for op in lobstrio.OPERATIONS:
        source, resource, verb = op.split("/")
        method = getattr(getattr(getattr(sync, source), resource), verb)
        assert method.operation == op
        assert callable(method)
        amethod = getattr(getattr(getattr(asyn, source), resource), verb)
        assert inspect.iscoroutinefunction(amethod.__call__)
        # An operation that answers one record takes no limit, and has no iterate.
        assert hasattr(method, "iterate") == ("limit" in inspect.signature(method).parameters)


def test_a_typed_method_sends_what_run_sends():
    mock = Mock(ok(envelope()), ok(envelope()))
    c = client(mock)
    c.google_news.articles.search(query="lobstr.io", when="7d", limit=1, idempotency_key="k")
    c.run(
        "google_news/articles/search",
        {"query": "lobstr.io", "when": "7d"},
        limit=1,
        idempotency_key="k",
    )
    assert (
        mock.bodies()[0]
        == mock.bodies()[1]
        == {
            "source": "google_news",
            "operation": "articles/search",
            "options": {"query": "lobstr.io", "when": "7d"},
            "limit": 1,
        }
    )


def test_typed_signatures_follow_the_openapi():
    c = client(Mock())
    params = inspect.signature(c.google_maps.places.search).parameters
    assert params["query"].default is inspect.Parameter.empty
    assert params["near"].default is None
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    assert {"fields", "limit", "timeout_s", "idempotency_key"} <= set(params)
    # A get takes no limit and no iterate; websites/contacts/get's own max_pages stays an option.
    assert not hasattr(c.websites.contacts.get, "iterate")
    assert "max_pages" in inspect.signature(c.websites.contacts.get).parameters
    assert "limit" not in inspect.signature(c.google_maps.places.get).parameters
    assert "limit" in inspect.signature(c.google_maps.reviews.list).parameters
    assert "google_maps/places/search" in (c.google_maps.places.search.__call__.__doc__ or "")


def test_typed_iterate():
    nxt = {
        "source": "google_maps",
        "operation": "reviews/list",
        "options": {"place": "p", "cursor": "c"},
    }
    mock = Mock(
        ok(envelope("google_maps/reviews/list", next=nxt)), ok(envelope("google_maps/reviews/list"))
    )
    records = list(client(mock).google_maps.reviews.list.iterate(place="p", max_pages=5))
    assert len(records) == 2
    assert mock.bodies()[0]["options"] == {"place": "p"}


def test_async_typed_method():
    mock = Mock(ok(envelope()))

    async def main():
        c = async_client(mock)
        return await c.google_news.articles.search(query="x")

    assert asyncio.run(main()).id == "run_1"


def test_typed_dicts():
    assert types.GoogleNewsArticlesSearchOptions.__required_keys__ == frozenset({"query"})
    assert "edition" in types.GoogleNewsArticlesSearchOptions.__optional_keys__
    assert "title" in types.GoogleNewsArticlesSearchRecord.__required_keys__
    assert "from" in types.RealtorListingsSearchRecordPrice.__required_keys__
    assert "1" in types.GoogleMapsPlacesSearchRecordRatingDistribution.__required_keys__
    assert types.Account.__required_keys__ >= {"object", "id", "site", "name", "status"}
    for name in (
        "Balance",
        "RunLog",
        "RunLogList",
        "ConnectAccountParams",
        "DeletedAccount",
        "RunRequest",
        "Feedback",
        "CatalogList",
    ):
        assert name in types.__all__
    assert "ErrorBody" not in types.__all__ and "RunSummary" not in types.__all__
    # Descriptions the OpenAPI cut at a comma are joined back.
    source = Path(lobstrio._generated.__file__).read_text(encoding="utf-8")
    assert '"""The title, as the publisher wrote it."""' in source


MINI = {
    "openapi": "3.1.0",
    "info": {"title": "test", "version": "2"},
    "paths": {},
    "components": {
        "schemas": {
            "Thing": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
            "demo.items.search.Options": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "The words", "as typed.": None},
                    "limit": {"type": "integer"},
                    "max_pages": {"type": "integer"},
                    "from": {"type": "string"},
                    "sort": {"enum": ["new", "old"], "default": "new"},
                },
            },
            "demo.items.search.Request": {
                "type": "object",
                "description": "Searches items.",
                "properties": {"limit": {"type": "integer"}},
            },
            "demo.items.search.Record": {
                "type": "object",
                "required": ["id", "class"],
                "properties": {
                    "id": {"type": "string"},
                    "class": {"type": ["string", "null"]},
                    "tags": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"name": {"type": "string"}}},
                    },
                    "thing": {"$ref": "#/components/schemas/Thing"},
                },
            },
            "demo.items.search.Answer": {"type": "object"},
        }
    },
}


def test_the_generator_is_deterministic_and_renames_clashes():
    generate = load_generator()
    first = generate.Generator(MINI).generate()
    assert first == generate.Generator(MINI).generate()
    namespace = {}
    code = first.replace(
        "from ._run import AsyncRun, Run", "from lobstrio._run import AsyncRun, Run"
    )
    exec(compile(code, "<generated>", "exec"), namespace)
    call = inspect.signature(namespace["_DemoItemsSearchOperation"].__call__).parameters
    assert {"limit_option", "from_", "max_pages", "limit"} <= set(call)
    iterate = inspect.signature(namespace["_DemoItemsSearchOperation"].iterate).parameters
    assert {"max_pages_option", "max_pages", "limit_option"} <= set(iterate)
    assert namespace["DemoItemsSearchRecord"].__required_keys__ == frozenset({"id", "class"})
    assert namespace["OPERATIONS"] == ("demo/items/search",)
    assert "'limit': limit_option" in first and "'from': from_" in first
    assert "query: The words, as typed." in first


@pytest.mark.parametrize("text", ['ends with "', 'has """ inside', "back\\slash"])
def test_docstrings_are_escaped(text):
    generate = load_generator()
    literal = generate.docstring(text, 0)
    assert eval(literal) == text  # noqa: S307
