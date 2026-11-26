"""After the daily regeneration changed the generated types: the next version, written where the package keeps it,
and its CHANGELOG entry. A minor version when operations were added (new typed methods), a patch otherwise (options,
fields, descriptions). Prints the new version; the workflow commits, tags and pushes, and the tag publishes.

    python3 scripts/release_after_regenerate.py
"""

import datetime
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
OPERATION = re.compile(r"""["']([a-z0-9_]+/[a-z0-9_]+/[a-z0-9_]+)["']""")

# Where each SDK keeps its generated types, its version and its CHANGELOG's style.
PYTHON = {
    "generated": "src/lobstrio/_generated.py",
    "style": "python",
    "repo": "lobstrio/lobstrio-python",
}
NODE = {"generated": "src/generated.ts", "style": "node", "repo": "lobstrio/lobstrio-node"}
SDK = PYTHON if (ROOT / PYTHON["generated"]).exists() else NODE


def operations(text):
    return set(OPERATION.findall(text))


def current_version():
    if SDK["style"] == "python":
        return re.search(
            r'__version__ = "([^"]+)"', (ROOT / "src/lobstrio/_version.py").read_text()
        ).group(1)
    return json.loads((ROOT / "package.json").read_text())["version"]


def write_version(version):
    if SDK["style"] == "python":
        path = ROOT / "src/lobstrio/_version.py"
        path.write_text(
            re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', path.read_text())
        )
        return
    for name in ("package.json", "package-lock.json"):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text()
        # Only the package's own version, the first "version" of package.json, and the lockfile's root and root package.
        count = 1 if name == "package.json" else 2
        text = re.sub(r'("version": ")[^"]+(")', rf"\g<1>{version}\g<2>", text, count=count)
        path.write_text(text)
    path = ROOT / "src/version.ts"
    path.write_text(re.sub(r"VERSION = '[^']+'", f"VERSION = '{version}'", path.read_text()))


def changelog(version, added, removed, today):
    if added:
        lines = ["### Added", ""] + [
            f"- `{op}`: its typed method, options and record." for op in sorted(added)
        ]
    else:
        lines = [
            "### Changed",
            "",
            "- Types and descriptions regenerated from the API's OpenAPI: options, fields or "
            "wording of existing operations.",
        ]
    if removed:
        lines += ["", "### Removed", ""] + [
            f"- `{op}`, no longer in the API." for op in sorted(removed)
        ]
    head = f"## [{version}] - {today}" if SDK["style"] == "python" else f"## {version} ({today})"
    entry = "\n".join([head, "", *lines, "", ""])
    path = ROOT / "CHANGELOG.md"
    text = path.read_text()
    first = text.index("\n## ") + 1
    text = text[:first] + entry + text[first:]
    if SDK["style"] == "python":  # Keep a Changelog's links at the bottom, newest first
        link = f"[{version}]: https://github.com/{SDK['repo']}/releases/tag/v{version}\n"
        found = re.search(r"^\[\d+\.\d+\.\d+\]: ", text, re.M)
        text = (
            text[: found.start()] + link + text[found.start() :]
            if found
            else text.rstrip("\n") + "\n\n" + link
        )
    path.write_text(text)


def main():
    before = subprocess.run(
        ["git", "show", f"HEAD:{SDK['generated']}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    after = (ROOT / SDK["generated"]).read_text()
    added, removed = operations(after) - operations(before), operations(before) - operations(after)
    major, minor, patch = (int(x) for x in current_version().split("."))
    version = f"{major}.{minor + 1}.0" if added or removed else f"{major}.{minor}.{patch + 1}"
    write_version(version)
    changelog(version, added, removed, datetime.date.today().isoformat())
    print(version)


if __name__ == "__main__":
    main()
