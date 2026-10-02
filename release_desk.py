"""Store releases and render change notes grouped by category."""
import argparse
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

CATEGORIES = ("Added", "Changed", "Fixed")

VERSION_PATTERN = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"


def _unique_object(pairs):
    # object_pairs_hook for json.loads: reject repeated keys within one object.
    # Decoded keys compare case-sensitively, without trimming or normalization.
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError("duplicate JSON object key")
        seen.add(key)
    return dict(pairs)


def _loads_unique(raw):
    return json.loads(raw, object_pairs_hook=_unique_object)


class ReleaseDesk:
    def __init__(self, path):
        self.path = Path(path)

    def releases(self):
        return self._read_store()

    def add(self, version, changes):
        if not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        records = self._read_store()
        if version in records:
            raise ValueError("release already exists")
        clean = self._clean_changes(changes)
        records[version] = clean
        self._write_store(records)
        return {"version": version, "changes": len(clean)}

    def import_releases(self, payload):
        incoming = self._validated_payload(payload)
        records = self._read_store()
        imported, skipped = [], []
        for version, changes in incoming.items():
            if version in records:
                if self._clean_changes(records[version]) != changes:
                    raise ValueError("conflicting release already exists")
                skipped.append(version)
            else:
                records[version] = changes
                imported.append(version)
        order = lambda version: tuple(map(int, version.split(".")))
        imported.sort(key=order)
        skipped.sort(key=order)
        if imported:
            self._write_store(records)
        return {"imported": imported, "skipped": skipped}

    @staticmethod
    def _clean_changes(changes):
        if not isinstance(changes, list) or not changes:
            raise ValueError("at least one change is required")
        clean = []
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError("changes require a valid category and single-line text")
            category, text = change.get("category"), change.get("text")
            if category not in CATEGORIES or not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
                raise ValueError("changes require a valid category and single-line text")
            clean.append({"category": category, "text": text.strip()})
        return clean

    def export_releases(self, versions=None):
        if versions is not None and not isinstance(versions, list):
            raise ValueError("versions must be None or a list of version strings")
        if versions is None:
            chosen_names = None
        else:
            chosen_names, seen = [], set()
            for version in versions:
                if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                    raise ValueError("version must have three nonnegative numeric components")
                # Repeated names export once; first occurrence survives the dedup.
                if version not in seen:
                    seen.add(version)
                    chosen_names.append(version)
        # The whole store is validated even when the selection is small or empty.
        records = self._read_store()
        if chosen_names is None:
            chosen_names = list(records)
        else:
            for version in chosen_names:
                if version not in records:
                    raise ValueError("unknown release")
        order = lambda version: tuple(map(int, version.split(".")))
        # Cleaning keeps only category and stripped text, with array order and repeats.
        return {version: self._clean_changes(records[version])
                for version in sorted(chosen_names, key=order)}

    def export_releases_file(self, output, versions=None):
        exported = self.export_releases(versions)
        target = Path(output)
        if self._same_path(target, self.path):
            raise ValueError("export output must not be the release store")
        self._write_json_file(target, exported)
        return {"exported": list(exported)}

    @staticmethod
    def _same_path(left, right):
        # Lexical aliases (./x, a/../x) match even when neither path exists yet.
        left_abs, right_abs = os.path.abspath(left), os.path.abspath(right)
        if os.path.normcase(left_abs) == os.path.normcase(right_abs):
            return True
        if os.path.normcase(os.path.realpath(left_abs)) == os.path.normcase(os.path.realpath(right_abs)):
            return True
        try:
            # Existing symlinks and hard links share identity despite different names.
            return os.path.samefile(left, right)
        except OSError:
            return False

    @staticmethod
    def _write_json_file(target, records):
        target.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(records, ensure_ascii=False) + "\n"
        temp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False)
        try:
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
            temp.close()
            os.replace(temp.name, target)
        except OSError:
            try:
                os.unlink(temp.name)
            except OSError:
                pass
            raise

    def _validated_payload(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("import payload must be a JSON object")
        normalized = {}
        for version, changes in payload.items():
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
            normalized[version] = self._clean_changes(changes)
        return normalized

    def _read_store(self):
        if not self.path.exists():
            return {}
        # OSErrors (directory target, permissions, ...) propagate unchanged.
        raw = self.path.read_text(encoding="utf-8")
        if not raw:
            raise ValueError("release store must be a JSON object")
        try:
            records = _loads_unique(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("release store must be a JSON object") from exc
        return self._validated_records(records)

    @staticmethod
    def _validated_records(records):
        if not isinstance(records, dict):
            raise ValueError("release store must be a JSON object")
        for version, changes in records.items():
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
            # Validates without replacing: whitespace, extra fields and order survive.
            ReleaseDesk._clean_changes(changes)
        return records

    def _write_store(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
        temp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, delete=False)
        try:
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
            temp.close()
            os.replace(temp.name, self.path)
        except OSError:
            try:
                os.unlink(temp.name)
            except OSError:
                pass
            raise

    def versions(self):
        records = self._read_store()
        return sorted(records, key=lambda version: tuple(map(int, version.split("."))))

    def notes(self, version):
        records = self._read_store()
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
        records = self._read_store()
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
    def _checked_entries(records, version):
        if version not in records:
            raise ValueError("unknown release")
        return ReleaseDesk._clean_changes(records[version])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="samples/releases.json")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("version")
    add.add_argument("changes")
    commands.add_parser("notes").add_argument("version")
    commands.add_parser("versions")
    commands.add_parser("import").add_argument("file")
    diff = commands.add_parser("diff")
    diff.add_argument("base_version")
    diff.add_argument("target_version")
    export = commands.add_parser("export")
    export.add_argument("--version", dest="versions", action="append")
    export.add_argument("--output")
    args = parser.parse_args()
    try:
        desk = ReleaseDesk(args.store)
        if args.command == "notes":
            print(desk.notes(args.version), end="")
        else:
            if args.command == "add":
                result = desk.add(args.version, _loads_unique(Path(args.changes).read_text(encoding="utf-8")))
            elif args.command == "import":
                try:
                    payload = _loads_unique(Path(args.file).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("import file must contain UTF-8 encoded JSON") from exc
                result = desk.import_releases(payload)
            elif args.command == "diff":
                result = desk.diff(args.base_version, args.target_version)
            elif args.command == "export":
                if args.output is None:
                    result = desk.export_releases(args.versions)
                else:
                    result = desk.export_releases_file(args.output, args.versions)
            else:
                result = desk.versions()
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc) or exc.__class__.__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
