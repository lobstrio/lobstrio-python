import io
import json

import pytest

from _helpers import API_KEY, Mock, client, envelope, error, ok
from lobstrio import __version__
from lobstrio._cli import UsageError, main, parse_options, parse_value

OP = "google_news/articles/search"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    "raw, value",
    [
        ("5", 5),
        ("45.76", 45.76),
        ("true", True),
        ("null", None),
        ('"75001"', "75001"),
        ('["a","b"]', ["a", "b"]),
        ('{"lat": 1}', {"lat": 1}),
        ("bakeries in Lyon", "bakeries in Lyon"),
        ("ChIJc_459AHr9EcRd0ALc7vrjcI", "ChIJc_459AHr9EcRd0ALc7vrjcI"),
        ("NaN", "NaN"),
        ("", ""),
        ("https://x.com/nasa?a=b", "https://x.com/nasa?a=b"),
    ],
)
def test_values_are_json_when_valid_json(raw, value):
    assert parse_value(raw) == value


def test_dotted_keys_nest():
    assert parse_options(
        ["query=bakeries in Lyon", "near.lat=45.76", "near.lng=4.83", "a.b.c=x=y"]
    ) == {
        "query": "bakeries in Lyon",
        "near": {"lat": 45.76, "lng": 4.83},
        "a": {"b": {"c": "x=y"}},
    }
    assert parse_options(["a=1", "a=2"]) == {"a": 2}


@pytest.mark.parametrize("pairs", [["query"], ["=x"], ["near.=1"], ["near=1", "near.lat=2"]])
def test_bad_options(pairs):
    with pytest.raises(UsageError):
        parse_options(pairs)


