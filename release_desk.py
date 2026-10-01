"""Store releases and render change notes grouped by category."""
import argparse
import json
import re
from pathlib import Path

CATEGORIES = ("Added", "Changed", "Fixed")


class ReleaseDesk:
    def __init__(self, path):
        self.path = Path(path)

    def releases(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def add(self, version, changes):
        if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
            raise ValueError("version must have three nonnegative numeric components")
        records = self.releases()
        if version in records:
            raise ValueError("release already exists")
        if not isinstance(changes, list) or not changes:
            raise ValueError("at least one change is required")
        clean = []
        for change in changes:
            category, text = change["category"], change["text"]
            if category not in CATEGORIES or not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
                raise ValueError("changes require a valid category and single-line text")
            clean.append({"category": category, "text": text.strip()})
        records[version] = clean
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"version": version, "changes": len(clean)}

    def versions(self):
        return sorted(self.releases(), key=lambda version: tuple(map(int, version.split("."))))

    def notes(self, version):
        records = self.releases()
        if version not in records:
            raise ValueError("unknown release")
        lines = [f"# {version}"]
        for category in CATEGORIES:
            changes = [change["text"] for change in records[version] if change["category"] == category]
            if changes:
                lines.extend(["", f"## {category}", *[f"- {text}" for text in changes]])
        return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="samples/releases.json")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("version")
    add.add_argument("changes")
    commands.add_parser("notes").add_argument("version")
    commands.add_parser("versions")
    args = parser.parse_args()
    try:
        desk = ReleaseDesk(args.store)
        if args.command == "notes":
            print(desk.notes(args.version), end="")
        else:
            result = desk.add(args.version, json.loads(Path(args.changes).read_text(encoding="utf-8"))) if args.command == "add" else desk.versions()
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
