"""Store releases and render change notes grouped by category."""
import argparse
import copy
import heapq
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


def _validated_merge_choices(decisions):
    # Validate the checklist conflict-choice map before it is applied: a JSON
    # object, possibly empty, with string keys mapping to "incoming", "current"
    # or a strict custom decision object: {"present": true, "value": item}
    # replaces the whole item and {"present": false} deletes it. Custom items
    # follow the checklist item rules and their normalized id must equal the
    # decision key. Membership against actual conflict ids is checked
    # afterwards.
    if not isinstance(decisions, dict):
        raise ValueError("merge decisions must be a JSON object")
    normalized = {}
    for item_id, choice in decisions.items():
        if not isinstance(item_id, str):
            raise ValueError("decision keys must be strings")
        if isinstance(choice, str):
            if choice not in ("incoming", "current"):
                raise ValueError(
                    "decision choice must be incoming, current or a custom decision object")
            normalized[item_id] = choice
        elif isinstance(choice, dict):
            normalized[item_id] = _validated_custom_merge_choice(item_id, choice)
        else:
            raise ValueError(
                "decision choice must be incoming, current or a custom decision object")
    return normalized


def _validated_custom_merge_choice(item_id, choice):
    # Validate one custom checklist decision: present is a strict boolean and
    # the object has no other shape. A present decision carries a single whole
    # checklist value with exactly id, text, required and status, validated and
    # trimmed like a checklist item; its normalized id must equal the decision
    # key exactly, with no trimming or Unicode normalization of the key. An
    # absent decision carries only present and deletes the item.
    if "present" not in choice:
        raise ValueError("custom decision requires a boolean present")
    present = choice["present"]
    if not isinstance(present, bool):
        raise ValueError("custom decision present must be a boolean")
    fields = set(choice)
    if present:
        if fields != {"present", "value"}:
            raise ValueError("a present custom decision requires only present and value")
        item = ReleaseDesk._validated_checklist_item(choice["value"], strict=True)
        if item["id"] != item_id:
            raise ValueError("custom item id must equal the decision conflict id")
        return {"present": True, "value": item}
    if fields != {"present"}:
        raise ValueError("an absent custom decision requires only present")
    return {"present": False}


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