def cli(mock, *argv, out=None, err=None, **kwargs):
    out = out if out is not None else io.StringIO()
    err = err if err is not None else io.StringIO()
    code = main(list(argv), client=client(mock, **kwargs), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_run_prints_compact_json_when_piped():
    mock = Mock(ok(envelope()))
    code, out, err = cli(
        mock, "run", OP, "query=lobstr.io", "--limit", "1", "--fields", "title,url"
    )
    assert code == 0 and err == ""
    assert out.count("\n") == 1 and json.loads(out)["id"] == "run_1"
    assert mock.bodies()[0] == {
        "source": "google_news",
        "operation": "articles/search",
        "options": {"query": "lobstr.io"},
        "fields": ["title", "url"],
        "limit": 1,
    }


def test_run_on_a_terminal_prints_pretty_json_and_a_summary():
    mock = Mock(ok(envelope(credits=1)))
    code, out, err = cli(mock, "run", OP, "query=lobstr.io", out=Terminal(), err=Terminal())
    assert code == 0
    assert out.startswith("{\n  ")
    assert err == "1 record · 1 credit · 99 credits left\n"


def test_flags_and_options_mix_in_any_order():
    mock = Mock(ok(envelope("google_maps/places/search")))
    code, _, _ = cli(
        mock,
        "run",
        "--limit",
        "5",
        "google_maps/places/search",
        "query=bakery",
        "near.lat=45.76",
        "near.lng=4.83",
    )
    assert code == 0
    assert mock.bodies()[0]["options"] == {"query": "bakery", "near": {"lat": 45.76, "lng": 4.83}}


def test_jsonl_prints_the_records_only():
    records = [{"object": "article", "id": f"a{i}", "position": i} for i in (1, 2)]
    mock = Mock(ok(envelope(results=records)))
    code, out, _ = cli(mock, "run", OP, "query=x", "--jsonl")
    assert code == 0
    assert [json.loads(line) for line in out.splitlines()] == records


REVIEWS = "google_maps/reviews/list"
NEXT = {"source": "google_maps", "operation": "reviews/list", "options": {"cursor": "c"}}


def test_all_prints_an_array_of_every_page_s_records():
    mock = Mock(
        ok(envelope(REVIEWS, [{"id": "r1"}, {"id": "r2"}], next=NEXT)),
        ok(envelope(REVIEWS, [{"id": "r3"}])),
    )
    code, out, err = cli(mock, "run", REVIEWS, "place=p", "--all", err=Terminal())
    assert code == 0
    assert [r["id"] for r in json.loads(out)] == ["r1", "r2", "r3"]
    assert err == "3 records · 2 credits · 99 credits left\n"


def test_max_records_implies_all_and_stops_early():
    mock = Mock(
        ok(envelope(REVIEWS, [{"id": "r1"}, {"id": "r2"}], next=NEXT)),
        ok(envelope(REVIEWS, [{"id": "r3"}, {"id": "r4"}], next=NEXT)),
    )
    code, out, _ = cli(mock, "run", REVIEWS, "place=p", "--max-records", "3", "--jsonl")
    assert code == 0
    assert [json.loads(line)["id"] for line in out.splitlines()] == ["r1", "r2", "r3"]
    assert len(mock.requests) == 2


def test_all_still_prints_the_records_read_when_a_page_fails():
    mock = Mock(
        ok(envelope(REVIEWS, [{"id": "r1"}], next=NEXT)),
        error(422, "invalid_cursor", "invalid_request_error"),
    )
    code, out, err = cli(mock, "run", REVIEWS, "place=p", "--all")
    assert code == 1
    assert [r["id"] for r in json.loads(out)] == ["r1"]
    assert "The message of invalid_cursor." in err


def test_an_error_prints_the_message_and_doc_url_and_exits_1():
    mock = Mock(error(422, "invalid_query", "invalid_request_error", param="options.query"))
    code, out, err = cli(mock, "run", OP, "query=")
    assert code == 1 and out == ""
    assert err == (
        "error: The message of invalid_query.\n"
        "param: options.query\n"
        "https://docs.lobstr.io/errors#invalid_query\n"
    )
    assert API_KEY not in err + out


@pytest.mark.parametrize(
    "argv",
    [
        ["run"],
        ["run", OP, "query"],
        ["run", OP, "--limit", "0"],
        ["run", OP, "--nope"],
        ["run", "google_news"],
        ["balance", "extra"],
        ["scrape"],
    ],
)
def test_usage_errors_exit_2(argv):
    mock = Mock()
    code, out, err = cli(mock, *argv)
    assert code == 2 and out == ""
    assert err.startswith("error: ")
    assert mock.requests == []


def test_a_missing_key_says_where_to_set_it(monkeypatch):
    monkeypatch.delenv("LOBSTR_API_KEY", raising=False)
    code, _, err = cli(Mock(), "balance", api_key=None)
    assert code == 1
    assert "LOBSTR_API_KEY" in err and "https://docs.lobstr.io/errors#missing_api_key" in err


def test_catalog_prints_matching_operations_in_short():
    catalog = {
        "object": "list",
        "data": [
            {
                "id": "google_maps/reviews/list",
                "title": "List reviews",
                "summary": "A place's reviews",
                "site": {"title": "Google Maps"},
                "price": {"credits_per_call": 4},
                "use_when": "long",
            },
            {
                "id": "google_maps/places/search",
                "title": "Search places",
                "summary": "Places",
                "price": {"credits_per_call": 20},
            },
            {
                "id": "x/tweets/search",
                "title": "Search tweets",
                "summary": "Tweets",
                "price": {"credits_per_call": 20},
            },
        ],
        "has_more": False,
    }
    mock = Mock(ok(catalog), ok(catalog))
    code, out, _ = cli(mock, "catalog", "google", "MAPS", "reviews")
    assert code == 0
    assert json.loads(out) == [
        {
            "id": "google_maps/reviews/list",
            "title": "List reviews",
            "credits_per_call": 4,
            "summary": "A place's reviews",
        }
    ]
    code, out, _ = cli(mock, "catalog", "search", "--jsonl")
    assert [json.loads(line)["id"] for line in out.splitlines()] == [
        "google_maps/places/search",
        "x/tweets/search",
    ]
    assert "authorization" not in mock.requests[0].headers


def test_operation_balance_runs_feedback():
    mock = Mock(
        ok({"id": OP}),
        ok({"object": "balance", "credits": 9}),
        ok({"object": "list", "data": [{"id": "run_1"}], "has_more": False, "next": None}),
        ok({"object": "feedback", "id": "fbk_1", "created_at": "x", "kind": "other"}),
    )
    assert json.loads(cli(mock, "operation", OP)[1])["id"] == OP
    assert json.loads(cli(mock, "balance")[1])["credits"] == 9
    assert json.loads(cli(mock, "runs", "--limit", "1")[1]) == [{"id": "run_1"}]
    assert (
        json.loads(cli(mock, "feedback", "it broke", "--operation", OP, "--run", "run_1")[1])["id"]
        == "fbk_1"
    )
    assert str(mock.requests[2].url).endswith("/v2/runs?limit=1")
    assert json.loads(mock.requests[3].content) == {
        "message": "it broke",
        "operation": OP,
        "run": "run_1",
    }


def test_help_and_version():
    assert cli(Mock(), "--version")[1] == f"{__version__}\n"
    code, out, _ = cli(Mock(), "--help")
    assert code == 0 and "lobstr run <source/resource/verb>" in out
    out, err = io.StringIO(), io.StringIO()
    assert main([], client=client(Mock()), stdout=out, stderr=err) == 2
    assert "Usage:" in err.getvalue()
