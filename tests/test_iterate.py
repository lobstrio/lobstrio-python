import asyncio

from _helpers import Mock, async_client, client, envelope, ok

OP = "google_maps/reviews/list"


def page(n: int, size: int = 3, last: bool = False):
    records = [{"object": "review", "id": f"r{n}-{i}", "position": i} for i in range(1, size + 1)]
    nxt = None
    if not last:
        nxt = {
            "source": "google_maps",
            "operation": "reviews/list",
            "options": {"place": "p", "cursor": f"c{n + 1}"},
        }
    return ok(envelope(OP, records, next=nxt, run_id=f"run_{n}"))


def ids(records):
    return [r["id"] for r in records]


def test_iterate_follows_next_until_it_is_null():
    mock = Mock(page(1), page(2), page(3, last=True))
    records = list(client(mock).iterate(OP, {"place": "p"}))
    assert ids(records) == [f"r{n}-{i}" for n in (1, 2, 3) for i in (1, 2, 3)]
    bodies = mock.bodies()
    assert bodies[0]["options"] == {"place": "p"}
    assert bodies[1]["options"]["cursor"] == "c2"
    assert bodies[2]["options"]["cursor"] == "c3"
    assert len(set(mock.keys())) == 3  # each page is its own call, with its own key


def test_iterate_is_lazy():
    mock = Mock(page(1, last=True))
    records = client(mock).iterate(OP, place="p")
    assert mock.requests == []
    assert ids(records) == ["r1-1", "r1-2", "r1-3"]


def test_max_records_stops_within_a_page_and_fetches_no_more():
    mock = Mock(page(1), page(2), page(3, last=True))
    records = list(client(mock).iterate(OP, {"place": "p"}, max_records=4))
    assert ids(records) == ["r1-1", "r1-2", "r1-3", "r2-1"]
    assert len(mock.requests) == 2


def test_max_pages_stops_after_that_many_calls():
    mock = Mock(page(1), page(2), page(3, last=True))
    records = list(client(mock).iterate(OP, {"place": "p"}, max_pages=2))
    assert len(records) == 6
    assert len(mock.requests) == 2


def test_caps_of_zero_make_no_call():
    mock = Mock()
    assert list(client(mock).iterate(OP, {"place": "p"}, max_records=0)) == []
    assert list(client(mock).iterate(OP, {"place": "p"}, max_pages=0)) == []
    assert mock.requests == []


def test_iterate_passes_fields_and_limit():
    mock = Mock(page(1, last=True))
    list(client(mock).iterate(OP, {"place": "p"}, fields=["rating"], limit=2))
    assert mock.bodies()[0]["fields"] == ["rating"]
    assert mock.bodies()[0]["limit"] == 2


def test_empty_pages_are_followed():
    empty = ok(
        envelope(
            OP,
            [],
            next={"source": "google_maps", "operation": "reviews/list", "options": {"cursor": "c"}},
        )
    )
    mock = Mock(empty, page(2, last=True))
    assert len(list(client(mock).iterate(OP, {"place": "p"}))) == 3


def test_async_iterate_with_caps():
    mock = Mock(page(1), page(2), page(3, last=True))

    async def main():
        c = async_client(mock)
        return [r async for r in c.iterate(OP, {"place": "p"}, max_records=5)]

    records = asyncio.run(main())
    assert ids(records) == ["r1-1", "r1-2", "r1-3", "r2-1", "r2-2"]
    assert len(mock.requests) == 2