def _exclusive_write(path, content):
    # Create-only write for release-record snapshots: the final name must not
    # already exist, so an existing file or symlink is never replaced or
    # followed. Missing parent directories are never created: opening the
    # temporary file in them raises OSError before anything is made.
    temp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
    try:
        temp.write(content)
        temp.flush()
        os.fsync(temp.fileno())
        temp.close()
        # link(2) fails when the destination name already exists, a dangling
        # symlink included, and never follows a trailing symlink.
        os.link(temp.name, path)
    except OSError:
        try:
            os.unlink(temp.name)
        except OSError:
            pass
        raise
    try:
        os.unlink(temp.name)
    except OSError:
        pass


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

    def check_dependencies(self, version, checklist, dependencies):
        # Read-only dependency-aware readiness check over a declared checklist:
        # declared statuses are kept and a blocked effective status is layered
        # on top from direct prerequisites that are not themselves effectively
        # done. Nothing is written and the passed objects are never mutated.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_checklist(checklist, version)
        prerequisites = self._validated_dependencies(dependencies, {item["id"] for item in items})
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        status_by_id = {item["id"]: item["status"] for item in items}
        effective = {}

        def effective_status(item_id):
            # Memoized effective status over the validated acyclic graph. A
            # declaration counts only when every direct prerequisite is
            # effectively done; otherwise the item is blocked no matter what
            # it declared, so blocking propagates layer by layer without
            # listing transitive prerequisites.
            if item_id not in effective:
                status = status_by_id[item_id]
                if any(effective_status(prerequisite) != "done"
                       for prerequisite in prerequisites.get(item_id, ())):
                    status = "blocked"
                effective[item_id] = status
            return effective[item_id]

        result_items, ready = [], True
        for item in items:
            item_id = item["id"]
            effect = effective_status(item_id)
            waiting = [prerequisite for prerequisite in prerequisites.get(item_id, ())
                       if effective_status(prerequisite) != "done"]
            result_items.append({"id": item["id"], "text": item["text"],
                                 "required": item["required"], "status": item["status"],
                                 "effectiveStatus": effect, "waiting": waiting})
            # Optional items never decide readiness themselves, but an
            # effectively blocked prerequisite keeps a required item blocked.
            if item["required"] and effect != "done":
                ready = False
        return {"version": version, "ready": ready, "items": result_items}

    def explain_dependencies(self, version, checklist, dependencies):
        # Read-only release-blocker explanation layered on check_dependencies:
        # the same validation, effective statuses and readiness rule, but the
        # report lists only the unfinished items that block required items,
        # each with the affected required items and their dependency paths.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_checklist(checklist, version)
        prerequisites = self._validated_dependencies(dependencies, {item["id"] for item in items})
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        status_by_id = {item["id"]: item["status"] for item in items}
        effective = {}

        def effective_status(item_id):
            # The same memoized effective status as check_dependencies.
            if item_id not in effective:
                status = status_by_id[item_id]
                if any(effective_status(prerequisite) != "done"
                       for prerequisite in prerequisites.get(item_id, ())):
                    status = "blocked"
                effective[item_id] = status
            return effective[item_id]

        position = {item["id"]: index for index, item in enumerate(items)}

        def best_paths(source):
            # Fewest-edge paths from source over the subgraph of items that
            # are not effectively done; equal-length paths are ordered by the
            # checklist positions of their nodes, first differing position
            # wins, so the dependency-array order never matters.
            costs = {source: (0, (position[source],))}
            paths = {source: [source]}
            heap = [((0, (position[source],)), source)]
            while heap:
                cost, node = heapq.heappop(heap)
                if cost != costs[node]:
                    continue
                for prerequisite in prerequisites.get(node, ()):
                    if effective_status(prerequisite) == "done":
                        continue
                    candidate = (cost[0] + 1, cost[1] + (position[prerequisite],))
                    if prerequisite not in costs or candidate < costs[prerequisite]:
                        costs[prerequisite] = candidate
                        paths[prerequisite] = paths[node] + [prerequisite]
                        heapq.heappush(heap, (candidate, prerequisite))
            return paths

        # Trace from every required item that is not effectively done,
        # following only prerequisites that are themselves not effectively
        # done. Nodes declared pending or blocked count as reasons and their
        # own prerequisites are still traced; declared-done nodes only carry
        # the path. Optional items no required item reaches never appear.
        reason_requireds = {}
        paths_from = {}
        for item in items:
            if not item["required"] or effective_status(item["id"]) == "done":
                continue
            paths = best_paths(item["id"])
            paths_from[item["id"]] = paths
            for node in paths:
                if status_by_id[node] != "done":
                    reason_requireds.setdefault(node, set()).add(item["id"])
        ready = all(effective_status(item["id"]) == "done"
                    for item in items if item["required"])
        reasons = []
        for item in items:
            item_id = item["id"]
            if item_id not in reason_requireds:
                continue
            affected = [{"id": required_id, "path": paths_from[required_id][item_id]}
                        for required_id in (entry["id"] for entry in items if entry["required"])
                        if required_id in reason_requireds[item_id]]
            reasons.append({"id": item_id, "text": item["text"],
                            "required": item["required"], "status": item["status"],
                            "affected": affected})
        return {"version": version, "ready": ready, "reasons": reasons}

    def plan_dependencies(self, version, checklist, dependencies):
        # Read-only release preparation order plan layered on
        # check_dependencies: the same validation and readiness rule, but the
        # report batches the unfinished work still required for release. The
        # planning scope is every required item together with all of its direct
        # and indirect prerequisites, traversing declared-done nodes; only
        # items declared pending or blocked inside that scope are scheduled.
        # Nothing is written and the passed objects are never mutated.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_checklist(checklist, version)
        prerequisites = self._validated_dependencies(dependencies, {item["id"] for item in items})
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        status_by_id = {item["id"]: item["status"] for item in items}
        effective = {}

        def effective_status(item_id):
            # The same memoized effective status as check_dependencies.
            if item_id not in effective:
                status = status_by_id[item_id]
                if any(effective_status(prerequisite) != "done"
                       for prerequisite in prerequisites.get(item_id, ())):
                    status = "blocked"
                effective[item_id] = status
            return effective[item_id]

        # Mark the planning scope: every required item plus every item reached
        # by following prerequisites from a required item. Edges out of
        # declared-done nodes are followed too, so an unfinished prerequisite
        # behind a done chain stays in scope; an optional branch no required
        # item reaches never enters the plan.
        scope = set()

        def mark_scope(node):
            if node in scope:
                return
            scope.add(node)
            for prerequisite in prerequisites.get(node, ()):
                mark_scope(prerequisite)

        for item in items:
            if item["required"]:
                mark_scope(item["id"])
        ready = all(effective_status(item["id"]) == "done"
                    for item in items if item["required"])
        # Reduce the prerequisite edges past declared-done nodes: an item's
        # effective unfinished prerequisites are its direct unfinished
        # prerequisites plus the unfinished prerequisites of any done direct
        # prerequisite, recursively. The indirect relation through a done
        # node therefore still orders the plan, while the done node itself is
        # never scheduled. Every reached prerequisite lies in scope because
        # mark_scope followed the same edges.
        reduced = {}

        def unfinished_prerequisites(node):
            if node not in reduced:
                closure = set()
                for prerequisite in prerequisites.get(node, ()):
                    if status_by_id[prerequisite] == "done":
                        closure.update(unfinished_prerequisites(prerequisite))
                    else:
                        closure.add(prerequisite)
                reduced[node] = closure
            return reduced[node]

        # Batch the unfinished scope items: each wave takes every not-yet-
        # scheduled item whose effective unfinished prerequisites are all in
        # earlier waves. The validated acyclic graph guarantees progress, so
        # the waves stay compact (no empty batch) and end with the pool empty.
        remaining = {item["id"] for item in items
                     if item["id"] in scope and status_by_id[item["id"]] != "done"}
        scheduled = set()
        waves = []
        while remaining:
            current = []
            for item in items:
                item_id = item["id"]
                if item_id in remaining and unfinished_prerequisites(item_id) <= scheduled:
                    current.append(item_id)
            scheduled.update(current)
            remaining.difference_update(current)
            waves.append(current)
        # blocked lists scope items declared blocked in checklist order,
        # independently of the waves; being planned never clears the block.
        blocked = [item["id"] for item in items
                   if item["id"] in scope and status_by_id[item["id"]] == "blocked"]
        return {"version": version, "ready": ready,
                "waves": waves, "blocked": blocked}

    @staticmethod
    def _validated_dependencies(dependencies, known_ids):
        # Validate the dependency map: an object mapping normalized item ids
        # to arrays of normalized prerequisite ids, each following the
        # checklist single-line id rule. Keys, array entries, unknown ends,
        # self dependencies and cycles are all rejected; empty maps and empty
        # arrays mean "no prerequisites".
        if not isinstance(dependencies, dict):
            raise ValueError("dependencies must be a JSON object")
        normalized = {}
        seen_keys = set()
        for item_id, prerequisites in dependencies.items():
            if not isinstance(item_id, str) or not item_id.strip() or "\n" in item_id or "\r" in item_id:
                raise ValueError("dependency item id must be a non-empty single-line string")
            item_id = item_id.strip()
            # Case-sensitive, no trimming beyond the ends, no Unicode normalization.
            if item_id in seen_keys:
                raise ValueError("dependency item id must be unique")
            seen_keys.add(item_id)
            if not isinstance(prerequisites, list):
                raise ValueError("dependency prerequisites must be arrays of item ids")
            ordered, seen = [], set()
            for prerequisite in prerequisites:
                if (not isinstance(prerequisite, str) or not prerequisite.strip()
                        or "\n" in prerequisite or "\r" in prerequisite):
                    raise ValueError("dependency item id must be a non-empty single-line string")
                prerequisite = prerequisite.strip()
                # Case-sensitive, no trimming beyond the ends, no Unicode normalization.
                if prerequisite in seen:
                    raise ValueError("dependency prerequisite ids must be unique within one item")
                seen.add(prerequisite)
                ordered.append(prerequisite)
            normalized[item_id] = ordered
        for item_id, prerequisites in normalized.items():
            if item_id not in known_ids:
                raise ValueError("dependency item id must exist in the checklist")
            for prerequisite in prerequisites:
                if prerequisite not in known_ids:
                    raise ValueError("dependency prerequisite id must exist in the checklist")
                if prerequisite == item_id:
                    raise ValueError("dependencies must not contain a self dependency")
        # Reject every remaining cycle, including cycles over optional items:
        # resolve every id with an empty trail set.
        visiting = set()
        settled = set()

        def visit(node):
            if node in settled:
                return
            if node in visiting:
                raise ValueError("dependencies must not contain a cycle")
            visiting.add(node)
            for prerequisite in normalized.get(node, ()):
                visit(prerequisite)
            visiting.remove(node)
            settled.add(node)

        for item_id in normalized:
            visit(item_id)
        return normalized

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

    def generate_checklist_with_dependencies(self, version, payload, dependencies):
        # Read-only dependency-aware checklist generation: the template is
        # filtered by the release's change categories exactly like
        # generate_checklist, then every direct and indirect prerequisite of
        # the selected items is added even when its own categories do not
        # apply. The whole dependency graph is validated against the whole
        # template first, so edges of template items the filter would drop
        # still have to be legal and acyclic. Nothing is written and the
        # passed objects are never mutated.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_template(payload)
        # Every id in the dependency map, at either end, must reference a
        # template item: the complete map and the complete template are
        # validated, including items the category filter never selects.
        prerequisites = self._validated_dependencies(
            dependencies, {item["id"] for item in items})
        # The whole store is validated before the version is looked up.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        selected = self._applicable_template_items(items, records[version])
        selected_ids = {item["id"] for item in selected}
        # Close over every direct and indirect prerequisite of the selected
        # items; shared prerequisites are collected once. Only the member set
        # matters here: the final order always follows the template.
        closure = set(selected_ids)
        pending = list(selected_ids)
        while pending:
            node = pending.pop()
            for prerequisite in prerequisites.get(node, ()):
                if prerequisite not in closure:
                    closure.add(prerequisite)
                    pending.append(prerequisite)
        selected_by_id = {item["id"]: item for item in selected}
        # Final items keep template order: initially selected items stay in
        # their positions and prerequisites added solely for the closure slot
        # in at their template positions, even when their categories do not
        # apply. The selected dicts already carry the uniform pending item
        # shape; added prerequisites are rendered from their template entry.
        result_items, dependency_pairs = [], []
        for item in items:
            item_id = item["id"]
            if item_id not in closure:
                continue
            if item_id in selected_ids:
                result_items.append(dict(selected_by_id[item_id]))
            else:
                result_items.append({"id": item_id, "text": item["text"],
                                     "required": item["required"], "status": "pending"})
            # Every final id gets a normalized array in template order;
            # omitted items and declared empty arrays both freeze to [].
            dependency_pairs.append((item_id, list(prerequisites.get(item_id, ()))))
        # includedPrerequisites lists, in template order, only the items
        # added solely because of a dependency edge.
        included_prerequisites = [item["id"] for item in items
                                  if item["id"] in closure and item["id"] not in selected_ids]
        return {"version": version, "items": result_items,
                "dependencies": dict(dependency_pairs),
                "includedPrerequisites": included_prerequisites}

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
        return self._audit_report(version, items, selected)

    @staticmethod
    def _audit_report(version, items, expected_items):
        # Build the audit_checklist report from normalized actual items and the
        # expected item set (already filtered/closed over), both sequences of
        # four-field items with a pending status on the expected side.
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
        for expected in expected_items:
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

    def audit_checklist_with_dependencies(self, version, checklist, template, dependencies):
        # Read-only dependency-aware audit: the expected set is the applicable
        # template items closed over every direct and indirect prerequisite,
        # exactly like generate_checklist_with_dependencies, and the report
        # pairs an audit over that final set with a dependency report over the
        # actual items plus the still-missing expected items. The whole
        # dependency graph is validated against the whole template first, so
        # edges of template items the filter would drop still have to be legal
        # and acyclic. Nothing is written and the passed objects are never
        # mutated.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_checklist(checklist, version)
        template_items = self._validated_template(template)
        # Every id in the dependency map, at either end, must reference a
        # template item: the complete map and the complete template are
        # validated, including items the category filter never selects.
        prerequisites = self._validated_dependencies(
            dependencies, {item["id"] for item in template_items})
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        selected = self._applicable_template_items(template_items, records[version])
        selected_ids = {item["id"] for item in selected}
        # Close over every direct and indirect prerequisite of the selected
        # items; shared prerequisites are collected once. Only the member set
        # matters here: the final order always follows the template.
        closure = set(selected_ids)
        pending = list(selected_ids)
        while pending:
            node = pending.pop()
            for prerequisite in prerequisites.get(node, ()):
                if prerequisite not in closure:
                    closure.add(prerequisite)
                    pending.append(prerequisite)
        selected_by_id = {item["id"]: item for item in selected}
        # Expected items keep template order, each carrying the uniform pending
        # shape whether it was selected by category or added solely for the
        # closure.
        expected_items = []
        for item in template_items:
            item_id = item["id"]
            if item_id not in closure:
                continue
            if item_id in selected_ids:
                expected_items.append(dict(selected_by_id[item_id]))
            else:
                expected_items.append({"id": item_id, "text": item["text"],
                                      "required": item["required"], "status": "pending"})
        # includedPrerequisites lists, in template order, only the items
        # expected solely because of a dependency edge.
        included_prerequisites = [item["id"] for item in template_items
                                  if item["id"] in closure and item["id"] not in selected_ids]
        audit = self._audit_report(version, items, expected_items)
        dependency_check = self._audit_dependency_report(
            version, items, expected_items, prerequisites, closure)
        return {"version": version,
                "ready": audit["ready"] and dependency_check["ready"],
                "audit": copy.deepcopy(audit),
                "dependencyCheck": dependency_check,
                "includedPrerequisites": included_prerequisites}

    @staticmethod
    def _audit_dependency_report(version, items, expected_items, prerequisites, closure):
        # Dependency report over the final expected set: actual checklist items
        # first in checklist order, then expected items absent from the
        # checklist in template order. Actual items keep their definition and
        # declared status; appended items take the template definition with a
        # pending status. Only dependency edges whose both ends lie in the final
        # set count; actual items outside it behave as having no prerequisites.
        actual_by_id = {item["id"]: item for item in items}
        missing_expected = [expected for expected in expected_items
                            if expected["id"] not in actual_by_id]

        def declared_status(node):
            actual = actual_by_id.get(node)
            return actual["status"] if actual is not None else "pending"

        effective = {}

        def effective_status(node):
            # The same memoized effective-status rule as check_dependencies,
            # over the final set: an actual item outside the set has no edges.
            if node not in effective:
                status = declared_status(node)
                if node in closure and any(
                        effective_status(prerequisite) != "done"
                        for prerequisite in prerequisites.get(node, ())
                        if prerequisite in closure):
                    status = "blocked"
                effective[node] = status
            return effective[node]

        result_items, ready = [], True

        def add_report_item(item_id, text, required, declared):
            waiting = []
            if item_id in closure:
                waiting = [prerequisite for prerequisite in prerequisites.get(item_id, ())
                           if prerequisite in closure
                           and effective_status(prerequisite) != "done"]
            effect = effective_status(item_id)
            result_items.append({"id": item_id, "text": text, "required": required,
                                 "status": declared, "effectiveStatus": effect,
                                 "waiting": waiting})
            return effect

        for item in items:
            effect = add_report_item(item["id"], item["text"], item["required"], item["status"])
            if item["required"] and effect != "done":
                ready = False
        for expected in missing_expected:
            effect = add_report_item(expected["id"], expected["text"],
                                     expected["required"], "pending")
            if expected["required"] and effect != "done":
                ready = False
        return {"version": version, "ready": ready, "items": result_items}

    def release_record(self, version, checklist, template, rollback):
        # Read-only release snapshot: the audit follows the existing
        # audit_checklist rules exactly, and a not-ready audit rejects the
        # record without adding any optional-item blocking of its own. The
        # returned snapshot is detached from every passed object and carries
        # no generated timestamp, so identical inputs yield identical records.
        audit = self.audit_checklist(version, checklist, template)
        if not audit["ready"]:
            raise ValueError("release record requires a ready audit")
        target_version, steps = self._validated_rollback(rollback, version)
        changes = self.export_releases([version])[version]
        return {"version": version,
                "changes": copy.deepcopy(changes),
                "notes": self.notes(version),
                "audit": copy.deepcopy(audit),
                "rollback": {"targetVersion": target_version, "steps": steps}}

    def release_record_with_dependencies(self, version, checklist, template,
                                         rollback, dependencies):
        # Read-only release snapshot that additionally freezes the dependency
        # map: the audit follows audit_checklist and the dependency report
        # follows check_dependencies exactly, and the record is built only
        # when both reports are ready. An unfinished optional item never
        # blocks the record by itself, but as a direct or indirect
        # prerequisite of a required item it blocks through the existing
        # effective-status propagation. The returned snapshot is detached
        # from every passed object and carries no generated timestamp, so
        # identical inputs yield identical records.
        audit = self.audit_checklist(version, checklist, template)
        dependency_check = self.check_dependencies(version, checklist, dependencies)
        if not audit["ready"] or not dependency_check["ready"]:
            raise ValueError("release record requires a ready audit and dependency check")
        target_version, steps = self._validated_rollback(rollback, version)
        items = self._validated_checklist(checklist, version)
        prerequisites = self._validated_dependencies(
            dependencies, {item["id"] for item in items})
        # Every normalized checklist id appears in checklist order; an
        # undeclared item freezes to an empty array and declared
        # prerequisites keep their dependency-array order.
        frozen = {item["id"]: list(prerequisites.get(item["id"], ())) for item in items}
        changes = self.export_releases([version])[version]
        return {"version": version,
                "changes": copy.deepcopy(changes),
                "notes": self.notes(version),
                "audit": copy.deepcopy(audit),
                "rollback": {"targetVersion": target_version, "steps": steps},
                "dependencies": frozen,
                "dependencyCheck": copy.deepcopy(dependency_check)}

    def _validated_rollback(self, rollback, release_version):
        # Validate the rollback plan: a strict {"targetVersion", "steps"}
        # object. targetVersion is null or a registered version numerically
        # smaller than the release; steps is a non-empty array of single-line
        # strings, stored trimmed with order and duplicates preserved.
        if not isinstance(rollback, dict):
            raise ValueError("rollback plan must be a JSON object")
        if set(rollback) != {"targetVersion", "steps"}:
            raise ValueError("rollback plan requires exactly targetVersion and steps")
        target_version = rollback["targetVersion"]
        if target_version is not None:
            if not isinstance(target_version, str) or not re.fullmatch(VERSION_PATTERN, target_version):
                raise ValueError("rollback targetVersion must be null or a valid version")
            records = self._read_store()
            if target_version not in records:
                raise ValueError("rollback targetVersion must be a registered release")
            if _version_order(target_version) >= _version_order(release_version):
                raise ValueError("rollback targetVersion must be smaller than the release version")
        steps = rollback["steps"]
        if not isinstance(steps, list) or not steps:
            raise ValueError("rollback steps must be a non-empty array of strings")
        normalized = []
        for step in steps:
            if not isinstance(step, str) or "\n" in step or "\r" in step or not step.strip():
                raise ValueError("rollback steps must be non-empty single-line strings")
            normalized.append(step.strip())
        return target_version, normalized

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

    def reconcile_checklist_with_dependencies(self, version, checklist, template,
                                              base_dependencies, dependencies):
        # Read-only dependency-aware update of a same-version checklist
        # against a revised template: the new template is filtered by the
        # release's change categories and closed over every direct and
        # indirect prerequisite exactly like
        # generate_checklist_with_dependencies, then progress is aligned like
        # reconcile_checklist with the dependency graph as an additional
        # reset condition. The old graph is validated against the whole old
        # checklist and the new graph against the whole new template, items
        # no filter selects included. Nothing is written and the passed
        # objects are never mutated.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        items = self._validated_checklist(checklist, version)
        template_items = self._validated_template(template)
        # Every id in either dependency map, at either end, must reference an
        # item of its own side: the old map is checked against the complete
        # old checklist, the new map against the complete new template.
        base_prerequisites = self._validated_dependencies(
            base_dependencies, {item["id"] for item in items})
        prerequisites = self._validated_dependencies(
            dependencies, {item["id"] for item in template_items})
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        selected = self._applicable_template_items(template_items, records[version])
        selected_ids = {item["id"] for item in selected}
        # Close over every direct and indirect prerequisite of the selected
        # items; shared prerequisites are collected once. Only the member set
        # matters here: the final order always follows the template.
        closure = set(selected_ids)
        pending = list(selected_ids)
        while pending:
            node = pending.pop()
            for prerequisite in prerequisites.get(node, ()):
                if prerequisite not in closure:
                    closure.add(prerequisite)
                    pending.append(prerequisite)
        selected_by_id = {item["id"]: item for item in selected}
        # The expected definition of every final id, in template order, with
        # the uniform pending shape of dependency-generated checklists.
        expected_items = []
        for item in template_items:
            item_id = item["id"]
            if item_id not in closure:
                continue
            if item_id in selected_ids:
                expected_items.append(dict(selected_by_id[item_id]))
            else:
                expected_items.append({"id": item_id, "text": item["text"],
                                       "required": item["required"], "status": "pending"})
        # Ids match case-sensitively after trimming, without Unicode normalization.
        previous = {item["id"]: item for item in items}
        classification = {}
        for expected in expected_items:
            item_id = expected["id"]
            actual = previous.get(item_id)
            if actual is None:
                classification[item_id] = "added"
            else:
                # A redefined item returns to pending; the direct prerequisite
                # id set is part of the definition, compared order-insensitively
                # with a missing key meaning the empty set.
                old_prerequisites = set(base_prerequisites.get(item_id, ()))
                new_prerequisites = set(prerequisites.get(item_id, ()))
                if (actual["text"] != expected["text"]
                        or actual["required"] != expected["required"]
                        or old_prerequisites != new_prerequisites):
                    classification[item_id] = "reset"
                else:
                    classification[item_id] = "retained"
        # Any old item in the final scope that can reach an added or reset
        # item along the new graph is reset as well; an existing prerequisite
        # that is merely pending or blocked never triggers a reset by itself.
        # Reverse reachability from the initial triggers is already the fixed
        # point: anything reaching a propagated reset also reaches a trigger.
        triggers = {item_id for item_id, kind in classification.items()
                    if kind in ("added", "reset")}
        dependents = {}
        for node, node_prerequisites in prerequisites.items():
            for prerequisite in node_prerequisites:
                dependents.setdefault(prerequisite, []).append(node)
        reached = set()
        stack = list(triggers)
        while stack:
            node = stack.pop()
            for dependent in dependents.get(node, ()):
                if dependent not in triggers and dependent not in reached:
                    reached.add(dependent)
                    stack.append(dependent)
        for item_id in reached:
            if classification.get(item_id) == "retained":
                classification[item_id] = "reset"
        reconciled, retained, reset, added = [], [], [], []
        for expected in expected_items:
            item_id = expected["id"]
            kind = classification[item_id]
            if kind == "retained":
                reconciled.append({"id": item_id, "text": expected["text"],
                                   "required": expected["required"],
                                   "status": previous[item_id]["status"]})
                retained.append(item_id)
            else:
                reconciled.append({"id": item_id, "text": expected["text"],
                                   "required": expected["required"], "status": "pending"})
                (added if kind == "added" else reset).append(item_id)
        removed = [item["id"] for item in items if item["id"] not in closure]
        # Every final id gets a normalized array in template order; omitted
        # items and declared empty arrays both freeze to [].
        dependency_pairs = [(item["id"], list(prerequisites.get(item["id"], ())))
                            for item in expected_items]
        # includedPrerequisites lists, in template order, only the items
        # added solely because of a dependency edge.
        included_prerequisites = [item["id"] for item in expected_items
                                  if item["id"] not in selected_ids]
        return {"version": version, "items": reconciled,
                "retained": retained, "reset": reset, "added": added,
                "removed": removed,
                "dependencies": dict(dependency_pairs),
                "includedPrerequisites": included_prerequisites}

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

    def migrate_checklist_with_dependencies(self, base_version, target_version, checklist,
                                            template, base_dependencies, dependencies):
        # Read-only cross-version migration that additionally aligns the
        # dependency graphs: the new template is filtered by the target
        # release's change categories and closed over every direct and
        # indirect prerequisite exactly like
        # generate_checklist_with_dependencies, then progress is migrated
        # like migrate_checklist with the dependency graph as an additional
        # reset condition and reset propagation along the new graph. The old
        # graph is validated against the whole old checklist and the new
        # graph against the whole new template, items no filter selects
        # included. Nothing is written and the passed objects are never
        # mutated.
        for version in (base_version, target_version):
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
        # The checklist is validated against the base version it declares; the
        # template is validated in full before the target filter is applied.
        items = self._validated_checklist(checklist, base_version)
        template_items = self._validated_template(template)
        # Every id in either dependency map, at either end, must reference an
        # item of its own side: the old map is checked against the complete
        # old checklist, the new map against the complete new template.
        base_prerequisites = self._validated_dependencies(
            base_dependencies, {item["id"] for item in items})
        prerequisites = self._validated_dependencies(
            dependencies, {item["id"] for item in template_items})
        # The whole store is validated before either version is looked up.
        records = self._read_store()
        if base_version not in records:
            raise ValueError("unknown release")
        if target_version not in records:
            raise ValueError("unknown release")
        selected = self._applicable_template_items(template_items, records[target_version])
        selected_ids = {item["id"] for item in selected}
        # Close over every direct and indirect prerequisite of the selected
        # items; shared prerequisites are collected once. Only the member set
        # matters here: the final order always follows the template.
        closure = set(selected_ids)
        pending = list(selected_ids)
        while pending:
            node = pending.pop()
            for prerequisite in prerequisites.get(node, ()):
                if prerequisite not in closure:
                    closure.add(prerequisite)
                    pending.append(prerequisite)
        selected_by_id = {item["id"]: item for item in selected}
        # The expected definition of every final id, in template order, with
        # the uniform pending shape of dependency-generated checklists.
        expected_items = []
        for item in template_items:
            item_id = item["id"]
            if item_id not in closure:
                continue
            if item_id in selected_ids:
                expected_items.append(dict(selected_by_id[item_id]))
            else:
                expected_items.append({"id": item_id, "text": item["text"],
                                       "required": item["required"], "status": "pending"})
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
        # Ids match case-sensitively after trimming, without Unicode normalization.
        previous = {item["id"]: item for item in items}
        definitions = {item["id"]: item for item in template_items}
        classification = {}
        for expected in expected_items:
            item_id = expected["id"]
            actual = previous.get(item_id)
            if actual is None:
                classification[item_id] = "added"
                continue
            # A redefined item returns to pending; the direct prerequisite
            # id set is part of the definition, compared order-insensitively
            # with a missing key meaning the empty set.
            old_prerequisites = set(base_prerequisites.get(item_id, ()))
            new_prerequisites = set(prerequisites.get(item_id, ()))
            if (actual["text"] != expected["text"]
                    or actual["required"] != expected["required"]
                    or old_prerequisites != new_prerequisites):
                classification[item_id] = "reset"
                continue
            categories = definitions[item_id]["categories"]
            scope = CATEGORIES if categories is None else tuple(categories)
            if all(not changed_by_category[category] for category in scope):
                classification[item_id] = "retained"
            else:
                classification[item_id] = "reset"
        # Any old item in the final scope that can reach an added or reset
        # item along the new graph is reset as well; an existing prerequisite
        # that is merely pending or blocked never triggers a reset by itself.
        # Reverse reachability from the initial triggers is already the fixed
        # point: anything reaching a propagated reset also reaches a trigger.
        triggers = {item_id for item_id, kind in classification.items()
                    if kind in ("added", "reset")}
        dependents = {}
        for node, node_prerequisites in prerequisites.items():
            for prerequisite in node_prerequisites:
                dependents.setdefault(prerequisite, []).append(node)
        reached = set()
        stack = list(triggers)
        while stack:
            node = stack.pop()
            for dependent in dependents.get(node, ()):
                if dependent not in triggers and dependent not in reached:
                    reached.add(dependent)
                    stack.append(dependent)
        for item_id in reached:
            if classification.get(item_id) == "retained":
                classification[item_id] = "reset"
        migrated, retained, reset, added_ids = [], [], [], []
        for expected in expected_items:
            item_id = expected["id"]
            kind = classification[item_id]
            if kind == "retained":
                migrated.append({"id": item_id, "text": expected["text"],
                                 "required": expected["required"],
                                 "status": previous[item_id]["status"]})
                retained.append(item_id)
            else:
                migrated.append({"id": item_id, "text": expected["text"],
                                 "required": expected["required"], "status": "pending"})
                (added_ids if kind == "added" else reset).append(item_id)
        removed_ids = [item["id"] for item in items if item["id"] not in closure]
        # Every final id gets a normalized array in template order; omitted
        # items and declared empty arrays both freeze to [].
        dependency_pairs = [(item["id"], list(prerequisites.get(item["id"], ())))
                            for item in expected_items]
        # includedPrerequisites lists, in template order, only the items
        # added solely because of a dependency edge.
        included_prerequisites = [item["id"] for item in expected_items
                                  if item["id"] not in selected_ids]
        return {"baseVersion": base_version, "version": target_version,
                "items": migrated,
                "retained": retained, "reset": reset, "added": added_ids,
                "removed": removed_ids,
                "dependencies": dict(dependency_pairs),
                "includedPrerequisites": included_prerequisites}

    def update_checklist(self, version, updates, checklist_path):
        # Batch-update the statuses of a local checklist file after confirming
        # every expected status still matches the read bytes: one mismatch
        # rejects the whole batch. Only when at least one status changes is the
        # target replaced wholesale with UTF-8 JSON ending in a newline;
        # otherwise its bytes and modification time are left untouched.
        normalized_updates, items, path = self._prepare_checklist_update(
            version, updates, checklist_path)
        status_by_id = {item["id"]: item["status"] for item in items}
        # Ids match case-sensitively after trimming, without Unicode
        # normalization; every expected status is checked, including entries
        # whose target status would not change anything.
        for item_id, expected, _status in normalized_updates:
            actual = status_by_id.get(item_id)
            if actual is None:
                raise ValueError("update item id must exist in the checklist")
            if actual != expected:
                raise ValueError("update expected status does not match the checklist")
        updated, resulting = self._apply_checklist_updates(items, normalized_updates)
        changed = bool(updated)
        if changed:
            content = json.dumps({"version": version, "items": resulting},
                                 ensure_ascii=False, indent=2) + "\n"
            _atomic_write(path, content)
        ready = all(item["status"] == "done" for item in resulting if item["required"])
        return {"version": version, "changed": changed, "items": resulting,
                "updated": updated, "ready": ready}

    def preview_update_checklist(self, version, updates, checklist_path):
        # Read-only plan for update_checklist: the same public validation,
        # store and checklist checks and the same id matching, but unknown ids
        # and expected-status mismatches are reported as conflicts in updates
        # order instead of raised, and nothing is ever written.
        normalized_updates, items, _path = self._prepare_checklist_update(
            version, updates, checklist_path)
        status_by_id = {item["id"]: item["status"] for item in items}
        # Every expected status is checked, including entries whose target
        # status is already satisfied; conflicts keep the updates-array order.
        conflicts = []
        for item_id, expected, target in normalized_updates:
            actual = status_by_id.get(item_id)
            if actual is None:
                conflicts.append({"id": item_id, "expected": expected, "actual": None,
                                  "status": target, "reason": "unknown-id"})
            elif actual != expected:
                conflicts.append({"id": item_id, "expected": expected, "actual": actual,
                                  "status": target, "reason": "status-mismatch"})
        if conflicts:
            # A conflicting batch plans nothing: the normalized original
            # checklist is reported without any partial status changes.
            can_update = False
            resulting = [dict(item) for item in items]
            updated = []
            changed = False
        else:
            can_update = True
            updated, resulting = self._apply_checklist_updates(items, normalized_updates)
            changed = bool(updated)
        ready = all(item["status"] == "done" for item in resulting if item["required"])
        return {"version": version, "canUpdate": can_update, "changed": changed,
                "items": resulting, "updated": updated, "ready": ready,
                "conflicts": conflicts}

    def preview_merge_checklist(self, version, base, incoming, current):
        # Read-only three-way merge preview of same-version checklists: items
        # are matched by normalized id and compared as whole normalized items
        # (id, text, required, status; extra fields ignored, missing differing
        # from any present item). An incoming side equal to the base or to the
        # current side keeps the current side; otherwise a current side equal
        # to the base adopts the incoming side; anything else is one whole-item
        # conflict that keeps the current presence state and content.
        base_items, incoming_items, current_items = self._prepared_merge_inputs(
            version, base, incoming, current)
        merged, conflicts = self._merge_classification(
            base_items, incoming_items, current_items)
        items = self._ordered_merge_items(merged, incoming_items, current_items)
        self._validate_merge_items(items)
        ready = all(item["status"] == "done" for item in items if item["required"])
        # Ids sort by Unicode code point, not by locale.
        conflicts.sort(key=lambda entry: entry["id"])
        return {"version": version, "canMerge": not conflicts, "ready": ready,
                "items": items, "conflicts": conflicts}

    def resolve_merge_checklist(self, version, base, incoming, current, decisions):
        # Read-only confirmation layer over preview_merge_checklist: decisions
        # maps original preview conflict ids to "incoming", "current" or a
        # custom decision object; empty and partial maps are allowed and the
        # two shapes may be mixed. A chosen side is adopted as one whole item
        # and choosing a side that lacks the id deletes the item. A custom
        # decision replaces the whole item with its value (present true) or
        # deletes it (present false); even a value equal to one of the sides,
        # or deleting an item the current side already lacks, is recorded as
        # "custom". Unchosen conflicts keep their original details and the
        # current side.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        base_items = self._validated_checklist(base, version)
        incoming_items = self._validated_checklist(incoming, version)
        current_items = self._validated_checklist(current, version)
        choices = _validated_merge_choices(decisions)
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        merged, conflicts = self._merge_classification(
            base_items, incoming_items, current_items)
        # The original preview must itself stay a legal checklist before any
        # decision is applied.
        self._validate_merge_items(
            self._ordered_merge_items(merged, incoming_items, current_items))
        conflict_ids = {entry["id"] for entry in conflicts}
        # Keys match the normalized conflict ids exactly: case-sensitive, with
        # no trimming or Unicode normalization, and never at a non-conflict id.
        for item_id in choices:
            if item_id not in conflict_ids:
                raise ValueError("decision id is not a conflict id")
        # Apply the confirmed decisions over the preview plan. A side lacking
        # the id deletes the item; a custom value replaces the whole item and
        # a present-false custom decision deletes it. Building from deep copies
        # keeps every passed object untouched, and confirming an unchanged
        # current side still counts as resolved.
        incoming_by_id = {item["id"]: item for item in incoming_items}
        current_by_id = {item["id"]: item for item in current_items}
        resolved, chosen_by_id = [], {}
        for item_id, choice in choices.items():
            if isinstance(choice, str):
                side = incoming_by_id if choice == "incoming" else current_by_id
                merged[item_id] = copy.deepcopy(side.get(item_id))
                chosen_by_id[item_id] = choice
                resolved.append({"id": item_id, "choice": choice})
            else:
                # The custom value replaces the whole item, even when it
                # equals one of the sides or only changes its presence.
                merged[item_id] = copy.deepcopy(choice["value"]) if choice["present"] else None
                chosen_by_id[item_id] = "custom"
                resolved.append({"id": item_id, "choice": "custom"})
        items = self._ordered_merge_items(merged, incoming_items, current_items)
        self._validate_merge_items(items)
        remaining = [entry for entry in conflicts if entry["id"] not in chosen_by_id]
        ready = all(item["status"] == "done" for item in items if item["required"])
        # Ids sort by Unicode code point, not by locale.
        remaining.sort(key=lambda entry: entry["id"])
        resolved.sort(key=lambda entry: entry["id"])
        return {"version": version, "canMerge": not remaining, "ready": ready,
                "items": items, "conflicts": remaining, "resolved": resolved}

    def apply_merge_checklist(self, version, base, incoming, expected, decisions,
                              current_path):
        # Confirmed three-way checklist merge written back to a local file:
        # the target is read, checked against the expected snapshot, resolved
        # exactly like resolve_merge_checklist (string and custom decisions
        # mixed, partially confirmed or not) and, only when the normalized
        # item sequence changes, replaced wholesale with UTF-8 JSON holding
        # just version and items and ending in a newline. Any remaining
        # conflict rejects the save; nothing else is modified.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        base_items = self._validated_checklist(base, version)
        incoming_items = self._validated_checklist(incoming, version)
        expected_items = self._validated_checklist(expected, version)
        choices = _validated_merge_choices(decisions)
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        path = Path(current_path)
        if path.is_symlink():
            raise ValueError("checklist file must not be a symbolic link")
        if _same_file(self.path, path):
            raise ValueError("checklist file must not be the same file as the store")
        # A missing target or an unreadable file raises OSError unchanged, and
        # no missing file or directory is ever created.
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
        try:
            payload = _loads_unique(text)
        except json.JSONDecodeError as exc:
            raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
        current_items = self._validated_checklist(payload, version)
        # The snapshot matches on the declared version and the normalized item
        # sequence: extra fields, object key order and trimmable whitespace
        # are ignored, item order is not.
        if current_items != expected_items:
            raise ValueError("current checklist does not match the expected checklist")
        merged, conflicts = self._merge_classification(
            base_items, incoming_items, current_items)
        # The original preview must itself stay a legal checklist before any
        # decision is applied.
        self._validate_merge_items(
            self._ordered_merge_items(merged, incoming_items, current_items))
        conflict_ids = {entry["id"] for entry in conflicts}
        # Keys match the normalized conflict ids exactly: case-sensitive, with
        # no trimming or Unicode normalization, and never at a non-conflict id.
        for item_id in choices:
            if item_id not in conflict_ids:
                raise ValueError("decision id is not a conflict id")
        # Apply the confirmed decisions over the preview plan, building from
        # deep copies so every passed object stays untouched. String choices
        # adopt one whole side; a custom choice replaces the whole item or
        # deletes it and records "custom" even when its value matches a side.
        incoming_by_id = {item["id"]: item for item in incoming_items}
        current_by_id = {item["id"]: item for item in current_items}
        resolved = []
        for item_id, choice in choices.items():
            if isinstance(choice, str):
                side = incoming_by_id if choice == "incoming" else current_by_id
                merged[item_id] = copy.deepcopy(side.get(item_id))
                resolved.append({"id": item_id, "choice": choice})
            else:
                merged[item_id] = copy.deepcopy(choice["value"]) if choice["present"] else None
                resolved.append({"id": item_id, "choice": "custom"})
        items = self._ordered_merge_items(merged, incoming_items, current_items)
        self._validate_merge_items(items)
        if any(entry["id"] not in choices for entry in conflicts):
            raise ValueError("unresolved conflicts remain")
        # Ids sort by Unicode code point, not by locale.
        resolved.sort(key=lambda entry: entry["id"])
        # Readiness never gates the save: a not-ready merge still writes.
        ready = all(item["status"] == "done" for item in items if item["required"])
        changed = items != current_items
        if changed:
            content = json.dumps({"version": version, "items": items},
                                 ensure_ascii=False, indent=2) + "\n"
            _atomic_write(path, content)
        return {"version": version, "changed": changed, "items": items,
                "resolved": resolved, "ready": ready}

    def _prepared_merge_inputs(self, version, base, incoming, current):
        # Shared version, checklist and store checks for the checklist merges.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        base_items = self._validated_checklist(base, version)
        incoming_items = self._validated_checklist(incoming, version)
        current_items = self._validated_checklist(current, version)
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        return base_items, incoming_items, current_items

    @staticmethod
    def _merge_classification(base_items, incoming_items, current_items):
        # Whole-item three-way classification shared by the preview and its
        # confirmation. merged maps every id to the kept item or None for a
        # planned deletion; conflicts lists one original-detail entry per
        # divergent id, and merged/conflict entries are detached copies.
        base_by_id = {item["id"]: item for item in base_items}
        incoming_by_id = {item["id"]: item for item in incoming_items}
        current_by_id = {item["id"]: item for item in current_items}
        merged, conflicts = {}, []
        for item_id in base_by_id.keys() | incoming_by_id.keys() | current_by_id.keys():
            base_item = base_by_id.get(item_id)
            incoming_item = incoming_by_id.get(item_id)
            current_item = current_by_id.get(item_id)
            if incoming_item == base_item or current_item == incoming_item:
                # The plan changes nothing, or the current side already
                # matches the incoming side: keep the current side.
                merged[item_id] = current_item
            elif current_item == base_item:
                # The current side matches the base: adopt the incoming side,
                # including whole-item additions and deletions.
                merged[item_id] = incoming_item
            else:
                # Divergent whole items: one conflict per id, keeping the
                # current presence state and content.
                conflicts.append({"id": item_id,
                                  "base": dict(base_item) if base_item is not None else None,
                                  "incoming": dict(incoming_item) if incoming_item is not None else None,
                                  "current": dict(current_item) if current_item is not None else None})
                merged[item_id] = current_item
        return merged, conflicts

    @staticmethod
    def _ordered_merge_items(merged, incoming_items, current_items):
        # Items keep the current-side order, then items the current side lacks
        # follow in incoming-side order; deleted ids are omitted.
        current_by_id = {item["id"]: item for item in current_items}
        items = []
        for item in current_items:
            kept = merged[item["id"]]
            if kept is not None:
                items.append(dict(kept))
        for item in incoming_items:
            item_id = item["id"]
            if item_id not in current_by_id:
                kept = merged[item_id]
                if kept is not None:
                    items.append(dict(kept))
        return items

    @staticmethod
    def _validate_merge_items(items):
        # The merged checklist must stay a legal checklist, conflicts or not.
        if not items:
            raise ValueError("checklist requires at least one item")
        if not any(item["required"] for item in items):
            raise ValueError("checklist requires at least one required item")

    def _prepare_checklist_update(self, version, updates, checklist_path):
        # Shared validation and reading for update_checklist and its preview:
        # version, updates batch, whole store, registration, target path rules
        # and the read checklist all follow the identical rules and order.
        if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
            raise ValueError("version must have three nonnegative numeric components")
        normalized_updates = self._validated_updates(updates)
        # The whole store is validated before the version is looked up; a
        # missing store is treated as empty and then reports it as unknown.
        records = self._read_store()
        if version not in records:
            raise ValueError("unknown release")
        path = Path(checklist_path)
        if path.is_symlink():
            raise ValueError("checklist file must not be a symbolic link")
        if _same_file(self.path, path):
            raise ValueError("checklist file must not be the same file as the store")
        # A missing target or an unreadable file raises OSError unchanged, and
        # no missing file or directory is ever created.
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
        try:
            payload = _loads_unique(text)
        except json.JSONDecodeError as exc:
            raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
        items = self._validated_checklist(payload, version)
        return normalized_updates, items, path

    @staticmethod
    def _apply_checklist_updates(items, normalized_updates):
        # Apply validated, expected-confirmed updates to normalized checklist
        # items, building fresh dicts: updated ids and items follow checklist
        # order, and only statuses of matched items change.
        updated, resulting = [], []
        targets = {item_id: status for item_id, _expected, status in normalized_updates}
        for item in items:
            item_id = item["id"]
            if item_id in targets and targets[item_id] != item["status"]:
                updated.append(item_id)
                resulting.append({"id": item["id"], "text": item["text"],
                                  "required": item["required"], "status": targets[item_id]})
            else:
                resulting.append(dict(item))
        return updated, resulting

    @staticmethod
    def _validated_updates(updates):
        # Validate the status update batch: an array (possibly empty) of
        # objects holding exactly id, expected and status. Ids follow the
        # single-line checklist id rule; expected/status are known statuses and
        # any status may switch to any other. Normalized ids must be unique.
        if not isinstance(updates, list):
            raise ValueError("checklist updates must be an array")
        normalized = []
        seen = set()
        for update in updates:
            if not isinstance(update, dict):
                raise ValueError("checklist updates require id, expected and status")
            if set(update) != {"id", "expected", "status"}:
                raise ValueError("checklist updates require exactly id, expected and status")
            item_id, expected, status = update["id"], update["expected"], update["status"]
            if not isinstance(item_id, str) or not item_id.strip() or "\n" in item_id or "\r" in item_id:
                raise ValueError("checklist item id must be a non-empty single-line string")
            if expected not in CHECK_STATUSES or status not in CHECK_STATUSES:
                raise ValueError("checklist item status must be done, pending or blocked")
            item_id = item_id.strip()
            # Case-sensitive, no trimming beyond the ends, no Unicode normalization.
            if item_id in seen:
                raise ValueError("checklist update id must be unique")
            seen.add(item_id)
            normalized.append((item_id, expected, status))
        return normalized

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
    def _validated_checklist_item(item, *, strict=False):
        # Validate and normalize one checklist item: id, text, required and
        # status follow the same rules everywhere, trimming id and text ends.
        # strict additionally rejects missing or extra fields, which a custom
        # decision value must never carry (a declared checklist may keep
        # unknown fields that normalization ignores).
        if not isinstance(item, dict):
            raise ValueError("checklist items require id, text, required and status")
        if strict and set(item) != {"id", "text", "required", "status"}:
            raise ValueError(
                "custom checklist value requires exactly id, text, required and status")
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
        return {"id": item_id, "text": text.strip(),
                "required": required, "status": status}

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
            entry = ReleaseDesk._validated_checklist_item(item)
            item_id = entry["id"]
            if item_id in seen:
                raise ValueError("checklist item id must be unique")
            seen.add(item_id)
            normalized.append(entry)
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
        # Applying a confirmed plan to a local configuration file: the file is
        # read, checked against the expected snapshot, resolved exactly like
        # resolve_config and, only when the result differs, replaced wholesale
        # with UTF-8 JSON ending in a newline. Nothing else is modified.
        for version in (base_version, target_version):
            if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
                raise ValueError("version must have three nonnegative numeric components")
        _validated_config(base_config)
        _validated_config(target_config)
        _validated_config(expected_config)
        choices = _validated_decisions(decisions)
        # The whole store is validated before either version is looked up.
        records = self._read_store()
        if base_version not in records:
            raise ValueError("unknown release")
        if target_version not in records:
            raise ValueError("unknown release")
        path = Path(current_path)
        if path.is_symlink():
            raise ValueError("current configuration file must not be a symbolic link")
        if _same_file(self.path, path):
            raise ValueError("current configuration file must not be the same file as the store")
        # A missing target or an unreadable file raises OSError unchanged.
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("current configuration file must contain UTF-8 encoded JSON") from exc
        try:
            current_config = _loads_unique(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("current configuration file must contain UTF-8 encoded JSON") from exc
        _validated_config(current_config)
        if not _json_values_equal(expected_config, current_config):
            raise ValueError("current configuration does not match the expected configuration")
        conflicts = []
        _preview_config_values(base_config, target_config, current_config, "", conflicts)
        conflict_paths = {entry["path"] for entry in conflicts}
        for decision_path in choices:
            if decision_path not in conflict_paths:
                raise ValueError("decision path is not a conflict path")
        resolved = []
        config = _resolve_config_values(
            base_config, target_config, current_config, "", choices, resolved)
        chosen_paths = {entry["path"] for entry in resolved}
        if any(entry["path"] not in chosen_paths for entry in conflicts):
            raise ValueError("unresolved conflicts remain")
        # Paths sort by Unicode code point, not by locale.
        resolved.sort(key=lambda entry: entry["path"])
        changed = not _json_values_equal(config, current_config)
        if changed:
            content = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
            _atomic_write(path, content)
        return {"baseVersion": base_version, "targetVersion": target_version,
                "changed": changed, "config": config, "resolved": resolved}


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
    check_dependencies = commands.add_parser("check-dependencies")
    check_dependencies.add_argument("version")
    check_dependencies.add_argument("checklist")
    check_dependencies.add_argument("dependencies")
    explain_dependencies = commands.add_parser("explain-dependencies")
    explain_dependencies.add_argument("version")
    explain_dependencies.add_argument("checklist")
    explain_dependencies.add_argument("dependencies")
    plan_dependencies = commands.add_parser("plan-dependencies")
    plan_dependencies.add_argument("version")
    plan_dependencies.add_argument("checklist")
    plan_dependencies.add_argument("dependencies")
    make_checklist = commands.add_parser("make-checklist")
    make_checklist.add_argument("version")
    make_checklist.add_argument("file")
    make_checklist.add_argument("--dependencies")
    audit = commands.add_parser("audit-checklist")
    audit.add_argument("version")
    audit.add_argument("checklist")
    audit.add_argument("template")
    audit.add_argument("--dependencies")
    record_release = commands.add_parser("record-release")
    record_release.add_argument("version")
    record_release.add_argument("checklist")
    record_release.add_argument("template")
    record_release.add_argument("rollback")
    record_release.add_argument("--output")
    record_release.add_argument("--dependencies")
    reconcile = commands.add_parser("reconcile-checklist")
    reconcile.add_argument("version")
    reconcile.add_argument("checklist")
    reconcile.add_argument("template")
    reconcile.add_argument("--base-dependencies", dest="base_dependencies")
    reconcile.add_argument("--dependencies")
    migrate = commands.add_parser("migrate-checklist")
    migrate.add_argument("base_version")
    migrate.add_argument("target_version")
    migrate.add_argument("checklist")
    migrate.add_argument("template")
    migrate.add_argument("--base-dependencies", dest="base_dependencies")
    migrate.add_argument("--dependencies")
    update_checklist = commands.add_parser("update-checklist")
    update_checklist.add_argument("version")
    update_checklist.add_argument("updates")
    update_checklist.add_argument("checklist")
    update_checklist.add_argument("--dry-run", action="store_true", dest="dry_run")
    merge_checklist = commands.add_parser("merge-checklist")
    merge_checklist.add_argument("version")
    merge_checklist.add_argument("base")
    merge_checklist.add_argument("incoming")
    merge_checklist.add_argument("current")
    resolve_merge_checklist = commands.add_parser("resolve-merge-checklist")
    resolve_merge_checklist.add_argument("version")
    resolve_merge_checklist.add_argument("base")
    resolve_merge_checklist.add_argument("incoming")
    resolve_merge_checklist.add_argument("current")
    resolve_merge_checklist.add_argument("decisions")
    apply_merge_checklist = commands.add_parser("apply-merge-checklist")
    apply_merge_checklist.add_argument("version")
    apply_merge_checklist.add_argument("base")
    apply_merge_checklist.add_argument("incoming")
    apply_merge_checklist.add_argument("expected")
    apply_merge_checklist.add_argument("decisions")
    apply_merge_checklist.add_argument("current")
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
                current_path = Path(args.current_config)
                for label, other in (("base configuration", args.base_config),
                                     ("target configuration", args.target_config),
                                     ("expected configuration", args.expected_config),
                                     ("decisions", args.decisions)):
                    if _same_file(Path(other), current_path):
                        raise ValueError(
                            f"current configuration file must not be the same file as the {label} file")
                result = desk.apply_config(args.base_version, args.target_version,
                                           base_payload, target_payload, expected_payload,
                                           decisions_payload, current_path)
            elif args.command == "check":
                try:
                    payload = _loads_unique(Path(args.file).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("check file must contain UTF-8 encoded JSON") from exc
                result = desk.checklist(args.version, payload)
            elif args.command == "check-dependencies":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    dependencies_payload = _loads_unique(Path(args.dependencies).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("dependencies file must contain UTF-8 encoded JSON") from exc
                result = desk.check_dependencies(
                    args.version, checklist_payload, dependencies_payload)
            elif args.command == "explain-dependencies":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    dependencies_payload = _loads_unique(Path(args.dependencies).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("dependencies file must contain UTF-8 encoded JSON") from exc
                result = desk.explain_dependencies(
                    args.version, checklist_payload, dependencies_payload)
            elif args.command == "plan-dependencies":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    dependencies_payload = _loads_unique(Path(args.dependencies).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("dependencies file must contain UTF-8 encoded JSON") from exc
                result = desk.plan_dependencies(
                    args.version, checklist_payload, dependencies_payload)
            elif args.command == "make-checklist":
                try:
                    payload = _loads_unique(Path(args.file).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                if args.dependencies is None:
                    result = desk.generate_checklist(args.version, payload)
                else:
                    try:
                        dependencies_payload = _loads_unique(
                            Path(args.dependencies).read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(
                            "dependencies file must contain UTF-8 encoded JSON") from exc
                    result = desk.generate_checklist_with_dependencies(
                        args.version, payload, dependencies_payload)
            elif args.command == "audit-checklist":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    template_payload = _loads_unique(Path(args.template).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                if args.dependencies is None:
                    result = desk.audit_checklist(args.version, checklist_payload, template_payload)
                else:
                    try:
                        dependencies_payload = _loads_unique(
                            Path(args.dependencies).read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(
                            "dependencies file must contain UTF-8 encoded JSON") from exc
                    result = desk.audit_checklist_with_dependencies(
                        args.version, checklist_payload, template_payload, dependencies_payload)
            elif args.command == "record-release":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    template_payload = _loads_unique(Path(args.template).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                try:
                    rollback_payload = _loads_unique(Path(args.rollback).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("rollback file must contain UTF-8 encoded JSON") from exc
                if args.dependencies is None:
                    result = desk.release_record(args.version, checklist_payload,
                                                 template_payload, rollback_payload)
                else:
                    try:
                        dependencies_payload = _loads_unique(
                            Path(args.dependencies).read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(
                            "dependencies file must contain UTF-8 encoded JSON") from exc
                    result = desk.release_record_with_dependencies(
                        args.version, checklist_payload, template_payload,
                        rollback_payload, dependencies_payload)
                if args.output:
                    output_path = Path(args.output)
                    # The snapshot target must be a brand-new path: an existing
                    # name or symlink (dangling ones included) is invalid input.
                    if output_path.exists() or output_path.is_symlink():
                        raise ValueError("release record output must not already exist")
                    inputs = [("store", desk.path),
                              ("checklist", Path(args.checklist)),
                              ("template", Path(args.template)),
                              ("rollback", Path(args.rollback))]
                    if args.dependencies is not None:
                        inputs.append(("dependencies", Path(args.dependencies)))
                    for label, other in inputs:
                        if _same_file(output_path, other):
                            raise ValueError(
                                f"release record output must not be the same file as the {label} file")
                    _exclusive_write(output_path, json.dumps(result, ensure_ascii=False) + "\n")
            elif args.command == "reconcile-checklist":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    template_payload = _loads_unique(Path(args.template).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                if args.base_dependencies is None and args.dependencies is None:
                    result = desk.reconcile_checklist(args.version, checklist_payload, template_payload)
                elif args.base_dependencies is None or args.dependencies is None:
                    raise ValueError(
                        "reconcile-checklist requires --base-dependencies and --dependencies together")
                else:
                    try:
                        base_dependencies_payload = _loads_unique(
                            Path(args.base_dependencies).read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(
                            "base dependencies file must contain UTF-8 encoded JSON") from exc
                    try:
                        dependencies_payload = _loads_unique(
                            Path(args.dependencies).read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(
                            "dependencies file must contain UTF-8 encoded JSON") from exc
                    result = desk.reconcile_checklist_with_dependencies(
                        args.version, checklist_payload, template_payload,
                        base_dependencies_payload, dependencies_payload)
            elif args.command == "migrate-checklist":
                try:
                    checklist_payload = _loads_unique(Path(args.checklist).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    template_payload = _loads_unique(Path(args.template).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("template file must contain UTF-8 encoded JSON") from exc
                if args.base_dependencies is None and args.dependencies is None:
                    result = desk.migrate_checklist(args.base_version, args.target_version,
                                                    checklist_payload, template_payload)
                elif args.base_dependencies is None or args.dependencies is None:
                    raise ValueError(
                        "migrate-checklist requires --base-dependencies and --dependencies together")
                else:
                    try:
                        base_dependencies_payload = _loads_unique(
                            Path(args.base_dependencies).read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(
                            "base dependencies file must contain UTF-8 encoded JSON") from exc
                    try:
                        dependencies_payload = _loads_unique(
                            Path(args.dependencies).read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError(
                            "dependencies file must contain UTF-8 encoded JSON") from exc
                    result = desk.migrate_checklist_with_dependencies(
                        args.base_version, args.target_version,
                        checklist_payload, template_payload,
                        base_dependencies_payload, dependencies_payload)
            elif args.command == "update-checklist":
                try:
                    updates_payload = _loads_unique(Path(args.updates).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("updates file must contain UTF-8 encoded JSON") from exc
                checklist_path = Path(args.checklist)
                if _same_file(Path(args.updates), checklist_path):
                    raise ValueError(
                        "checklist file must not be the same file as the updates file")
                if args.dry_run:
                    result = desk.preview_update_checklist(
                        args.version, updates_payload, checklist_path)
                else:
                    result = desk.update_checklist(
                        args.version, updates_payload, checklist_path)
            elif args.command == "merge-checklist":
                try:
                    base_payload = _loads_unique(Path(args.base).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("base checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    incoming_payload = _loads_unique(Path(args.incoming).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("incoming checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    current_payload = _loads_unique(Path(args.current).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("current checklist file must contain UTF-8 encoded JSON") from exc
                result = desk.preview_merge_checklist(args.version, base_payload,
                                                      incoming_payload, current_payload)
            elif args.command == "resolve-merge-checklist":
                try:
                    base_payload = _loads_unique(Path(args.base).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("base checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    incoming_payload = _loads_unique(Path(args.incoming).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("incoming checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    current_payload = _loads_unique(Path(args.current).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("current checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    decisions_payload = _loads_unique(Path(args.decisions).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("decisions file must contain UTF-8 encoded JSON") from exc
                result = desk.resolve_merge_checklist(
                    args.version, base_payload, incoming_payload, current_payload,
                    decisions_payload)
            elif args.command == "apply-merge-checklist":
                try:
                    base_payload = _loads_unique(Path(args.base).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("base checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    incoming_payload = _loads_unique(Path(args.incoming).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("incoming checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    expected_payload = _loads_unique(Path(args.expected).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("expected checklist file must contain UTF-8 encoded JSON") from exc
                try:
                    decisions_payload = _loads_unique(Path(args.decisions).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("decisions file must contain UTF-8 encoded JSON") from exc
                current_path = Path(args.current)
                for label, other in (("base checklist", args.base),
                                     ("incoming checklist", args.incoming),
                                     ("expected checklist", args.expected),
                                     ("decisions", args.decisions)):
                    if _same_file(Path(other), current_path):
                        raise ValueError(
                            f"current checklist file must not be the same file as the {label} file")
                result = desk.apply_merge_checklist(
                    args.version, base_payload, incoming_payload, expected_payload,
                    decisions_payload, current_path)
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
