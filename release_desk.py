"""Store releases and render change notes grouped by category."""
import argparse
import contextlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

CATEGORIES = ("Added", "Changed", "Fixed")

VERSION_PATTERN = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"


class ReleaseDesk:
    def __init__(self, path):
        self.path = Path(path)

    def releases(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def add(self, version, changes):
        if not re.fullmatch(VERSION_PATTERN, version):
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
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
        records = self.releases()
        if not isinstance(records, dict):
            raise ValueError("release store must be a JSON object")
        base = self._checked_entries(records, base_version)
        target = self._checked_entries(records, target_version)
        added, removed, unchanged = [], [], []
        for category in CATEGORIES:
            base_entries = [entry for entry in base if entry["category"] == category]
            target_entries = [entry for entry in target if entry["category"] == category]
            available = Counter(entry["text"] for entry in base_entries)
            for entry in target_entries:
                if available[entry["text"]] > 0:
                    available[entry["text"]] -= 1
                    unchanged.append(entry)
                else:
                    added.append(entry)
            matched = Counter(entry["text"] for entry in target_entries)
            for entry in base_entries:
                if matched[entry["text"]] > 0:
                    matched[entry["text"]] -= 1
                else:
                    removed.append(entry)
        return {"baseVersion": base_version, "targetVersion": target_version,
                "added": added, "removed": removed, "unchanged": unchanged}

    @staticmethod
    def _version_key(version):
        return tuple(map(int, version.split(".")))

    @staticmethod
    def _checked_change_list(changes):
        if not isinstance(changes, list) or not changes:
            raise ValueError("release must contain at least one change")
        clean = []
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError("changes require a valid category and single-line text")
            category, text = change.get("category"), change.get("text")
            if category not in CATEGORIES or not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
                raise ValueError("changes require a valid category and single-line text")
            clean.append({"category": category, "text": text.strip()})
        return clean

    def import_releases(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("import payload must be a JSON object")
        incoming = {}
        for version, changes in payload.items():
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
            incoming[version] = self._checked_change_list(changes)
        records, clean = self._load_records()
        imported, skipped = [], []
        for version, changes in incoming.items():
            if version in records:
                if clean[version] != changes:
                    raise ValueError("conflicting release already exists")
                skipped.append(version)
            else:
                imported.append(version)
        imported.sort(key=self._version_key)
        skipped.sort(key=self._version_key)
        if imported:
            merged = dict(records)
            merged.update(incoming)
            self._write_bytes(json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        return {"imported": imported, "skipped": skipped}

    def _load_records(self):
        if not self.path.exists():
            return {}, {}
        try:
            raw = self.path.read_bytes()
            records = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("release store must be valid UTF-8 JSON") from exc
        if not isinstance(records, dict):
            raise ValueError("release store must be a JSON object")
        clean = {}
        for version, changes in records.items():
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
            clean[version] = self._checked_change_list(changes)
        return records, clean

    def _write_bytes(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix="." + self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    @classmethod
    def _checked_entries(cls, records, version):
        if version not in records:
            raise ValueError("unknown release")
        return cls._checked_change_list(records[version])


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
    imp = commands.add_parser("import")
    imp.add_argument("file")
    args = parser.parse_args()
    try:
        desk = ReleaseDesk(args.store)
        if args.command == "notes":
            print(desk.notes(args.version), end="")
        else:
            if args.command == "add":
                result = desk.add(args.version, json.loads(Path(args.changes).read_text(encoding="utf-8")))
            elif args.command == "diff":
                result = desk.diff(args.base_version, args.target_version)
            elif args.command == "import":
                result = desk.import_releases(_read_payload(Path(args.file)))
            else:
                result = desk.versions()
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


def _read_payload(path):
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("import file must be valid UTF-8 JSON") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("import file must be valid UTF-8 JSON") from exc


if __name__ == "__main__":
    raise SystemExit(main())
