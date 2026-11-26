"""The ``lobstr`` command: lobstr.io's API v2 from a terminal.

    lobstr run google_maps/places/search query="bakeries in Lyon" near.lat=45.76 --limit 5

The same grammar as the Node SDK's ``lobstr``. Output: pretty JSON on a terminal, compact JSON when
piped, JSON Lines with ``--jsonl``. Errors: the API's message and its doc_url on stderr, exit code 1;
a mistake in the command line, exit code 2.
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, TextIO

from ._client import Lobstr
from ._errors import LobstrError
from ._version import __version__

USAGE = f"""lobstr {__version__}: lobstr.io's API v2 from the command line.

Usage:
  lobstr run <source/resource/verb> [key=value ...] [--limit N] [--fields a,b] [--all] [--max-records N] [--jsonl]
  lobstr catalog [words ...] [--jsonl]
  lobstr operation <source/resource/verb>
  lobstr balance
  lobstr runs [--limit N] [--jsonl]
  lobstr feedback "<message>" [--operation op] [--run id]

Options are key=value: the value is read as JSON when it is valid JSON, else as a string; dotted keys nest
(near.lat=45.76). --all follows every page (each page is a call, at the operation's price), up to --max-records.
The API key comes from LOBSTR_API_KEY (https://app.lobstr.io/dashboard/api); the catalog needs none.

Examples:
  lobstr catalog google maps
  lobstr run google_maps/places/search query="bakeries in Lyon" --limit 5 --fields name,phone
  lobstr run google_maps/reviews/list place=ChIJc_459AHr9EcRd0ALc7vrjcI --all --max-records 100 --jsonl

Docs: https://docs.lobstr.io . Catalog: https://api.lobstr.io/v2/catalog.md
"""


class UsageError(Exception):
    """A mistake in the command line itself: exit code 2."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:  # argparse's own mistakes, as ours
        raise UsageError(message)


def parse_value(raw: str) -> Any:
    """A ``key=value``'s value: JSON when it is valid JSON (``5``, ``true``, ``["a"]``), else the
    string as it is."""

    def reject(constant: str) -> Any:
        raise ValueError(constant)

    try:
        return json.loads(raw, parse_constant=reject)
    except ValueError:
        return raw


def parse_options(pairs: Iterable[str]) -> Dict[str, Any]:
    """``key=value`` pairs to the options; dotted keys nest (``near.lat=45.76``)."""
    options: Dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        parts = key.split(".")
        if not sep or not key:
            raise UsageError(f'Options are key=value: got "{pair}".')
        if not all(parts):
            raise UsageError(f'An empty key in "{pair}".')
        target = options
        for part in parts[:-1]:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                raise UsageError(f'"{pair}": {part} is already a value, not an object.')
            target = child
        target[parts[-1]] = parse_value(raw)
    return options


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise argparse.ArgumentTypeError("takes a whole number, 1 or more")
    return number


def _plural(n: float, word: str) -> str:
    return f"{n:,g} {word}{'' if n == 1 else 's'}"


def _summary(records: int, credits: float, left: Optional[float], more: bool) -> str:
    parts = [_plural(records, "record"), _plural(credits, "credit")]
    if left is not None:
        parts.append(f"{_plural(left, 'credit')} left")
    return " · ".join(parts) + (" · more pages: --all" if more else "") + "\n"


def _credits(value: Any) -> Optional[float]:
    if isinstance(value, dict) and isinstance(value.get("credits"), (int, float)):
        return float(value["credits"])
    return None


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


class _Output:
    def __init__(self, out: TextIO, err: TextIO) -> None:
        self.out = out
        self.err = err
        self.pretty = _isatty(out)
        self.summaries = _isatty(err)

    def print(self, value: Any, jsonl: bool = False) -> None:
        if jsonl and isinstance(value, list):
            for item in value:
                self.line(item)
        elif jsonl or not self.pretty:
            self.line(value)
        else:
            self.out.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    def line(self, value: Any) -> None:
        self.out.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.out.flush()


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    return _Parser(prog=f"lobstr {prog}", description=description)


def cmd_run(client: Lobstr, argv: Sequence[str], io: _Output) -> int:
    parser = _parser(
        "run",
        "Run an operation. Options are key=value: the value is read as JSON when it is valid "
        'JSON (5, true, ["a","b"]), else as a string; dotted keys nest (near.lat=45.76).',
    )
    parser.add_argument("operation", help="the full source/resource/verb")
    parser.add_argument("options", nargs="*", metavar="key=value", help="the operation's options")
    parser.add_argument("--limit", type=_positive_int, help="only the first N records of a page")
    parser.add_argument("--fields", help="only these fields of each record, as name,phone")
    parser.add_argument("--all", action="store_true", help="every page (each page is a call)")
    parser.add_argument(
        "--max-records",
        type=_positive_int,
        metavar="N",
        help="stop after N records (implies --all)",
    )
    parser.add_argument("--jsonl", action="store_true", help="the records only, one a line")
    args = parser.parse_intermixed_args(argv)
    options = parse_options(args.options)
    fields = None
    if args.fields is not None:
        fields = [part.strip() for part in args.fields.split(",") if part.strip()]
    run: Any = client.run(args.operation, options, fields=fields, limit=args.limit)
    if not (args.all or args.max_records is not None):
        io.print(run.results if args.jsonl else run.to_dict(), args.jsonl)
        if io.summaries:
            credits, left = _credits(run.cost) or 0, _credits(run.balance)
            io.err.write(_summary(run.count, credits, left, bool(run.next)))
        return 0

    # Every page, up to --max-records: records as they come with --jsonl, else one array at the
    # end. A page that fails still leaves the records read before it written.
    records: List[Any] = []
    count, credits, left = 0, 0.0, None
    failure: Optional[LobstrError] = None
    try:
        while run is not None:
            credits += _credits(run.cost) or 0
            left = _credits(run.balance) if _credits(run.balance) is not None else left
            for record in run.results:
                if args.max_records is not None and count >= args.max_records:
                    break
                if args.jsonl:
                    io.line(record)
                else:
                    records.append(record)
                count += 1
            if args.max_records is not None and count >= args.max_records:
                break
            run = run.next_page()
    except LobstrError as exc:
        failure = exc
    if not args.jsonl:
        io.print(records)
    if io.summaries:
        io.err.write(_summary(count, credits, left, False))
    if failure is not None:
        raise failure
    return 0


def cmd_catalog(client: Lobstr, argv: Sequence[str], io: _Output) -> int:
    parser = _parser("catalog", "The operations, those matching every word.")
    parser.add_argument("words", nargs="*")
    parser.add_argument("--jsonl", action="store_true", help="one operation a line")
    args = parser.parse_args(argv)
    words = [word.lower() for word in args.words]
    matches = []
    for op in client.catalog.list().get("data") or []:
        site = op.get("site") or {}
        parts: List[Any] = [op.get("id"), op.get("title"), op.get("summary"), op.get("use_when")]
        parts.append(site.get("title") if isinstance(site, dict) else None)
        parts.extend(op.get("categories") or [])
        text = " ".join(str(part) for part in parts if part).lower()
        if all(word in text for word in words):
            price = op.get("price") or {}
            matches.append(
                {
                    "id": op.get("id"),
                    "title": op.get("title"),
                    "credits_per_call": price.get("credits_per_call"),
                    "summary": op.get("summary"),
                }
            )
    io.print(matches, args.jsonl)
    return 0


def cmd_operation(client: Lobstr, argv: Sequence[str], io: _Output) -> int:
    parser = _parser("operation", "An operation's contract: options, record, errors, price.")
    parser.add_argument("operation", help="the full source/resource/verb")
    args = parser.parse_args(argv)
    io.print(client.catalog.get(args.operation))
    return 0


def cmd_balance(client: Lobstr, argv: Sequence[str], io: _Output) -> int:
    _parser("balance", "Your credits, and your plan with the price of one credit.").parse_args(argv)
    io.print(client.balance())
    return 0


def cmd_runs(client: Lobstr, argv: Sequence[str], io: _Output) -> int:
    parser = _parser("runs", "Your last calls, newest first: what was sent and answered.")
    parser.add_argument("--limit", type=_positive_int, help="1 to 100 (20 by default)")
    parser.add_argument("--jsonl", action="store_true", help="one call a line")
    args = parser.parse_args(argv)
    io.print(client.runs.list(limit=args.limit).data, args.jsonl)
    return 0


def cmd_feedback(client: Lobstr, argv: Sequence[str], io: _Output) -> int:
    parser = _parser("feedback", "Tell lobstr.io's team what went wrong or what you need.")
    parser.add_argument("message", help="quote it")
    parser.add_argument("--operation", help="the operation concerned, as google_maps/reviews/list")
    parser.add_argument("--run", help="the id of the call concerned (run_...)")
    args = parser.parse_args(argv)
    io.print(client.feedback(args.message, operation=args.operation, run=args.run))
    return 0


COMMANDS = {
    "run": cmd_run,
    "catalog": cmd_catalog,
    "operation": cmd_operation,
    "balance": cmd_balance,
    "runs": cmd_runs,
    "feedback": cmd_feedback,
}


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    client: Optional[Lobstr] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    """Runs the ``lobstr`` command; returns its exit code."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    for stream in (out, err):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-V", "--version"):
        out.write(f"{__version__}\n")
        return 0
    if args and args[0] in ("-h", "--help", "help"):
        out.write(USAGE)
        return 0
    if not args:
        err.write(USAGE)
        return 2
    command = COMMANDS.get(args[0])
    if command is None:
        err.write(f'error: Unknown command "{args[0]}".\nRun "lobstr --help" for the commands.\n')
        return 2
    own_client = client is None
    try:
        if client is None:
            client = Lobstr()
        return command(client, args[1:], _Output(out, err))
    except UsageError as exc:
        err.write(f'error: {exc}\nRun "lobstr --help" for the commands.\n')
        return 2
    except LobstrError as exc:
        err.write(f"error: {exc.message}\n")
        if exc.param:
            err.write(f"param: {exc.param}\n")
        if exc.doc_url:
            err.write(f"{exc.doc_url}\n")
        return 1
    except (ValueError, TypeError) as exc:
        # An operation that is not source/resource/verb, and such: the command line's mistake.
        err.write(f'error: {exc}\nRun "lobstr --help" for the commands.\n')
        return 2
    except BrokenPipeError:
        # The reader went away (| head): stop, and fetch no more pages.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, out.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        if own_client and client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
