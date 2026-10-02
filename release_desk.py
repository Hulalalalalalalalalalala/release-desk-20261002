"""Store releases and render change notes grouped by category."""
import argparse
import copy
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path

CATEGORIES = ("Added", "Changed", "Fixed")

CHECK_STATUSES = ("done", "pending", "blocked")

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


def _validate_json_value(value, seen=None, *, require_object=False):
    # Validate a user-supplied JSON value read-only: string keys, JSON values
    # only, finite numbers, no cycles. Objects and arrays are traversed without
    # replacing anything; require_object additionally demands an object root.
    if seen is None:
        if require_object and not isinstance(value, dict):
            raise ValueError("configuration must be a JSON object")
        seen = set()
    if isinstance(value, dict):
        if id(value) in seen:
            raise ValueError("configuration contains a circular reference")
        seen.add(id(value))
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("configuration object keys must be strings")
            _validate_json_value(item, seen)
        seen.remove(id(value))
    elif isinstance(value, list):
        if id(value) in seen:
            raise ValueError("configuration contains a circular reference")
        seen.add(id(value))
        for item in value:
            _validate_json_value(item, seen)
        seen.remove(id(value))
    elif isinstance(value, bool) or value is None or isinstance(value, (str, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("configuration numbers must be finite")
    else:
        raise ValueError("configuration values must be JSON values")
    return value


def _validated_config(value, seen=None):
    # Validate a user-supplied JSON configuration before comparison: an object
    # root, string keys, JSON values only, finite numbers, no cycles.
    if seen is not None:
        return _validate_json_value(value, seen)
    return _validate_json_value(value, require_object=True)


def _validated_custom_choice(choice):
    # Validate one custom decision: a strict {"present": bool} object, with a
    # required "value" when present and no other fields. The value is any JSON
    # value (null included) and is traversed read-only.
    present = choice.get("present")
    if not isinstance(present, bool):
        raise ValueError("custom decision present must be a boolean")
    fields = set(choice)
    if present:
        if fields != {"present", "value"}:
            raise ValueError("a present custom decision requires only present and value")
        _validate_json_value(choice["value"])
    else:
        if fields != {"present"}:
            raise ValueError("an absent custom decision requires only present")
    return choice


def _validated_decisions(decisions):
    # Validate the decisions map before anything is compared: string keys and
    # either a legal side name or a well-formed custom choice object. Path
    # membership against actual conflict paths is checked afterwards.
    if not isinstance(decisions, dict):
        raise ValueError("decisions must be a JSON object")
    normalized = {}
    for path, choice in decisions.items():
        if not isinstance(path, str):
            raise ValueError("decision keys must be strings")
        if isinstance(choice, str):
            if choice not in ("target", "current"):
                raise ValueError("decision choice must be target, current or a custom decision object")
            normalized[path] = choice
        elif isinstance(choice, dict):
            normalized[path] = _validated_custom_choice(choice)
        else:
            raise ValueError("decision choice must be target, current or a custom decision object")
    return normalized


def _json_values_equal(base, target):
    # Recursive JSON equality: object key order is ignored, array order counts.
    if isinstance(base, bool) or isinstance(target, bool):
        return type(base) is bool and type(target) is bool and base == target
    if base is None or target is None:
        return base is None and target is None
    if isinstance(base, (int, float)) and isinstance(target, (int, float)):
        # Numerically equal integers and floats compare equal.
        return base == target
    if isinstance(base, str) or isinstance(target, str):
        return isinstance(base, str) and isinstance(target, str) and base == target
    if isinstance(base, dict) or isinstance(target, dict):
        if not (isinstance(base, dict) and isinstance(target, dict)):
            return False
        if base.keys() != target.keys():
            return False
        return all(_json_values_equal(base[key], target[key]) for key in base)
    if isinstance(base, list) or isinstance(target, list):
        if not (isinstance(base, list) and isinstance(target, list)):
            return False
        return len(base) == len(target) and all(
            _json_values_equal(base[index], target[index]) for index in range(len(base)))
    return False


def _escape_pointer_token(key):
    return key.replace("~", "~0").replace("/", "~1")


def _diff_config_values(base, target, path, added, removed, changed):
    if isinstance(base, dict) and isinstance(target, dict):
        for key in target.keys() - base.keys():
            added.append({"path": path + "/" + _escape_pointer_token(key), "value": target[key]})
        for key in base.keys() - target.keys():
            removed.append({"path": path + "/" + _escape_pointer_token(key), "value": base[key]})
        for key in base.keys() & target.keys():
            _diff_config_values(
                base[key], target[key], path + "/" + _escape_pointer_token(key),
                added, removed, changed)
    elif not _json_values_equal(base, target):
        changed.append({"path": path, "before": base, "after": target})


def _present_equal(present_a, value_a, present_b, value_b):
    # Equality with presence: a missing field only equals another missing
    # field, never null or any present value.
    if not present_a or not present_b:
        return not present_a and not present_b
    return _json_values_equal(value_a, value_b)


def _preview_side(present, value):
    side = {"present": present}
    if present:
        side["value"] = value
    return side


def _preview_config_values(base, target, current, path, conflicts):
    # Three-way merge of one object level. All three values are objects;
    # arrays, scalars and whole added/removed subobjects never reach here.
    preview = {}
    for key in base.keys() | target.keys() | current.keys():
        child_path = path + "/" + _escape_pointer_token(key)
        bp, tp, cp = key in base, key in target, key in current
        bv, tv, cv = base.get(key), target.get(key), current.get(key)
        if _present_equal(bp, bv, tp, tv):
            # The plan leaves the field untouched: keep the current state.
            if cp:
                preview[key] = cv
        elif _present_equal(cp, cv, tp, tv):
            # The current state already matches the target: keep it.
            if cp:
                preview[key] = cv
        elif _present_equal(cp, cv, bp, bv):
            # The current state matches the base: adopt the target,
            # including whole-field additions and deletions.
            if tp:
                preview[key] = tv
        elif bp and tp and cp and isinstance(bv, dict) and isinstance(tv, dict) and isinstance(cv, dict):
            # All three sides changed the same field, each to another object:
            # preview the subfields independently.
            preview[key] = _preview_config_values(bv, tv, cv, child_path, conflicts)
        else:
            # Divergent whole values (arrays, type changes, added/removed
            # subobjects, changed fields the current side lacks): one conflict
            # at this path, keeping the current presence state and value.
            conflicts.append({"path": child_path,
                              "base": _preview_side(bp, bv),
                              "target": _preview_side(tp, tv),
                              "current": _preview_side(cp, cv)})
            if cp:
                preview[key] = cv
    return preview


def _version_order(version):
    return tuple(map(int, version.split(".")))


def _resolve_config_values(base, target, current, path, choices, resolved):
    # Three-way merge mirroring _preview_config_values, applying confirmed
    # choices at conflict paths. choices maps exact conflict paths to either a
    # side name ("target"/"current") or a {"present", "value"?} object. All
    # values are traversed read-only; the returned object is built from deep
    # copies.
    resolved_config = {}
    for key in base.keys() | target.keys() | current.keys():
        child_path = path + "/" + _escape_pointer_token(key)
        bp, tp, cp = key in base, key in target, key in current
        bv, tv, cv = base.get(key), target.get(key), current.get(key)
        if _present_equal(bp, bv, tp, tv):
            if cp:
                resolved_config[key] = copy.deepcopy(cv)
        elif _present_equal(cp, cv, tp, tv):
            if cp:
                resolved_config[key] = copy.deepcopy(cv)
        elif _present_equal(cp, cv, bp, bv):
            if tp:
                resolved_config[key] = copy.deepcopy(tv)
        elif bp and tp and cp and isinstance(bv, dict) and isinstance(tv, dict) and isinstance(cv, dict):
            resolved_config[key] = _resolve_config_values(
                bv, tv, cv, child_path, choices, resolved)
        else:
            choice = choices.get(child_path)
            if choice is None:
                # Unresolved conflict: keep the current presence state and value.
                if cp:
                    resolved_config[key] = copy.deepcopy(cv)
            else:
                if isinstance(choice, str):
                    present = tp if choice == "target" else cp
                    side = tv if choice == "target" else cv
                    recorded = choice
                else:
                    # A custom choice replaces the whole field, even when the
                    # new value equals one of the sides or changes its type.
                    present = choice["present"]
                    side = choice.get("value")
                    recorded = "custom"
                resolved.append({"path": child_path, "choice": recorded})
                if present:
                    # A null side value is kept as a value, not a missing field.
                    resolved_config[key] = copy.deepcopy(side)
    return resolved_config


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
    try:
        temp.write(content)
        temp.flush()
        os.fsync(temp.fileno())
        temp.close()
        os.replace(temp.name, path)
    except OSError:
        try:
            os.unlink(temp.name)
        except OSError:
            pass
        raise


def _same_file(path_a, path_b):
    # Existing files: compare via inode so symlinks and hard links are caught.
    try:
        if path_a.exists() and path_b.exists():
            return path_a.samefile(path_b)
    except OSError:
        pass
    # Missing targets: compare resolved paths to catch textual aliases.
    try:
        return path_a.resolve() == path_b.resolve()
    except OSError:
        return os.path.abspath(path_a) == os.path.abspath(path_b)


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
        order = _version_order
        imported.sort(key=order)
        skipped.sort(key=order)
        if imported:
            self._write_store(records)
        return {"imported": imported, "skipped": skipped}

    def preview_import_releases(self, payload):
        # Read-only plan for import_releases: same validation and comparison,
        # but conflicts are reported instead of raised and nothing is written.
        incoming = self._validated_payload(payload)
        records = self._read_store()
        imported, skipped, conflicts = [], [], []
        for version, changes in incoming.items():
            if version not in records:
                imported.append(version)
                continue
            existing = self._clean_changes(records[version])
            if existing == changes:
                skipped.append(version)
                continue
            added, removed, unchanged = self._diff_entries(existing, changes)
            conflicts.append({"version": version, "added": added, "removed": removed,
                              "unchanged": unchanged, "orderOnly": not added and not removed})
        order = _version_order
        imported.sort(key=order)
        skipped.sort(key=order)
        conflicts.sort(key=lambda detail: order(detail["version"]))
        return {"imported": imported, "skipped": skipped,
                "conflicts": conflicts, "canImport": not conflicts}

    def export_releases(self, versions=None):
        if versions is None:
            selected = None
        else:
            if not isinstance(versions, list) or not all(isinstance(version, str) for version in versions):
                raise ValueError("versions must be None or a list of version strings")
            selected = []
            for version in versions:
                if not re.fullmatch(VERSION_PATTERN, version):
                    raise ValueError("version must have three nonnegative numeric components")
                if version not in selected:
                    selected.append(version)
        # The whole store is validated regardless of the requested selection.
        records = self._read_store()
        if selected is None:
            chosen = list(records)
        else:
            for version in selected:
                if version not in records:
                    raise ValueError("unknown release")
            chosen = selected
        exported = {}
        for version in sorted(chosen, key=_version_order):
            exported[version] = [
                {"category": change["category"], "text": change["text"].strip()}
                for change in records[version]
            ]
        return exported

    def checklist(self, version, payload):
        # Read-only release readiness check against a declared checklist.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_checklist(payload, version)
        # The whole store is validated before the version is looked up.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        groups = {"done": [], "pending": [], "blocked": []}
        ready = True
        for item in items:
            groups[item["status"]].append(
                {"id": item["id"], "text": item["text"],
                 "required": item["required"], "status": item["status"]}
            )
            if item["required"] and item["status"] != "done":
                ready = False
        return {"version": version, "ready": ready,
                "done": groups["done"], "pending": groups["pending"], "blocked": groups["blocked"]}

    def generate_checklist(self, version, payload):
        # Read-only checklist generated from a template: items are filtered by
        # the change categories actually registered for the release.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_template(payload)
        # The whole store is validated before the version is looked up.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        selected = self._applicable_template_items(items, records[version])
        return {"version": version, "items": selected}

    def audit_checklist(self, version, checklist, template):
        # Read-only audit of a declared checklist against the template items
        # applicable to the same release; status is never inferred from texts.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_checklist(checklist, version)
        template_items = self._validated_template(template)
        # The whole store is validated before the version is looked up.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        selected = self._applicable_template_items(template_items, records[version])
        groups = {"done": [], "pending": [], "blocked": []}
        ready = True
        for item in items:
            groups[item["status"]].append(
                {"id": item["id"], "text": item["text"],
                 "required": item["required"], "status": item["status"]}
            )
            if item["required"] and item["status"] != "done":
                ready = False
        # Ids match case-sensitively after trimming, without Unicode normalization.
        actual_by_id = {item["id"]: item for item in items}
        expected_ids = set()
        missing, mismatched = [], []
        for expected in selected:
            expected_ids.add(expected["id"])
            actual = actual_by_id.get(expected["id"])
            if actual is None:
                missing.append({"id": expected["id"], "text": expected["text"],
                                "required": expected["required"], "status": "pending"})
            elif actual["text"] != expected["text"] or actual["required"] != expected["required"]:
                mismatched.append({
                    "expected": {"id": expected["id"], "text": expected["text"],
                                 "required": expected["required"], "status": "pending"},
                    "actual": {"id": actual["id"], "text": actual["text"],
                               "required": actual["required"], "status": actual["status"]}})
            else:
                if expected["required"] and actual["status"] != "done":
                    ready = False
                continue
            # Differences in optional template items never affect readiness.
            if expected["required"]:
                ready = False
        unexpected = [
            {"id": item["id"], "text": item["text"],
             "required": item["required"], "status": item["status"]}
            for item in items if item["id"] not in expected_ids
        ]
        return {"version": version, "ready": ready,
                "done": groups["done"], "pending": groups["pending"], "blocked": groups["blocked"],
                "missing": missing, "mismatched": mismatched, "unexpected": unexpected}

    def reconcile_checklist(self, version, checklist, template):
        # Read-only update of a same-version checklist against a revised
        # template: unchanged definitions keep their status, redefined items
        # reset to pending, new items are added and inapplicable items drop.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_checklist(checklist, version)
        template_items = self._validated_template(template)
        # The whole store is validated before the version is looked up.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        selected = self._applicable_template_items(template_items, records[version])
        # Ids match case-sensitively after trimming, without Unicode normalization.
        previous = {item["id"]: item for item in items}
        reconciled, retained, reset, added = [], [], [], []
        for expected in selected:
            item_id = expected["id"]
            actual = previous.get(item_id)
            if actual is None:
                reconciled.append(dict(expected))
                added.append(item_id)
            elif actual["text"] != expected["text"] or actual["required"] != expected["required"]:
                # A redefined item takes the new definition and returns to pending.
                reconciled.append(dict(expected))
                reset.append(item_id)
            else:
                reconciled.append({"id": item_id, "text": expected["text"],
                                   "required": expected["required"], "status": actual["status"]})
                retained.append(item_id)
        selected_ids = {item["id"] for item in selected}
        removed = [item["id"] for item in items if item["id"] not in selected_ids]
        return {"version": version, "items": reconciled,
                "retained": retained, "reset": reset, "added": added, "removed": removed}

    def migrate_checklist(self, base_version, target_version, checklist, template):
        # Read-only cross-version migration: only progress still backed by the
        # target release is inherited. Unlike reconcile_checklist, a same-id
        # item whose definition is unchanged keeps its status solely when the
        # change entries covered by the item's template categories are
        # identical between the two registered releases.
        for version in (base_version, target_version):
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
        # The checklist is validated against the base version it declares; the
        # template is validated in full before the target filter is applied.
        items = self._validated_checklist(checklist, base_version)
        template_items = self._validated_template(template)
        # The whole store is validated before either version is looked up.
        records = self._read_store()
        if base_version not in records:
            raise ValueError("unknown release")
        if target_version not in records:
            raise ValueError("unknown release")
        selected = self._applicable_template_items(template_items, records[target_version])
        # Reuse the diff classification as the change comparison; the scope
        # filters it down to the categories a template item declares. When the
        # item declares none every category is compared, categories not present
        # in the target release included.
        added, removed, unchanged = self._diff_entries(
            self._clean_changes(records[base_version]),
            self._clean_changes(records[target_version]))
        changed_by_category = {}
        for category in CATEGORIES:
            changed_by_category[category] = any(
                entry["category"] == category for entry in added + removed)
        previous = {item["id"]: item for item in items}
        definitions = {item["id"]: item for item in template_items}
        migrated, retained, reset, added_ids = [], [], [], []
        for expected in selected:
            item_id = expected["id"]
            actual = previous.get(item_id)
            if actual is None:
                migrated.append(dict(expected))
                added_ids.append(item_id)
            elif actual["text"] != expected["text"] or actual["required"] != expected["required"]:
                migrated.append(dict(expected))
                reset.append(item_id)
            else:
                categories = definitions[item_id]["categories"]
                scope = CATEGORIES if categories is None else tuple(categories)
                if all(not changed_by_category[category] for category in scope):
                    migrated.append({"id": item_id, "text": expected["text"],
                                     "required": expected["required"], "status": actual["status"]})
                    retained.append(item_id)
                else:
                    migrated.append(dict(expected))
                    reset.append(item_id)
        selected_ids = {item["id"] for item in selected}
        removed_ids = [item["id"] for item in items if item["id"] not in selected_ids]
        return {"baseVersion": base_version, "version": target_version,
                "items": migrated,
                "retained": retained, "reset": reset, "added": added_ids,
                "removed": removed_ids}

    @staticmethod
    def _applicable_template_items(items, changes):
        present = {change["category"] for change in changes}
        selected = []
        for item in items:
            categories = item["categories"]
            if categories is None or present.intersection(categories):
                selected.append({"id": item["id"], "text": item["text"],
                                 "required": item["required"], "status": "pending"})
        if not selected:
            raise ValueError("no template items apply to this release")
        if not any(item["required"] for item in selected):
            raise ValueError("checklist requires at least one required item")
        return selected

    @staticmethod
    def _validated_template(payload):
        if not isinstance(payload, dict):
            raise ValueError("template must be a JSON object")
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("template requires at least one item")
        normalized = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("template items require id, text and required")
            item_id, text, required = item.get("id"), item.get("text"), item.get("required")
            if not isinstance(item_id, str) or not item_id.strip() or "\n" in item_id or "\r" in item_id:
                raise ValueError("template item id must be a non-empty single-line string")
            if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
                raise ValueError("template item text must be a non-empty single-line string")
            if not isinstance(required, bool):
                raise ValueError("template item required must be a boolean")
            categories = item.get("categories")
            if categories is not None:
                if (not isinstance(categories, list) or not categories
                        or any(category not in CATEGORIES for category in categories)
                        or len(set(categories)) != len(categories)):
                    raise ValueError("template item categories must be a non-empty array of unique known categories")
            item_id = item_id.strip()
            # Case-sensitive, no trimming beyond the ends, no Unicode normalization.
            if item_id in seen:
                raise ValueError("template item id must be unique")
            seen.add(item_id)
            normalized.append({"id": item_id, "text": text.strip(),
                               "required": required, "categories": categories})
        return normalized

    @staticmethod
    def _validated_checklist(payload, version):
        if not isinstance(payload, dict):
            raise ValueError("checklist must be a JSON object")
        declared = payload.get("version")
        if not isinstance(declared, str) or not re.fullmatch(VERSION_PATTERN, declared):
            raise ValueError("version must have three nonnegative numeric components")
        if declared != version:
            raise ValueError("checklist version does not match queried version")
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("checklist requires at least one item")
        normalized = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("checklist items require id, text, required and status")
            item_id, text, required, status = (
                item.get("id"), item.get("text"), item.get("required"), item.get("status"))
            if not isinstance(item_id, str) or not item_id.strip() or "\n" in item_id or "\r" in item_id:
                raise ValueError("checklist item id must be a non-empty single-line string")
            if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
                raise ValueError("checklist item text must be a non-empty single-line string")
            if not isinstance(required, bool):
                raise ValueError("checklist item required must be a boolean")
            if status not in CHECK_STATUSES:
                raise ValueError("checklist item status must be done, pending or blocked")
            item_id = item_id.strip()
            # Case-sensitive, no trimming beyond the ends, no Unicode normalization.
            if item_id in seen:
                raise ValueError("checklist item id must be unique")
            seen.add(item_id)
            normalized.append({"id": item_id, "text": text.strip(),
                               "required": required, "status": status})
        if not any(item["required"] for item in normalized):
            raise ValueError("checklist requires at least one required item")
        return normalized

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
        content = json.dumps(records, ensure_ascii=False, indent=2) + "\n"
        _atomic_write(self.path, content)

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
        added, removed, unchanged = self._diff_entries(base, target)
        return {"baseVersion": base_version, "targetVersion": target_version,
                "added": added, "removed": removed, "unchanged": unchanged}

    @staticmethod
    def _diff_entries(base, target):
        # Classify cleaned entries of target relative to base, per category in
        # fixed order; duplicates pair up by occurrence within each category.
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
        return added, removed, unchanged

    @staticmethod
    def _checked_entries(records, version):
        if version not in records:
            raise ValueError("unknown release")
        return ReleaseDesk._clean_changes(records[version])

    def diff_config(self, base_version, target_version, base_config, target_config):
        # Read-only structural comparison of two externally supplied
        # configurations for two registered versions. Nothing is stored and
        # the configurations are never inferred from change entries.
        for version in (base_version, target_version):
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
        _validated_config(base_config)
        _validated_config(target_config)
        # The whole store is validated before either version is looked up.
        records = self._read_store()
        if base_version not in records:
            raise ValueError("unknown release")
        if target_version not in records:
            raise ValueError("unknown release")
        added, removed, changed = [], [], []
        _diff_config_values(base_config, target_config, "", added, removed, changed)
        # Paths sort by Unicode code point, not by locale.
        added.sort(key=lambda entry: entry["path"])
        removed.sort(key=lambda entry: entry["path"])
        changed.sort(key=lambda entry: entry["path"])
        return {"baseVersion": base_version, "targetVersion": target_version,
                "added": added, "removed": removed, "changed": changed}

    def preview_config(self, base_version, target_version, base_config, target_config, current_config):
        # Read-only three-way preview of applying the base->target plan onto
        # the current configuration. Nothing is stored and the configurations
        # are never inferred from change entries.
        for version in (base_version, target_version):
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
        _validated_config(base_config)
        _validated_config(target_config)
        _validated_config(current_config)
        # The whole store is validated before either version is looked up.
        records = self._read_store()
        if base_version not in records:
            raise ValueError("unknown release")
        if target_version not in records:
            raise ValueError("unknown release")
        conflicts = []
        preview = _preview_config_values(base_config, target_config, current_config, "", conflicts)
        # Paths sort by Unicode code point, not by locale.
        conflicts.sort(key=lambda entry: entry["path"])
        return {"baseVersion": base_version, "targetVersion": target_version,
                "canApply": not conflicts, "config": preview, "conflicts": conflicts}

    def resolve_config(self, base_version, target_version, base_config, target_config,
                       current_config, decisions):
        # Read-only three-way resolution with per-path conflict choices. The
        # decisions map original preview_config conflict paths verbatim to
        # "target", "current" or a custom {"present", "value"?} decision;
        # nothing is stored or inferred from entries.
        for version in (base_version, target_version):
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
        _validated_config(base_config)
        _validated_config(target_config)
        _validated_config(current_config)
        choices = _validated_decisions(decisions)
        # The whole store is validated before either version is looked up.
        records = self._read_store()
        if base_version not in records:
            raise ValueError("unknown release")
        if target_version not in records:
            raise ValueError("unknown release")
        conflicts = []
        _preview_config_values(base_config, target_config, current_config, "", conflicts)
        conflict_paths = {entry["path"] for entry in conflicts}
        # Keys match the original conflict paths verbatim, using the same JSON
        # Pointer escaping: no trimming, normalization or subpath selection.
        for path in choices:
            if path not in conflict_paths:
                raise ValueError("decision path is not a conflict path")
        resolved = []
        config = _resolve_config_values(
            base_config, target_config, current_config, "", choices, resolved)
        chosen_paths = {entry["path"] for entry in resolved}
        remaining = [entry for entry in conflicts if entry["path"] not in chosen_paths]
        # Paths sort by Unicode code point, not by locale.
        remaining.sort(key=lambda entry: entry["path"])
        resolved.sort(key=lambda entry: entry["path"])
        return {"baseVersion": base_version, "targetVersion": target_version,
                "canApply": not remaining, "config": config,
                "conflicts": remaining, "resolved": resolved}

    def apply_config(self, base_version, target_version, base_config, target_config,
                     expected_config, decisions, current_path):
        # The only writing config operation: applies the confirmed
        # base->target plan to an existing current configuration file. The
        # file must match the expected snapshot under the diff_config equality
        # rules and every conflict must be decided; only then is the file
        # replaced wholesale, and only when the resolved configuration is
        # semantically different from what was read.
        for version in (base_version, target_version):
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
        _validated_config(base_config)
        _validated_config(target_config)
        _validated_config(expected_config)
        _validated_decisions(decisions)
        # The whole store is validated before either version is looked up.
        records = self._read_store()
        if base_version not in records:
            raise ValueError("unknown release")
        if target_version not in records:
            raise ValueError("unknown release")
        # The target must already exist as a non-symlink file and must not be
        # the store itself, however spelled or linked; a missing target is
        # never created, nor is its directory.
        target = Path(current_path)
        if not target.exists():
            raise ValueError("current configuration file must already exist")
        if target.is_symlink():
            raise ValueError("current configuration file must not be a symbolic link")
        if _same_file(target, self.path):
            raise ValueError("current configuration must not be the same file as the store")
        # OSErrors (permissions, directory target, ...) propagate unchanged.
        try:
            current_config = _loads_unique(target.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("current configuration file must contain UTF-8 encoded JSON") from exc
        _validated_config(current_config)
        if not _json_values_equal(current_config, expected_config):
            raise ValueError("current configuration does not match the expected configuration")
        confirmed = self.resolve_config(base_version, target_version, base_config,
                                        target_config, current_config, decisions)
        if not confirmed["canApply"]:
            raise ValueError("unresolved conflicts remain")
        config = confirmed["config"]
        changed = not _json_values_equal(config, current_config)
        if changed:
            content = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
            _atomic_write(target, content)
        return {"baseVersion": base_version, "targetVersion": target_version,
                "changed": changed, "config": config, "resolved": confirmed["resolved"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="samples/releases.json")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("version")
    add.add_argument("changes")
    commands.add_parser("notes").add_argument("version")
    commands.add_parser("versions")
    import_cmd = commands.add_parser("import")
    import_cmd.add_argument("file")
    import_cmd.add_argument("--dry-run", action="store_true", dest="dry_run")
    export = commands.add_parser("export")
    export.add_argument("--version", action="append", dest="versions")
    export.add_argument("--output")
    diff = commands.add_parser("diff")
    diff.add_argument("base_version")
    diff.add_argument("target_version")
    diff_config = commands.add_parser("diff-config")
    diff_config.add_argument("base_version")
    diff_config.add_argument("target_version")
    diff_config.add_argument("base_config")
    diff_config.add_argument("target_config")
    preview_config = commands.add_parser("preview-config")
    preview_config.add_argument("base_version")
    preview_config.add_argument("target_version")
    preview_config.add_argument("base_config")
    preview_config.add_argument("target_config")
    preview_config.add_argument("current_config")
    resolve_config = commands.add_parser("resolve-config")
    resolve_config.add_argument("base_version")
    resolve_config.add_argument("target_version")
    resolve_config.add_argument("base_config")
    resolve_config.add_argument("target_config")
    resolve_config.add_argument("current_config")
    resolve_config.add_argument("decisions")
    apply_config = commands.add_parser("apply-config")
    apply_config.add_argument("base_version")
    apply_config.add_argument("target_version")
    apply_config.add_argument("base_config")
    apply_config.add_argument("target_config")
    apply_config.add_argument("expected_config")
    apply_config.add_argument("decisions")
    apply_config.add_argument("current_config")
    check = commands.add_parser("check")
    check.add_argument("version")
    check.add_argument("file")
    make_checklist = commands.add_parser("make-checklist")
    make_checklist.add_argument("version")
    make_checklist.add_argument("file")
    audit = commands.add_parser("audit-checklist")
    audit.add_argument("version")
    audit.add_argument("checklist")
    audit.add_argument("template")
    reconcile = commands.add_parser("reconcile-checklist")
    reconcile.add_argument("version")
    reconcile.add_argument("checklist")
    reconcile.add_argument("template")
    migrate = commands.add_parser("migrate-checklist")
    migrate.add_argument("base_version")
    migrate.add_argument("target_version")
    migrate.add_argument("checklist")
    migrate.add_argument("template")
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
                result = desk.preview_import_releases(payload) if args.dry_run else desk.import_releases(payload)
            elif args.command == "diff":
                result = desk.diff(args.base_version, args.target_version)
            elif args.command == "diff-config":
                try:
                    base_payload = _loads_unique(Path(args.base_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("base configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    target_payload = _loads_unique(Path(args.target_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("target configuration file must contain UTF-8 encoded JSON") from exc
                result = desk.diff_config(args.base_version, args.target_version,
                                          base_payload, target_payload)
            elif args.command == "preview-config":
                try:
                    base_payload = _loads_unique(Path(args.base_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("base configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    target_payload = _loads_unique(Path(args.target_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("target configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    current_payload = _loads_unique(Path(args.current_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("current configuration file must contain UTF-8 encoded JSON") from exc
                result = desk.preview_config(args.base_version, args.target_version,
                                             base_payload, target_payload, current_payload)
            elif args.command == "resolve-config":
                try:
                    base_payload = _loads_unique(Path(args.base_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("base configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    target_payload = _loads_unique(Path(args.target_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("target configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    current_payload = _loads_unique(Path(args.current_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("current configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    decisions_payload = _loads_unique(Path(args.decisions).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("decisions file must contain UTF-8 encoded JSON") from exc
                result = desk.resolve_config(args.base_version, args.target_version,
                                             base_payload, target_payload, current_payload,
                                             decisions_payload)
            elif args.command == "apply-config":
                try:
                    base_payload = _loads_unique(Path(args.base_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("base configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    target_payload = _loads_unique(Path(args.target_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("target configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    expected_payload = _loads_unique(Path(args.expected_config).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("expected configuration file must contain UTF-8 encoded JSON") from exc
                try:
                    decisions_payload = _loads_unique(Path(args.decisions).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("decisions file must contain UTF-8 encoded JSON") from exc
                # The write target must not be any of the read-only inputs,
                # however spelled or linked; the store check happens inside
                # apply_config.
                current_path = Path(args.current_config)
                for input_file in (args.base_config, args.target_config,
                                   args.expected_config, args.decisions):
                    if _same_file(current_path, Path(input_file)):
                        raise ValueError("current configuration must not be the same file as an input file")
                result = desk.apply_config(args.base_version, args.target_version,
                                           base_payload, target_payload, expected_payload,
                                           decisions_payload, current_path)
            elif args.command == "check":
                try:
                    payload = _loads_unique(Path(args.file).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("check file must contain UTF-8 encoded JSON") from exc
                result = desk.checklist(args.version, payload)
            elif args.command == "make-checklist":
                try:
                    payload = _loads_unique(Path(args.file).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                result = desk.generate_checklist(args.version, payload)
            elif args.command == "audit-checklist":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    template_payload = _loads_unique(Path(args.template).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                result = desk.audit_checklist(args.version, checklist_payload, template_payload)
            elif args.command == "reconcile-checklist":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    template_payload = _loads_unique(Path(args.template).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                result = desk.reconcile_checklist(args.version, checklist_payload, template_payload)
            elif args.command == "migrate-checklist":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    template_payload = _loads_unique(Path(args.template).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                result = desk.migrate_checklist(args.base_version, args.target_version,
                                                checklist_payload, template_payload)
            elif args.command == "export":
                result = desk.export_releases(args.versions)
                if args.output:
                    output_path = Path(args.output)
                    if _same_file(desk.path, output_path):
                        raise ValueError("output must not be the same file as the store")
                    content = json.dumps(result, ensure_ascii=False) + "\n"
                    _atomic_write(output_path, content)
                    result = {"exported": list(result)}
            else:
                result = desk.versions()
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc) or exc.__class__.__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
