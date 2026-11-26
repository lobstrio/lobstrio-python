#!/usr/bin/env python3
"""Generates ``src/lobstrio/_generated.py`` from lobstr.io's OpenAPI.

    python scripts/generate.py                  # from https://api.lobstr.io/v2/openapi.json
    python scripts/generate.py path/to/openapi.json
    python scripts/generate.py --check          # exit 1 when the committed module is not current

The module holds, for each operation, a TypedDict of its options and of its record, and the typed
namespaces of both clients (``client.google_maps.places.search(...)``); plus the API's shared
schemas (``Account``, ``Balance``...). The output is deterministic, then formatted with ruff.
Standard library only, so the workflow that regenerates it needs nothing installed but ruff.
"""

import argparse
import json
import keyword
import re
import subprocess
import sys
import tempfile
import textwrap
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_URL = "https://api.lobstr.io/v2/openapi.json"
ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "src" / "lobstrio" / "_generated.py"

OPERATION_SCHEMA = re.compile(
    r"^([a-z0-9_]+)\.([a-z0-9_]+)\.([a-z0-9_]+)\.(Options|Record|Request|Answer)$"
)
# Shared schemas whose name would clash with the SDK's own classes.
RENAMED = {"Run": "RunLog", "RunList": "RunLogList", "ConnectAccount": "ConnectAccountParams"}
# Shared schemas left out: an error is raised as a LobstrError, not returned.
SKIPPED = {"Error"}
# JSON Schema keywords. Any other key with a null value is the tail of a description the OpenAPI
# cut at a comma ("description": "The title", "as the publisher wrote it.": null): joined back.
KEYWORDS = {
    "$ref", "$schema", "$id", "title", "description", "type", "enum", "const", "default",
    "examples", "example", "format", "pattern", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "minItems", "maxItems",
    "uniqueItems", "items", "properties", "required", "additionalProperties",
    "patternProperties", "oneOf", "anyOf", "allOf", "not", "nullable", "readOnly", "writeOnly",
    "deprecated",
}  # fmt: skip
# The run parameters every typed method takes beside the options.
# An operation that answers one record (its Request takes no `limit`) has no limit and no iterate.
CALL_PARAMS = ("fields", "limit", "timeout_s", "idempotency_key")
SINGLE_CALL_PARAMS = ("fields", "timeout_s", "idempotency_key")
ITERATE_PARAMS = ("fields", "limit", "timeout_s", "max_records", "max_pages")
TYPING_NAMES = (
    "TYPE_CHECKING",
    "Any",
    "AsyncIterator",
    "Dict",
    "Iterator",
    "List",
    "Literal",
    "Optional",
    "Sequence",
    "Tuple",
    "TypedDict",
    "Union",
    "cast",
)

CALL_DOCS = {
    "fields": "Only these fields of each record, by path (``name``, ``address.city``); "
    "``object``, ``id`` and ``position`` always come.",
    "limit": "Only the first 1 to 1,000 records of the page; the call costs the same.",
    "timeout_s": "The most seconds the API waits for the site, up to the operation's maximum.",
    "idempotency_key": "Sent as ``Idempotency-Key``; a UUID4 when left out, kept across retries.",
    "max_records": "Stop after this many records.",
    "max_pages": "Stop after this many pages (each page is one call, at the operation's price).",
}


