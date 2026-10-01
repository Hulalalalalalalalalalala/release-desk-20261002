"""Store releases and render change notes grouped by category."""
import argparse
import json
import re
from collections import defaultdict, deque
from pathlib import Path

CATEGORIES = ("Added", "Changed", "Fixed")
VERSION_RE = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


class ReleaseDesk:
    def __init__(self, path):
        self.path = Path(path)

    def releases(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def add(self, version, changes):
        if not VERSION_RE.fullmatch(version):
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

    def diff(self, base_version, target_version):
        for version in (base_version, target_version):
            if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
                raise ValueError("version must have three nonnegative numeric components")
        records = self.releases()
        if not isinstance(records, dict):
            raise ValueError("store must be a JSON object of releases")
        if base_version not in records or target_version not in records:
            raise ValueError("unknown release")
        base_entries = self._read_entries(records[base_version])
        target_entries = self._read_entries(records[target_version])

        waiting = {category: defaultdict(deque) for category in CATEGORIES}
        for index, entry in enumerate(base_entries):
            waiting[entry["category"]][entry["text"]].append(index)
        matched_base = set()
        matched_target = [False] * len(target_entries)
        for index, entry in enumerate(target_entries):
            candidates = waiting[entry["category"]][entry["text"]]
            if candidates:
                matched_base.add(candidates.popleft())
                matched_target[index] = True

        target_index = {category: [] for category in CATEGORIES}
        for index, entry in enumerate(target_entries):
            target_index[entry["category"]].append(index)
        added, unchanged = [], []
        for category in CATEGORIES:
            for index in target_index[category]:
                (unchanged if matched_target[index] else added).append(
                    {"category": category, "text": target_entries[index]["text"]}
                )
        removed = []
        for category in CATEGORIES:
            for index, entry in enumerate(base_entries):
                if entry["category"] == category and index not in matched_base:
                    removed.append({"category": category, "text": entry["text"]})
        return {
            "baseVersion": base_version,
            "targetVersion": target_version,
            "added": added,
            "removed": removed,
            "unchanged": unchanged,
        }

    def _read_entries(self, record):
        if not isinstance(record, list) or not record:
            raise ValueError("release record must be a non-empty array")
        entries = []
        for item in record:
            if not isinstance(item, dict) or "category" not in item or "text" not in item:
                raise ValueError("changes require a valid category and single-line text")
            category, text = item["category"], item["text"]
            if category not in CATEGORIES or not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
                raise ValueError("changes require a valid category and single-line text")
            entries.append({"category": category, "text": text.strip()})
        return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="samples/releases.json")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("version")
    add.add_argument("changes")
    commands.add_parser("notes").add_argument("version")
    commands.add_parser("versions")
    diff = commands.add_parser("diff")
    diff.add_argument("base_version")
    diff.add_argument("target_version")
    args = parser.parse_args()
    try:
        desk = ReleaseDesk(args.store)
        if args.command == "notes":
            print(desk.notes(args.version), end="")
        elif args.command == "diff":
            print(json.dumps(desk.diff(args.base_version, args.target_version), ensure_ascii=False))
        else:
            result = desk.add(args.version, json.loads(Path(args.changes).read_text(encoding="utf-8"))) if args.command == "add" else desk.versions()
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