def load(source: Optional[str]) -> Dict[str, Any]:
    if not source or source.startswith(("http://", "https://")):
        request = urllib.request.Request(
            source or DEFAULT_URL, headers={"User-Agent": "lobstrio-python-generator"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def pascal(text: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^0-9A-Za-z]+", text) if part)


def identifier(name: str) -> str:
    """A Python parameter name for an option."""
    clean = re.sub(r"\W", "_", name)
    if not clean or clean[0].isdigit():
        clean = "_" + clean
    if keyword.iskeyword(clean):
        clean += "_"
    return clean


def is_field_name(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def describe(schema: Any) -> str:
    """A schema's description, its pieces cut at commas joined back."""
    if not isinstance(schema, dict):
        return ""
    text = schema.get("description") if isinstance(schema.get("description"), str) else ""
    for key, value in schema.items():
        if value is None and key not in KEYWORDS:
            text = f"{text}, {key}" if text else key
    return one_line(text)


def one_line(text: Any) -> str:
    return " ".join(str(text).split()) if text else ""


def docstring(text: str, indent: int, width: int = 96) -> str:
    """A triple-quoted docstring, wrapped and escaped, its lines indented."""
    pad = " " * indent
    paragraphs = [p for p in text.strip().split("\n\n")]
    lines: List[str] = []
    for i, paragraph in enumerate(paragraphs):
        if i:
            lines.append("")
        for raw in paragraph.split("\n"):
            lead = len(raw) - len(raw.lstrip(" "))
            wrapped = textwrap.wrap(
                raw.strip(),
                width=max(40, width - indent - lead),
                break_long_words=False,
                break_on_hyphens=False,
            ) or [""]
            first, rest = wrapped[0], wrapped[1:]
            lines.append(" " * lead + first)
            hang = " " * (lead + 4 if lead else 0)
            lines.extend(hang + line for line in rest)
    body = "\n".join(lines).replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if body.endswith('"'):
        body = body[:-1] + '\\"'
    if "\n" not in body:
        return f'{pad}"""{body}"""'
    indented = "\n".join((pad + line) if line else "" for line in body.split("\n"))
    return f'{pad}"""{indented[indent:]}\n{pad}"""'


class Generator:
    def __init__(self, spec: Dict[str, Any]) -> None:
        self.spec = spec
        self.schemas: Dict[str, Any] = spec.get("components", {}).get("schemas", {})
        self.out: List[str] = []
        self.names: Set[str] = set()
        self.public: List[str] = []
        self.emitted: Dict[str, str] = {}  # component name -> Python name
        self.emitting: Set[str] = set()
        self.operations: Dict[str, Dict[str, str]] = {}
        self.fields_of: Dict[str, Dict[str, str]] = {}  # TypedDict -> its fields' annotations
        for name in sorted(self.schemas):
            match = OPERATION_SCHEMA.match(name)
            if match:
                source, resource, verb, _ = match.groups()
                self.operations.setdefault(f"{source}/{resource}/{verb}", {})
        for op in self.operations:
            prefix = op.replace("/", ".")
            for part in ("Options", "Record", "Request"):
                if f"{prefix}.{part}" not in self.schemas:
                    raise SystemExit(f"{op}: no {prefix}.{part} in the OpenAPI")

    # -- names

    def claim(self, name: str, *, public: bool = True) -> str:
        base, n = name, 2
        while name in self.names:
            name, n = f"{base}{n}", n + 1
        self.names.add(name)
        if public:
            self.public.append(name)
        return name

    def component_name(self, component: str) -> Optional[str]:
        """The Python name of a component schema the module defines, or None."""
        if component in SKIPPED:
            return None
        if component in self.schemas and not OPERATION_SCHEMA.match(component):
            return RENAMED.get(component, component)
        match = OPERATION_SCHEMA.match(component)
        if match and match.group(4) in ("Options", "Record"):
            source, resource, verb, part = match.groups()
            return pascal(source) + pascal(resource) + pascal(verb) + part
        return None

    # -- types

    def ref(self, schema: Dict[str, Any], hint: str, aliases: bool) -> str:
        component = schema["$ref"].rsplit("/", 1)[-1]
        if self.component_name(component) is None:
            return self.annotation(self.schemas.get(component, {}), hint, aliases)
        if component in self.emitted:
            return self.emitted[component]
        if component in self.emitting:
            return repr(self.component_name(component))
        return self.emit_component(component)

    def annotation(self, schema: Any, hint: str, aliases: bool = False) -> str:
        """The annotation for a schema; nested objects become TypedDicts named from ``hint``."""
        if not isinstance(schema, dict):
            return "Any"
        if "$ref" in schema:
            return self.ref(schema, hint, aliases)
        if "const" in schema:
            return "None" if schema["const"] is None else f"Literal[{schema['const']!r}]"
        if "enum" in schema:
            values = [v for v in schema["enum"] if v is not None]
            nullable = len(values) != len(schema["enum"]) or self.nullable(schema)
            if not values:
                return "None"
            literal = f"Literal[{', '.join(repr(v) for v in values)}]"
            if aliases and len(values) > 1:
                name = self.claim(hint)
                self.out.append(f"{name} = {literal}\n")
                literal = name
            return f"Optional[{literal}]" if nullable else literal
        for key in ("oneOf", "anyOf"):
            if key in schema:
                members = [m for m in schema[key] if not self.is_null(m)]
                nullable = len(members) != len(schema[key])
                if len(members) == 1:
                    parts = [self.annotation(members[0], hint, aliases)]
                else:
                    parts = [
                        self.annotation(m, f"{hint}Variant{i + 1}", aliases)
                        for i, m in enumerate(members)
                    ]
                return self.union(parts, nullable)
        if "allOf" in schema:
            members = schema["allOf"]
            return (
                self.annotation(members[0], hint, aliases)
                if len(members) == 1
                else "Dict[str, Any]"
            )
        types = schema.get("type")
        types = list(types) if isinstance(types, list) else ([types] if types else [])
        nullable = "null" in types
        types = [t for t in types if t != "null"]
        if not types:
            if "properties" in schema:
                types = ["object"]
            elif nullable:
                return "None"
            else:
                return "Any"
        if len(types) > 1:
            parts = [self.single(dict(schema, type=t), t, hint, aliases) for t in types]
            return self.union(parts, nullable)
        return self.union([self.single(schema, types[0], hint, aliases)], nullable)

    def single(self, schema: Dict[str, Any], type_: str, hint: str, aliases: bool) -> str:
        if type_ == "string":
            return "str"
        if type_ == "integer":
            return "int"
        if type_ == "number":
            return "float"
        if type_ == "boolean":
            return "bool"
        if type_ == "array":
            items = schema.get("items")
            return (
                f"List[{self.annotation(items, hint + 'Item', aliases)}]" if items else "List[Any]"
            )
        if type_ == "object":
            if isinstance(schema.get("properties"), dict) and schema["properties"]:
                return self.typeddict(hint, schema)
            extra = schema.get("additionalProperties")
            if isinstance(extra, dict) and extra:
                return f"Dict[str, {self.annotation(extra, hint + 'Value', aliases)}]"
            return "Dict[str, Any]"
        return "Any"

    @staticmethod
    def is_null(schema: Any) -> bool:
        return isinstance(schema, dict) and (
            schema.get("type") == "null" or schema.get("const", 0) is None
        )

    @staticmethod
    def nullable(schema: Dict[str, Any]) -> bool:
        types = schema.get("type")
        return isinstance(types, list) and "null" in types

    @staticmethod
    def union(parts: List[str], nullable: bool) -> str:
        unique: List[str] = []
        for part in parts:
            if part not in unique and part != "None":
                unique.append(part)
        nullable = nullable or "None" in parts
        if not unique:
            return "None"
        inner = unique[0] if len(unique) == 1 else f"Union[{', '.join(unique)}]"
        return f"Optional[{inner}]" if nullable else inner

    # -- TypedDicts

    def field_doc(self, schema: Any, with_hints: bool) -> str:
        if not isinstance(schema, dict):
            return ""
        text = describe(schema)
        if with_hints:
            extra = []
            if "default" in schema:
                extra.append(f"Default: {json.dumps(schema['default'], ensure_ascii=False)}.")
            if schema.get("examples"):
                examples = ", ".join(
                    json.dumps(e, ensure_ascii=False) for e in schema["examples"][:3]
                )
                extra.append(f"Examples: {examples}.")
            text = " ".join(p for p in [text, *extra] if p)
        return text

    def typeddict(
        self,
        name: str,
        schema: Dict[str, Any],
        doc: str = "",
        *,
        aliases: bool = False,
        claimed: bool = False,
    ) -> str:
        if not claimed:
            name = self.claim(name)
        properties = {
            key: value
            for key, value in (schema.get("properties") or {}).items()
            if isinstance(value, dict)
        }
        required = [key for key in properties if key in set(schema.get("required") or [])]
        optional = [key for key in properties if key not in required]
        # Nested types first: a TypedDict's annotations are read when its class is made.
        fields = {
            key: self.annotation(value, name + pascal(key), aliases)
            for key, value in properties.items()
        }
        self.fields_of[name] = fields
        docs = {key: self.field_doc(properties[key], aliases) for key in properties}
        doc = doc or describe(schema)
        lines: List[str] = []
        if all(is_field_name(key) for key in properties):

            def body(keys: List[str], class_doc: str) -> List[str]:
                out = [docstring(class_doc, 4)] if class_doc else []
                for key in keys:
                    out.append(f"    {key}: {fields[key]}")
                    if docs[key]:
                        out.append(docstring(docs[key], 4))
                return out or ["    pass"]

            if required and optional:
                base = self.claim(f"_{name}Required", public=False)
                lines.append(f"class {base}(TypedDict):")
                lines.extend(body(required, ""))
                lines.append("\n")
                lines.append(f"class {name}({base}, total=False):")
                lines.extend(body(optional, doc))
            elif required:
                lines.append(f"class {name}(TypedDict):")
                lines.extend(body(required, doc))
            else:
                lines.append(f"class {name}(TypedDict, total=False):")
                lines.extend(body(optional, doc))
        else:
            # Keys that are not identifiers ("1", "from"): the functional form.
            bases = []
            for keys, total, suffix in (
                (required, True, "Required"),
                (optional, False, "Optional"),
            ):
                if not keys:
                    continue
                base = self.claim(f"_{name}{suffix}", public=False)
                bases.append(base)
                entries = []
                for key in keys:
                    comment = f"  # {docs[key]}" if docs[key] else ""
                    entries.append(f"        {key!r}: {fields[key]},{comment}")
                lines.append(f"{base} = TypedDict(")
                lines.append(f"    {base!r},")
                lines.append("    {")
                lines.extend(entries)
                lines.append("    },")
                if not total:
                    lines.append("    total=False,")
                lines.append(")")
            lines.append(f"class {name}({', '.join(bases) or 'TypedDict'}):")
            lines.append(docstring(doc, 4) if doc else "    pass")
        self.out.append("\n".join(lines) + "\n")
        return name

    def emit_component(self, component: str) -> str:
        name = self.component_name(component)
        assert name is not None
        self.emitting.add(component)
        schema = self.schemas[component]
        aliases = component.endswith(".Options")
        doc = ""
        match = OPERATION_SCHEMA.match(component)
        if match:
            op = "/".join(match.groups()[:3])
            if match.group(4) == "Options":
                doc = f"The options of ``{op}``."
            else:
                doc = f"A record of ``{op}``. With ``fields``, only the fields asked come."
        name = self.claim(name)
        self.typeddict(name, schema, doc, aliases=aliases, claimed=True)
        self.emitting.discard(component)
        self.emitted[component] = name
        return name

    # -- namespaces

    def operation_info(self, op: str) -> Dict[str, Any]:
        source, resource, verb = op.split("/")
        prefix = op.replace("/", ".")
        base = pascal(source) + pascal(resource) + pascal(verb)
        options = self.schemas[f"{prefix}.Options"]
        request = self.schemas[f"{prefix}.Request"]
        properties = {
            k: v for k, v in (options.get("properties") or {}).items() if isinstance(v, dict)
        }
        required = set(options.get("required") or [])
        option_types = self.fields_of[self.emitted[f"{prefix}.Options"]]
        params = []
        for key, schema in properties.items():
            params.append(
                {
                    "option": key,
                    "param": identifier(key),
                    "required": key in required,
                    "schema": schema,
                    "annotation": option_types[key],
                    "doc": self.field_doc(schema, True),
                }
            )
        params.sort(key=lambda p: not p["required"])  # required first, then the OpenAPI's order
        paged = "limit" in (request.get("properties") or {})
        return {
            "op": op,
            "source": source,
            "resource": resource,
            "verb": verb,
            "base": base,
            "record": self.emitted[f"{prefix}.Record"],
            "summary": describe(request) or f"Runs {op}.",
            "params": params,
            "paged": paged,
        }

    def signature(
        self, params: List[Dict[str, Any]], reserved: Tuple[str, ...]
    ) -> List[Dict[str, Any]]:
        out = []
        for p in params:
            p = dict(p)
            if p["param"] in reserved:
                p["param"] = f"{p['param']}_option"
            out.append(p)
        return out

    def param_lines(self, params: List[Dict[str, Any]], extra: Tuple[str, ...]) -> List[str]:
        lines = ["        self,", "        *,"]
        for p in params:
            annotation = p["annotation"]
            if p["required"]:
                lines.append(f"        {p['param']}: {annotation},")
            else:
                if not annotation.startswith("Optional["):
                    annotation = f"Optional[{annotation}]"
                lines.append(f"        {p['param']}: {annotation} = None,")
        types = {
            "fields": "_Fields",
            "limit": "Optional[int]",
            "timeout_s": "Optional[int]",
            "idempotency_key": "Optional[str]",
            "max_records": "Optional[int]",
            "max_pages": "Optional[int]",
        }
        for name in extra:
            lines.append(f"        {name}: {types[name]} = None,")
        return lines

    @staticmethod
    def options_dict(params: List[Dict[str, Any]]) -> str:
        if not params:
            return "{}"
        return "{" + ", ".join(f"{p['option']!r}: {p['param']}" for p in params) + "}"

    def args_doc(self, params: List[Dict[str, Any]], extra: Tuple[str, ...]) -> str:
        lines = ["Args:"]
        for p in params:
            text = p["doc"] or "See the operation's contract."
            if p["param"] != p["option"]:
                text = f"The option ``{p['option']}``. {text}"
            lines.append(f"    {p['param']}: {text}")
        for name in extra:
            lines.append(f"    {name}: {CALL_DOCS[name]}")
        return "\n".join(lines)

    def operation_classes(self, info: Dict[str, Any], is_async: bool) -> str:
        op, record = info["op"], info["record"]
        run_type = "AsyncRun" if is_async else "Run"
        client = "AsyncLobstr" if is_async else "Lobstr"
        name = self.claim(f"_{'Async' if is_async else ''}{info['base']}Operation", public=False)
        info.setdefault("classes", {})[is_async] = name
        extras = CALL_PARAMS if info["paged"] else SINGLE_CALL_PARAMS
        call_params = self.signature(info["params"], extras)
        lines = [f"class {name}:", docstring(f"``{op}``: {info['summary']}", 4), ""]
        lines.append(f"    operation = {op!r}")
        lines.append("")
        lines.append(f'    def __init__(self, client: "{client}") -> None:')
        lines.append("        self._client = client")
        lines.append("")
        returns = f'"{run_type}[{record}]"'
        lines.append(f"    {'async ' if is_async else ''}def __call__(")
        lines.extend(self.param_lines(call_params, extras))
        lines.append(f"    ) -> {returns}:")
        doc = "\n\n".join(
            [
                info["summary"],
                self.args_doc(call_params, extras),
                (
                    f"Returns:\n    The answer, its records (``{record}``) in ``results``. "
                    + ("``await run.next_page()``" if is_async else "``run.next_page()``")
                    + " gives the following page; ``.iterate(...)`` every record."
                    if info["paged"]
                    else f"Returns:\n    The answer, its record (``{record}``) in ``results``."
                ),
                f"The contract: https://api.lobstr.io/v2/catalog/{op}",
            ]
        )
        lines.append(docstring(doc, 8))
        lines.append(f"        run = {'await ' if is_async else ''}self._client.run(")
        lines.append(f"            {op!r},")
        lines.append(f"            _options({self.options_dict(call_params)}),")
        lines.append("            fields=fields,")
        if info["paged"]:
            lines.append("            limit=limit,")
        lines.append("            timeout_s=timeout_s,")
        lines.append("            idempotency_key=idempotency_key,")
        lines.append("        )")
        lines.append(f"        return cast({returns}, run)")
        if info["paged"]:
            iterate_params = self.signature(info["params"], ITERATE_PARAMS)
            iterator = f'"{"AsyncIterator" if is_async else "Iterator"}[{record}]"'
            lines.append("")
            lines.append("    def iterate(")
            lines.extend(self.param_lines(iterate_params, ITERATE_PARAMS))
            lines.append(f"    ) -> {iterator}:")
            doc = "\n\n".join(
                [
                    f"Every page's records of ``{op}``"
                    + (" (``async for``)" if is_async else "")
                    + ", following ``next`` until it is null or a cap is hit.",
                    self.args_doc(iterate_params, ITERATE_PARAMS),
                ]
            )
            lines.append(docstring(doc, 8))
            lines.append("        records = self._client.iterate(")
            lines.append(f"            {op!r},")
            lines.append(f"            _options({self.options_dict(iterate_params)}),")
            lines.append("            max_records=max_records,")
            lines.append("            max_pages=max_pages,")
            lines.append("            fields=fields,")
            lines.append("            limit=limit,")
            lines.append("            timeout_s=timeout_s,")
            lines.append("        )")
            lines.append(f"        return cast({iterator}, records)")
        return "\n".join(lines) + "\n"

    def namespaces(self, infos: List[Dict[str, Any]], is_async: bool) -> None:
        prefix = "Async" if is_async else ""
        client = "AsyncLobstr" if is_async else "Lobstr"
        for info in infos:
            self.out.append(self.operation_classes(info, is_async))
        tree: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for info in infos:
            tree.setdefault(info["source"], {}).setdefault(info["resource"], []).append(info)
        source_classes: Dict[str, str] = {}
        for source, resources in tree.items():
            resource_classes: Dict[str, str] = {}
            for resource, ops in resources.items():
                name = self.claim(
                    f"_{prefix}{pascal(source)}{pascal(resource)}Resource", public=False
                )
                resource_classes[resource] = name
                lines = [f"class {name}:", f'    """``{source}/{resource}/...``"""', ""]
                for info in ops:
                    lines.append(f"    {info['verb']}: {info['classes'][is_async]}")
                lines.append("")
                lines.append(f'    def __init__(self, client: "{client}") -> None:')
                for info in ops:
                    lines.append(
                        f"        self.{info['verb']} = {info['classes'][is_async]}(client)"
                    )
                self.out.append("\n".join(lines) + "\n")
            name = self.claim(f"_{prefix}{pascal(source)}Source", public=False)
            source_classes[source] = name
            lines = [f"class {name}:", f'    """The operations on ``{source}``."""', ""]
            for resource, cls in resource_classes.items():
                lines.append(f"    {resource}: {cls}")
            lines.append("")
            lines.append(f'    def __init__(self, client: "{client}") -> None:')
            for resource, cls in resource_classes.items():
                lines.append(f"        self.{resource} = {cls}(client)")
            self.out.append("\n".join(lines) + "\n")
        name = self.claim("AsyncOperations" if is_async else "SyncOperations", public=False)
        lines = [
            f"class {name}:",
            f'    """The typed operations of ``{client}``, one attribute per source."""',
            "",
        ]
        for source, cls in source_classes.items():
            lines.append(f"    {source}: {cls}")
        lines.append("")
        lines.append("    def _init_operations(self) -> None:")
        lines.append(f'        client = cast("{client}", self)')
        for source, cls in source_classes.items():
            lines.append(f"        self.{source} = {cls}(client)")
        self.out.append("\n".join(lines) + "\n")

    # -- the module

    def generate(self) -> str:
        reserved = {
            "run", "send", "iterate", "run_many", "catalog", "accounts", "runs", "balance",
            "feedback", "close", "base_url", "timeout", "max_retries",
        }  # fmt: skip
        sources = sorted({op.split("/")[0] for op in self.operations})
        clash = [s for s in sources if s in reserved or not is_field_name(s)]
        if clash:
            raise SystemExit(f"sources whose name clashes with the client: {clash}")

        self.out.append("# -- Shared schemas\n")
        for component in sorted(self.schemas):
            if (
                not OPERATION_SCHEMA.match(component)
                and component not in SKIPPED
                and component not in self.emitted
            ):
                self.emit_component(component)
        feedback = (
            self.spec.get("paths", {})
            .get("/v2/feedback", {})
            .get("post", {})
            .get("responses", {})
            .get("201", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        if isinstance(feedback, dict) and feedback.get("properties"):
            self.typeddict("Feedback", feedback, "A message received by ``POST /v2/feedback``.")

        for op in sorted(self.operations):
            prefix = op.replace("/", ".")
            self.out.append(f"# -- {op}\n")
            self.emit_component(f"{prefix}.Options")
            self.emit_component(f"{prefix}.Record")

        infos = [self.operation_info(op) for op in sorted(self.operations)]
        self.out.append("# -- Lobstr's typed operations\n")
        self.namespaces(infos, is_async=False)
        self.out.append("# -- AsyncLobstr's typed operations\n")
        self.namespaces(infos, is_async=True)

        body = "\n\n".join(self.out)
        info = self.spec.get("info", {})
        operations = ",\n".join(f"    {op!r}" for op in sorted(self.operations))
        exports = sorted(self.public) + ["OPERATIONS"]
        used = [name for name in TYPING_NAMES if re.search(rf"\b{name}\b", body)]
        used = set(used) | {"TYPE_CHECKING", "Any", "Dict", "Sequence", "Tuple", "Union", "cast"}
        # isort's order: constants, then classes, then functions.
        used = sorted(used, key=lambda n: (0 if n.isupper() else 1 if n[0].isupper() else 2, n))
        header = "\n".join(
            [
                "# Generated by scripts/generate.py from lobstr.io's OpenAPI"
                f" ({one_line(info.get('title'))}, version {one_line(info.get('version'))}).",
                "# Do not edit it by hand: run `python scripts/generate.py` to regenerate it.",
                '"""The operations of lobstr.io\'s API v2: their options, their records, their methods."""',
                "",
                f"from typing import {', '.join(used)}",
                "",
                "from ._run import AsyncRun, Run",
                "",
                "if TYPE_CHECKING:",
                "    from ._client import AsyncLobstr, Lobstr",
                "",
                "__all__ = [",
                *[f"    {name!r}," for name in exports],
                "]",
                "",
                f"#: Every operation this module knows ({len(self.operations)}), as ``source/resource/verb``.",
                f"OPERATIONS: Tuple[str, ...] = (\n{operations},\n)",
                "",
                "_Fields = Union[str, Sequence[str], None]",
                "",
                "",
                "def _options(values: Dict[str, Any]) -> Dict[str, Any]:",
                '    """The options given, those left as None out."""',
                "    return {key: value for key, value in values.items() if value is not None}",
                "",
                "",
            ]
        )
        return header + "\n" + body


def ruff_format(path: Path) -> None:
    for command in ([sys.executable, "-m", "ruff"], ["ruff"]):
        try:
            subprocess.run(
                [*command, "format", "--quiet", str(path)], check=True, capture_output=True
            )
            return
        except (OSError, subprocess.CalledProcessError):
            continue
    print("warning: ruff is not installed, the module is left unformatted", file=sys.stderr)


def render(spec: Dict[str, Any]) -> str:
    source = Generator(spec).generate()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "_generated.py"
        path.write_text(source, encoding="utf-8")
        ruff_format(path)
        return path.read_text(encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("openapi", nargs="?", help=f"a path or URL (default: {DEFAULT_URL})")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="exit 1 when the output would change")
    args = parser.parse_args(argv)
    spec = load(args.openapi)
    text = render(spec)
    current = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    if args.check:
        if current != text:
            print(f"{args.output} is not current: run python scripts/generate.py", file=sys.stderr)
            return 1
        return 0
    if current == text:
        print(f"{args.output}: unchanged")
        return 0
    args.output.write_text(text, encoding="utf-8")
    operations = len(Generator(spec).operations)
    print(f"{args.output}: written ({operations} operations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
