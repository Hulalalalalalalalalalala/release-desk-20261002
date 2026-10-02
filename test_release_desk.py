import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from release_desk import ReleaseDesk

ROOT = Path(__file__).resolve().parent


class ReleaseDeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)

    def test_grouped_notes_and_numeric_version_order(self):
        changes = [{"category": "Fixed", "text": "Retry empty exports"}, {"category": "Added", "text": "Export receipts"}]
        self.desk.add("1.10.0", changes)
        self.desk.add("1.2.0", [{"category": "Changed", "text": "Shorter output"}])
        self.assertEqual(self.desk.versions(), ["1.2.0", "1.10.0"])
        self.assertEqual(self.desk.notes("1.10.0"), "# 1.10.0\n\n## Added\n- Export receipts\n\n## Fixed\n- Retry empty exports\n")

    def test_invalid_version_category_and_multiline(self):
        for version, changes in (("v1", [{"category": "Added", "text": "x"}]), ("1.0.0", [{"category": "Other", "text": "x"}]), ("1.0.0", [{"category": "Fixed", "text": "x\ny"}])):
            with self.assertRaises(ValueError):
                self.desk.add(version, changes)
        self.assertFalse(self.path.exists())

    def test_duplicate_and_unknown_release(self):
        changes = [{"category": "Added", "text": "First release"}]
        self.desk.add("1.0.0", changes)
        with self.assertRaises(ValueError):
            self.desk.add("1.0.0", changes)
        with self.assertRaises(ValueError):
            self.desk.notes("2.0.0")

    def test_cli_add_and_render(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        added = subprocess.run(prefix + ["add", "1.2.0", str(ROOT / "samples/next-changes.json")], capture_output=True, text=True)
        self.assertEqual(added.returncode, 0, added.stderr)
        notes = subprocess.run(prefix + ["notes", "1.2.0"], capture_output=True, text=True)
        self.assertIn("## Fixed", notes.stdout)
        self.assertEqual(subprocess.run(prefix + ["notes", "9.0.0"], capture_output=True).returncode, 2)

    def test_diff_added_removed_unchanged(self):
        self.desk.add("1.0.0", [
            {"category": "Added", "text": "Export receipts"},
            {"category": "Fixed", "text": "Retry empty exports"},
            {"category": "Fixed", "text": "Trim titles"},
        ])
        self.desk.add("1.1.0", [
            {"category": "Fixed", "text": " Trim titles "},
            {"category": "Added", "text": "Group changes"},
            {"category": "Changed", "text": "Shorter output"},
        ])
        self.assertEqual(self.desk.diff("1.0.0", "1.1.0"), {
            "baseVersion": "1.0.0",
            "targetVersion": "1.1.0",
            "added": [{"category": "Added", "text": "Group changes"}, {"category": "Changed", "text": "Shorter output"}],
            "removed": [{"category": "Added", "text": "Export receipts"}, {"category": "Fixed", "text": "Retry empty exports"}],
            "unchanged": [{"category": "Fixed", "text": "Trim titles"}],
        })

    def test_diff_duplicates_match_by_occurrence(self):
        self.desk.add("1.0.0", [
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Other"},
        ])
        self.desk.add("1.1.0", [
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Retry"},
        ])
        result = self.desk.diff("1.0.0", "1.1.0")
        self.assertEqual(result["added"], [{"category": "Fixed", "text": "Retry"}])
        self.assertEqual(result["removed"], [{"category": "Fixed", "text": "Other"}])
        self.assertEqual(result["unchanged"], [{"category": "Fixed", "text": "Retry"}] * 2)

    def test_diff_same_version_and_reverse_order(self):
        changes = [{"category": "Added", "text": "One"}, {"category": "Fixed", "text": "Two"}]
        self.desk.add("1.0.0", changes)
        self.desk.add("2.0.0", [{"category": "Added", "text": "Three"}])
        same = self.desk.diff("1.0.0", "1.0.0")
        self.assertEqual(same["added"], [])
        self.assertEqual(same["removed"], [])
        self.assertEqual(same["unchanged"], changes)
        reverse = self.desk.diff("2.0.0", "1.0.0")
        self.assertEqual(reverse["added"], changes)
        self.assertEqual(reverse["removed"], [{"category": "Added", "text": "Three"}])

    def test_diff_validation_errors(self):
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        for base, target in ((None, "1.0.0"), ("1.0.0", "v2"), ("1.0.0", "9.9.9")):
            with self.assertRaises(ValueError):
                self.desk.diff(base, target)
        self.path.write_text(json.dumps({"1.0.0": [{"category": "Added", "text": "One"}], "2.0.0": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.desk.diff("1.0.0", "2.0.0")
        self.path.write_text(json.dumps({"1.0.0": [{"category": "Added", "text": "One"}], "2.0.0": [{"category": "Other", "text": "x"}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.desk.diff("1.0.0", "2.0.0")
        self.path.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.desk.diff("1.0.0", "2.0.0")
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            self.desk.diff("1.0.0", "2.0.0")

    def test_cli_diff(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        changes = ROOT / "samples/next-changes.json"
        self.assertEqual(subprocess.run(prefix + ["add", "1.0.0", str(changes)], capture_output=True).returncode, 0)
        result = subprocess.run(prefix + ["diff", "1.0.0", "1.0.0"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["baseVersion"], "1.0.0")
        self.assertEqual(payload["added"], [])
        self.assertEqual(len(payload["unchanged"]), 2)
        failed = subprocess.run(prefix + ["diff", "1.0.0", "9.0.0"], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stdout))

    def test_import_new_skipped_and_numeric_order(self):
        self.desk.add("1.1.0", [{"category": "Added", "text": "Existing"}])
        payload = {
            "1.10.0": [{"category": "Fixed", "text": "Ten"}],
            "1.2.0": [{"category": "Added", "text": "  New two  ", "extra": "ignored"}, {"category": "Added", "text": "Tëßt ✓"}],
            "1.1.0": [{"category": "Added", "text": " Existing "}],
        }
        result = self.desk.import_releases(payload)
        self.assertEqual(result, {"imported": ["1.2.0", "1.10.0"], "skipped": ["1.1.0"]})
        records = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(records["1.2.0"], [{"category": "Added", "text": "New two"}, {"category": "Added", "text": "Tëßt ✓"}])
        self.assertEqual(records["1.1.0"], [{"category": "Added", "text": "Existing"}])
        self.assertEqual(self.desk.versions(), ["1.1.0", "1.2.0", "1.10.0"])

    def test_import_keeps_duplicates_and_order(self):
        result = self.desk.import_releases({"1.0.0": [
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Retry"},
            {"category": "Added", "text": "Uni"},
        ]})
        self.assertEqual(result["imported"], ["1.0.0"])
        again = self.desk.import_releases({"1.0.0": [
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Retry"},
            {"category": "Added", "text": "Uni"},
        ]})
        self.assertEqual(again, {"imported": [], "skipped": ["1.0.0"]})

    def test_import_empty_object_creates_nothing(self):
        self.assertEqual(self.desk.import_releases({}), {"imported": [], "skipped": []})
        self.assertFalse(self.path.exists())
        self.desk.add("1.0.0", [{"category": "Added", "text": "x"}])
        before = self.path.read_bytes()
        self.assertEqual(self.desk.import_releases({}), {"imported": [], "skipped": []})
        self.assertEqual(self.path.read_bytes(), before)

    def test_import_conflicts_abort_entire_batch(self):
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}, {"category": "Fixed", "text": "Two"}])
        before = self.path.read_bytes()
        good = {"2.0.0": [{"category": "Added", "text": "New"}]}
        conflicts = [
            {"1.0.0": [{"category": "Added", "text": "one"}, {"category": "Fixed", "text": "Two"}]},
            {"1.0.0": [{"category": "Fixed", "text": "Two"}, {"category": "Added", "text": "One"}]},
            {"1.0.0": [{"category": "Added", "text": "One"}]},
            {"1.0.0": [{"category": "Changed", "text": "One"}, {"category": "Fixed", "text": "Two"}]},
            {"1.0.0": [{"category": "Added", "text": "One"}, {"category": "Fixed", "text": "Twox"}], **good},
        ]
        for payload in conflicts:
            with self.assertRaises(ValueError):
                self.desk.import_releases(payload)
            self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn("2.0.0", self.desk.releases())

    def test_import_all_skipped_does_not_rewrite(self):
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        before = self.path.read_bytes()
        original_mtime = self.path.stat().st_mtime_ns
        result = self.desk.import_releases({"1.0.0": [{"category": "Added", "text": "One"}]})
        self.assertEqual(result["skipped"], ["1.0.0"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, original_mtime)

    def test_import_payload_validation(self):
        invalid_payloads = [
            [{"1.0.0": [{"category": "Added", "text": "x"}]}],
            {"v1": [{"category": "Added", "text": "x"}]},
            {"1.0.0": []},
            {"1.0.0": {}},
            {"1.0.0": [{"category": "Added", "text": "  "}]},
            {"1.0.0": [{"category": "Added", "text": "a\nb"}]},
            {"1.0.0": [{"category": "Other", "text": "x"}]},
            {"1.0.0": ["x"]},
            {"1.0.0": [{"text": "x"}]},
            {1: [{"category": "Added", "text": "x"}]},
        ]
        for payload in invalid_payloads:
            with self.assertRaises(ValueError):
                self.desk.import_releases(payload)
            self.assertFalse(self.path.exists())

    def test_store_duplicate_keys_rejected_everywhere(self):
        raw = ('{"1.0.0": [{"category": "Added", "text": "One"}],'
               ' "1.0.0": [{"category": "Added", "text": "One"}]}').encode()
        self.path.write_bytes(raw)
        actions = [
            self.desk.versions,
            self.desk.releases,
            lambda: self.desk.notes("1.0.0"),
            lambda: self.desk.diff("1.0.0", "1.0.0"),
            lambda: self.desk.add("2.0.0", [{"category": "Added", "text": "New"}]),
            lambda: self.desk.import_releases({"2.0.0": [{"category": "Added", "text": "New"}]}),
        ]
        for action in actions:
            with self.assertRaises(ValueError) as caught:
                action()
            self.assertEqual(str(caught.exception), "duplicate JSON object key")
        self.assertEqual(self.path.read_bytes(), raw)

    def test_store_duplicate_keys_nested_and_unqueried(self):
        cases = [
            '{"1.0.0": [{"category": "Added", "category": "Added", "text": "One"}]}',
            '{"1.0.0": [{"category": "Added", "text": "One", "extra": 1, "extra": 1}]}',
            '{"1.0.0": [{"category": "Added", "text": "One"}],'
            ' "2.0.0": [{"category": "Added", "text": "Two", "text": "Two"}]}',
            '{"1.0.0": [{"category": "Added", "t\\u0065xt": "One", "text": "One"}]}',
        ]
        for raw in cases:
            self.path.write_text(raw, encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                self.desk.notes("1.0.0")
            self.assertEqual(str(caught.exception), "duplicate JSON object key")
            self.assertEqual(self.path.read_text(encoding="utf-8"), raw)

    def test_store_keys_decoded_exactly(self):
        # Case differs: not a duplicate. Same key in sibling objects: fine.
        # Quotes and field-like text inside string values: no false positive.
        self.path.write_text(
            '{"1.0.0": [{"category": "Added", "text": "One", "Text": "Two"},'
            ' {"category": "Fixed", "text": "say \\"text\\": {\\"text\\": 1}"}]}',
            encoding="utf-8")
        self.assertEqual(self.desk.versions(), ["1.0.0"])
        notes = self.desk.notes("1.0.0")
        self.assertIn('- say "text": {"text": 1}', notes)

    def test_cli_duplicate_keys_in_changes_file(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        changes = Path(self.temp.name) / "changes.json"
        changes.write_text('[{"category": "Added", "text": "One", "text": "One"}]', encoding="utf-8")
        failed = subprocess.run(prefix + ["add", "1.0.0", str(changes)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout), {"error": "duplicate JSON object key"})
        self.assertFalse(self.path.exists())

    def test_cli_duplicate_keys_create_nothing(self):
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(store)]
        batch = Path(self.temp.name) / "batch.json"
        batch.write_text('{"1.0.0": [{"category": "Added", "text": "One"}],'
                         ' "1.0.0": [{"category": "Added", "text": "One"}]}', encoding="utf-8")
        failed = subprocess.run(prefix + ["import", str(batch)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout), {"error": "duplicate JSON object key"})
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())

    def test_cli_duplicate_keys_leave_store_untouched(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        before = self.path.read_bytes()
        batch = Path(self.temp.name) / "batch.json"
        batch.write_text('{"1.0.0": [{"category": "Added", "text": "One", "extra": 1, "extra": 2}],'
                         ' "2.0.0": [{"category": "Added", "text": "New"}]}', encoding="utf-8")
        failed = subprocess.run(prefix + ["import", str(batch)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout), {"error": "duplicate JSON object key"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.desk.versions(), ["1.0.0"])

    def test_cli_identical_change_entries_still_legal(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        changes = Path(self.temp.name) / "changes.json"
        changes.write_text('[{"category": "Fixed", "text": "Retry"},'
                           ' {"category": "Fixed", "text": "Retry"}]', encoding="utf-8")
        added = subprocess.run(prefix + ["add", "1.0.0", str(changes)], capture_output=True, text=True)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(json.loads(added.stdout), {"version": "1.0.0", "changes": 2})

    def test_import_invalid_target_store(self):
        self.desk.add("2.0.0", [{"category": "Added", "text": "Keep"}])
        payload = {"1.0.0": [{"category": "Added", "text": "New"}]}
        corrupt = [
            b"not json",
            b"\xff\xfe",
            b"[1, 2]",
            json.dumps({"v1": [{"category": "Added", "text": "x"}]}).encode(),
            json.dumps({"1.0.0": []}).encode(),
            json.dumps({"1.0.0": [{"category": "Other", "text": "x"}]}).encode(),
        ]
        for raw in corrupt:
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.desk.import_releases(payload)
            self.assertEqual(self.path.read_bytes(), raw)

    def test_cli_import(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        batch = Path(self.temp.name) / "batch.json"
        batch.write_text(json.dumps({
            "1.10.0": [{"category": "Added", "text": "Ten"}],
            "1.2.0": [{"category": "Fixed", "text": "Two"}],
            "1.0.0": [{"category": "Added", "text": "Base"}],
        }), encoding="utf-8")
        result = subprocess.run(prefix + ["import", str(batch)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {"imported": ["1.0.0", "1.2.0", "1.10.0"], "skipped": []})
        again = subprocess.run(prefix + ["import", str(batch)], capture_output=True, text=True)
        self.assertEqual(json.loads(again.stdout), {"imported": [], "skipped": ["1.0.0", "1.2.0", "1.10.0"]})
        empty = Path(self.temp.name) / "empty.json"
        empty.write_text("{}", encoding="utf-8")
        missing = Path(self.temp.name) / "missing-store.json"
        ok = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(missing), "import", str(empty)], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0)
        self.assertFalse(missing.exists())

    def test_cli_import_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        before = self.path.read_bytes()
        bad_json = Path(self.temp.name) / "bad.json"
        bad_json.write_text("{not json", encoding="utf-8")
        failed = subprocess.run(prefix + ["import", str(bad_json)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stdout))
        bad_json.write_bytes(b"\xff\xfe")
        failed = subprocess.run(prefix + ["import", str(bad_json)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stdout))
        conflict = Path(self.temp.name) / "conflict.json"
        conflict.write_text(json.dumps({"1.0.0": [{"category": "Added", "text": "Different"}], "2.0.0": [{"category": "Added", "text": "New"}]}), encoding="utf-8")
        failed = subprocess.run(prefix + ["import", str(conflict)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        missing = subprocess.run(prefix + ["import", str(Path(self.temp.name) / "nope.json")], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stdout))

    def test_export_all_filter_empty_and_dedup(self):
        self.desk.add("1.10.0", [{"category": "Fixed", "text": "Ten"}])
        self.desk.add("1.2.0", [{"category": "Added", "text": "Two"}])
        self.assertEqual(list(self.desk.export_releases()), ["1.2.0", "1.10.0"])
        self.assertEqual(list(self.desk.export_releases(None)), ["1.2.0", "1.10.0"])
        self.assertEqual(self.desk.export_releases([]), {})
        picked = self.desk.export_releases(["1.10.0", "1.10.0"])
        self.assertEqual(list(picked), ["1.10.0"])
        chosen = self.desk.export_releases(["1.10.0", "1.2.0", "1.10.0"])
        self.assertEqual(list(chosen), ["1.2.0", "1.10.0"])

    def test_export_entries_trimmed_without_extra_fields(self):
        self.path.write_text(json.dumps({
            "1.0.0": [
                {"category": "Fixed", "text": " 修复 重试 ", "extra": "dropped"},
                {"category": "Fixed", "text": "修复 重试"},
                {"category": "Added", "text": "New"},
            ],
        }), encoding="utf-8")
        exported = self.desk.export_releases()
        self.assertEqual(exported, {"1.0.0": [
            {"category": "Fixed", "text": "修复 重试"},
            {"category": "Fixed", "text": "修复 重试"},
            {"category": "Added", "text": "New"},
        ]})
        self.assertEqual(set(exported["1.0.0"][0]), {"category", "text"})

    def test_export_roundtrip_skips_on_repeat(self):
        self.desk.add("1.10.0", [{"category": "Fixed", "text": " 重试 "}, {"category": "Fixed", "text": "重试"}])
        self.desk.add("1.2.0", [{"category": "Added", "text": " New "}])
        payload = self.desk.export_releases(["1.2.0", "1.10.0"])
        fresh = ReleaseDesk(Path(self.temp.name) / "fresh.json")
        self.assertEqual(fresh.import_releases(payload), {"imported": ["1.2.0", "1.10.0"], "skipped": []})
        self.assertEqual(fresh.import_releases(payload), {"imported": [], "skipped": ["1.2.0", "1.10.0"]})

    def test_export_readonly(self):
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        self.desk.export_releases()
        self.desk.export_releases(["1.0.0"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_export_validation(self):
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        for versions in ("1.0.0", ["1.0.0", 1], ("1.0.0",), {"1.0.0"}, [None], [True]):
            with self.assertRaises(ValueError):
                self.desk.export_releases(versions)
        for versions in (["v1"], ["1.0"], ["1.0.0.0"], ["01.0.0"]):
            with self.assertRaises(ValueError):
                self.desk.export_releases(versions)
        with self.assertRaises(ValueError):
            self.desk.export_releases(["9.9.9"])
        # An unselected invalid record still fails validation.
        self.path.write_text(json.dumps({"1.0.0": [{"category": "Added", "text": "One"}], "2.0.0": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.desk.export_releases(["1.0.0"])
        self.path.write_bytes(b'{"1.0.0": [], "1.0.0": []}')
        with self.assertRaises(ValueError) as caught:
            self.desk.export_releases(["1.0.0"])
        self.assertEqual(str(caught.exception), "duplicate JSON object key")
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            self.desk.export_releases()

    def test_export_missing_store(self):
        missing = Path(self.temp.name) / "no-store" / "releases.json"
        desk = ReleaseDesk(missing)
        self.assertEqual(desk.export_releases(), {})
        self.assertFalse(missing.parent.exists())
        with self.assertRaises(ValueError):
            desk.export_releases(["1.0.0"])
        self.assertFalse(missing.parent.exists())

    def test_cli_export_stdout(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        self.desk.add("1.10.0", [{"category": "Fixed", "text": "重试"}, {"category": "Fixed", "text": "重试"}])
        self.desk.add("1.2.0", [{"category": "Added", "text": "中文 ✓"}])
        result = subprocess.run(prefix + ["export"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {
            "1.2.0": [{"category": "Added", "text": "中文 ✓"}],
            "1.10.0": [{"category": "Fixed", "text": "重试"}, {"category": "Fixed", "text": "重试"}],
        })
        picked = subprocess.run(prefix + ["export", "--version", "1.10.0", "--version", "1.10.0", "--version", "1.2.0"], capture_output=True, text=True)
        self.assertEqual(list(json.loads(picked.stdout)), ["1.2.0", "1.10.0"])
        missing = Path(self.temp.name) / "missing.json"
        empty = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(missing), "export"], capture_output=True, text=True)
        self.assertEqual(empty.returncode, 0)
        self.assertEqual(empty.stdout.strip(), "{}")
        self.assertFalse(missing.exists())

    def test_cli_export_output_file(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        self.desk.add("1.0.0", [{"category": "Added", "text": " 中文 "}])
        target = Path(self.temp.name) / "nested" / "out.json"
        result = subprocess.run(prefix + ["export", "--output", str(target)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"exported": ["1.0.0"]})
        raw = target.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(json.loads(raw.decode("utf-8")), {"1.0.0": [{"category": "Added", "text": "中文"}]})
        # Existing targets are replaced wholesale.
        target.write_text("PREVIOUS", encoding="utf-8")
        result = subprocess.run(prefix + ["export", "--version", "1.0.0", "--output", str(target)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"exported": ["1.0.0"]})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["1.0.0"], [{"category": "Added", "text": "中文"}])

    def test_cli_export_same_file_as_store(self):
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        script = str(ROOT / "release_desk.py")
        alias = str(self.path.parent) + "/./" + self.path.name
        attempts = [str(self.path), alias]
        link = self.path.parent / "link.json"
        link.symlink_to(self.path)
        attempts.append(str(link))
        hard = self.path.parent / "hard.json"
        os.link(self.path, hard)
        attempts.append(str(hard))
        for output in attempts:
            result = subprocess.run([sys.executable, script, "--store", str(self.path), "export", "--output", output], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, output)
            self.assertIn("error", json.loads(result.stdout))
        ghost = self.path.parent / "ghost.json"
        result = subprocess.run([sys.executable, script, "--store", str(ghost), "export", "--output", "./" + ghost.name], cwd=ghost.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(ghost.exists())

    def test_cli_export_failure_preserves_target(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        target = Path(self.temp.name) / "target.json"
        target.write_text("PRECIOUS", encoding="utf-8")
        result = subprocess.run(prefix + ["export", "--version", "9.9.9", "--output", str(target)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(set(json.loads(result.stdout)), {"error"})
        self.assertEqual(target.read_text(encoding="utf-8"), "PRECIOUS")
        absent = Path(self.temp.name) / "missing-dir" / "target.json"
        result = subprocess.run(prefix + ["export", "--version", "9.9.9", "--output", str(absent)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(absent.exists())
        self.assertFalse(absent.parent.exists())

    def test_preview_imported_skipped_conflicts_sorted(self):
        self.desk.add("1.1.0", [{"category": "Added", "text": "Existing"}])
        self.desk.add("1.2.0", [{"category": "Fixed", "text": "Two"}])
        payload = {
            "1.10.0": [{"category": "Added", "text": "Ten"}],
            "1.2.0": [{"category": "Fixed", "text": "Changed"}],
            "1.1.0": [{"category": "Added", "text": " Existing ", "extra": "ignored"}],
            "1.0.0": [{"category": "Added", "text": "Base"}],
        }
        result = self.desk.preview_import_releases(payload)
        self.assertEqual(result["imported"], ["1.0.0", "1.10.0"])
        self.assertEqual(result["skipped"], ["1.1.0"])
        self.assertEqual([detail["version"] for detail in result["conflicts"]], ["1.2.0"])
        self.assertFalse(result["canImport"])
        detail = result["conflicts"][0]
        self.assertEqual(detail["added"], [{"category": "Fixed", "text": "Changed"}])
        self.assertEqual(detail["removed"], [{"category": "Fixed", "text": "Two"}])
        self.assertEqual(detail["unchanged"], [])
        self.assertFalse(detail["orderOnly"])

    def test_preview_can_import_and_empty_payload(self):
        self.assertEqual(self.desk.preview_import_releases({}),
                         {"imported": [], "skipped": [], "conflicts": [], "canImport": True})
        self.assertFalse(self.path.exists())
        result = self.desk.preview_import_releases({"1.0.0": [{"category": "Added", "text": "One"}]})
        self.assertEqual(result, {"imported": ["1.0.0"], "skipped": [], "conflicts": [], "canImport": True})
        self.assertFalse(self.path.exists())

    def test_preview_conflict_detail_matches_diff_semantics(self):
        self.desk.add("1.0.0", [
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Gone"},
            {"category": "Added", "text": "Old"},
        ])
        payload = {"1.0.0": [
            {"category": "Fixed", "text": " Retry "},
            {"category": "Fixed", "text": "New"},
            {"category": "Added", "text": "Old"},
            {"category": "Changed", "text": "中文 ✓"},
        ]}
        detail = self.desk.preview_import_releases(payload)["conflicts"][0]
        self.assertEqual(detail["version"], "1.0.0")
        self.assertEqual(detail["added"], [{"category": "Changed", "text": "中文 ✓"},
                                           {"category": "Fixed", "text": "New"}])
        self.assertEqual(detail["removed"], [{"category": "Fixed", "text": "Retry"},
                                             {"category": "Fixed", "text": "Gone"}])
        self.assertEqual(detail["unchanged"], [{"category": "Added", "text": "Old"},
                                               {"category": "Fixed", "text": "Retry"}])
        self.assertFalse(detail["orderOnly"])
        for entry in detail["added"] + detail["removed"] + detail["unchanged"]:
            self.assertEqual(set(entry), {"category", "text"})

    def test_preview_order_only_conflict(self):
        changes = [{"category": "Added", "text": "One"}, {"category": "Fixed", "text": "Two"}]
        self.desk.add("1.0.0", changes)
        reordered = {"1.0.0": [changes[1], changes[0]]}
        detail = self.desk.preview_import_releases(reordered)["conflicts"][0]
        self.assertTrue(detail["orderOnly"])
        self.assertEqual(detail["added"], [])
        self.assertEqual(detail["removed"], [])
        self.assertEqual(detail["unchanged"], changes)
        # Same multiset plus a real difference is not order-only.
        different = {"1.0.0": [{"category": "Added", "text": "One"}, {"category": "Fixed", "text": "Two!"}]}
        self.assertFalse(self.desk.preview_import_releases(different)["conflicts"][0]["orderOnly"])

    def test_preview_readonly_and_input_untouched(self):
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        payload = {"1.0.0": [{"category": "Added", "text": "Other"}], "2.0.0": [{"category": "Added", "text": " New ", "extra": 1}]}
        snapshot = json.loads(json.dumps(payload))
        result = self.desk.preview_import_releases(payload)
        self.assertEqual(result["imported"], ["2.0.0"])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(payload, snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        self.assertEqual(self.desk.versions(), ["1.0.0"])

    def test_preview_validates_payload_and_store(self):
        for payload in ([], {"v1": [{"category": "Added", "text": "x"}]}, {"1.0.0": []},
                        {"1.0.0": [{"category": "Other", "text": "x"}]}):
            with self.assertRaises(ValueError):
                self.desk.preview_import_releases(payload)
        # The whole store is validated even for an empty batch.
        self.path.write_text(json.dumps({"9.9.9": []}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.desk.preview_import_releases({})
        raw = b'{"1.0.0": [], "1.0.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            self.desk.preview_import_releases({})
        self.assertEqual(str(caught.exception), "duplicate JSON object key")
        self.assertEqual(self.path.read_bytes(), raw)

    def test_preview_missing_store_and_oserror(self):
        missing = Path(self.temp.name) / "no-dir" / "releases.json"
        desk = ReleaseDesk(missing)
        result = desk.preview_import_releases({"1.0.0": [{"category": "Added", "text": "One"}]})
        self.assertEqual(result["imported"], ["1.0.0"])
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())
        desk = ReleaseDesk(Path(self.temp.name))
        with self.assertRaises(OSError):
            desk.preview_import_releases({})

    def test_cli_import_dry_run(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}, {"category": "Fixed", "text": "Two"}])
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        batch = Path(self.temp.name) / "batch.json"
        batch.write_text(json.dumps({
            "1.0.0": [{"category": "Fixed", "text": "Two"}, {"category": "Added", "text": "One"}],
            "2.0.0": [{"category": "Added", "text": "New"}],
        }), encoding="utf-8")
        result = subprocess.run(prefix + ["import", "--dry-run", str(batch)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["imported"], ["2.0.0"])
        self.assertEqual(payload["skipped"], [])
        self.assertFalse(payload["canImport"])
        self.assertEqual(len(payload["conflicts"]), 1)
        self.assertTrue(payload["conflicts"][0]["orderOnly"])
        # Conflicts do not block the preview and nothing is written.
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        self.assertEqual(self.desk.versions(), ["1.0.0"])

    def test_cli_import_dry_run_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        before = self.path.read_bytes()
        batch = Path(self.temp.name) / "batch.json"
        for raw in ("{not json", '{"1.0.0": [], "1.0.0": []}', '{"v1": []}'):
            batch.write_text(raw, encoding="utf-8")
            failed = subprocess.run(prefix + ["import", "--dry-run", str(batch)], capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, raw)
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        batch.write_bytes(b"\xff\xfe")
        failed = subprocess.run(prefix + ["import", "--dry-run", str(batch)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        missing = subprocess.run(prefix + ["import", "--dry-run", str(Path(self.temp.name) / "nope.json")], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(set(json.loads(missing.stdout)), {"error"})
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is not created, even on failure.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        batch.write_text('{"1.0.0": [], "1.0.0": []}', encoding="utf-8")
        failed = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "import", "--dry-run", str(batch)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout), {"error": "duplicate JSON object key"})
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.changes = [{"category": "Added", "text": "One"}, {"category": "Fixed", "text": "Two"}]
        self.desk.add("1.2.0", self.changes)

    def payload(self, **overrides):
        data = {
            "version": "1.2.0",
            "items": [
                {"id": "docs", "text": " Write notes ", "required": True, "status": "done"},
                {"id": "sign", "text": "Sign build", "required": True, "status": "pending"},
                {"id": "nice", "text": "Polish page", "required": False, "status": "blocked"},
            ],
        }
        data.update(overrides)
        return data

    def test_groups_keep_order_and_report_shape(self):
        not_ready = self.desk.checklist("1.2.0", self.payload())
        self.assertEqual(set(not_ready), {"version", "ready", "done", "pending", "blocked"})
        self.assertFalse(not_ready["ready"])
        self.assertEqual(not_ready["done"], [
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"}])
        self.assertEqual(not_ready["pending"], [
            {"id": "sign", "text": "Sign build", "required": True, "status": "pending"}])
        self.assertEqual(not_ready["blocked"], [
            {"id": "nice", "text": "Polish page", "required": False, "status": "blocked"}])
        for group in ("done", "pending", "blocked"):
            for item in not_ready[group]:
                self.assertEqual(set(item), {"id", "text", "required", "status"})

    def test_ready_when_all_required_done_optional_open(self):
        data = self.payload()
        data["items"][1]["status"] = "done"
        report = self.desk.checklist("1.2.0", data)
        self.assertTrue(report["ready"])
        self.assertEqual([item["id"] for item in report["done"]], ["docs", "sign"])
        self.assertEqual([item["id"] for item in report["blocked"]], ["nice"])

    def test_optional_never_blocks(self):
        data = self.payload()
        data["items"][1]["status"] = "done"
        data["items"][2]["status"] = "pending"
        self.assertTrue(self.desk.checklist("1.2.0", data)["ready"])

    def test_required_blocked_is_not_ready(self):
        data = self.payload()
        data["items"][1]["status"] = "done"
        data["items"][2] = {"id": "gate", "text": "Gate", "required": True, "status": "blocked"}
        self.assertFalse(self.desk.checklist("1.2.0", data)["ready"])

    def test_group_order_follows_input(self):
        data = self.payload()
        data["items"] = [
            {"id": "a", "text": "A", "required": False, "status": "pending"},
            {"id": "b", "text": "B", "required": True, "status": "done"},
            {"id": "c", "text": "C", "required": False, "status": "pending"},
        ]
        report = self.desk.checklist("1.2.0", data)
        self.assertTrue(report["ready"])
        self.assertEqual([item["id"] for item in report["pending"]], ["a", "c"])

    def test_duplicate_titles_legal_but_ids_case_sensitive(self):
        data = self.payload()
        data["items"] = [
            {"id": "Same", "text": "Title", "required": True, "status": "done"},
            {"id": "same", "text": "Title", "required": False, "status": "pending"},
            {"id": " SAME ", "text": "Title", "required": False, "status": "blocked"},
        ]
        report = self.desk.checklist("1.2.0", data)
        self.assertTrue(report["ready"])
        self.assertEqual([item["id"] for item in report["done"]], ["Same"])
        self.assertEqual([item["id"] for item in report["pending"]], ["same"])
        self.assertEqual([item["id"] for item in report["blocked"]], ["SAME"])

    def test_duplicate_ids_rejected_after_trim(self):
        data = self.payload()
        data["items"][1]["id"] = " docs "
        with self.assertRaises(ValueError):
            self.desk.checklist("1.2.0", data)

    def test_no_unicode_normalization_on_ids(self):
        composed = "caf" + chr(0x00E9)
        decomposed = "caf" + "e" + chr(0x0301)
        self.assertNotEqual(composed, decomposed)
        data = self.payload()
        data["items"] = [
            {"id": composed, "text": "Composed", "required": True, "status": "done"},
            {"id": decomposed, "text": "Decomposed", "required": False, "status": "pending"},
        ]
        report = self.desk.checklist("1.2.0", data)
        self.assertEqual([item["id"] for item in report["pending"]], [decomposed])

    def test_invalid_versions(self):
        for version in (None, 1, "v1", "1.0", "1.0.0.0", "01.0.0"):
            with self.assertRaises(ValueError):
                self.desk.checklist(version, self.payload())

    def test_invalid_payload_structures(self):
        invalid = [
            None, [], "x", 1,
            {"items": []},
            {"version": "1.2.0"},
            {"version": "1.2.0", "items": {}},
            {"version": "1.2.0", "items": []},
            {"version": "v1", "items": [{"id": "a", "text": "A", "required": True, "status": "done"}]},
            {"version": "2.0.0", "items": [{"id": "a", "text": "A", "required": True, "status": "done"}]},
        ]
        for payload in invalid:
            with self.assertRaises(ValueError):
                self.desk.checklist("1.2.0", payload)

    def test_invalid_items(self):
        good = {"id": "a", "text": "A", "required": True, "status": "done"}
        variants = [
            [], "x", None, 1,
            {**good, "id": None}, {**good, "id": 1}, {**good, "id": "  "},
            {**good, "id": "a\nb"}, {**good, "id": "a\rb"},
            {**good, "text": ""}, {**good, "text": 3}, {**good, "text": "x\ny"},
            {**good, "required": "yes"}, {**good, "required": 1}, {**good, "required": None},
            {**good, "status": "DONE"}, {**good, "status": "started"}, {**good, "status": None},
        ]
        for item in variants:
            with self.assertRaises(ValueError):
                self.desk.checklist("1.2.0", {"version": "1.2.0", "items": [item]})

    def test_all_optional_rejected(self):
        payload = {"version": "1.2.0", "items": [
            {"id": "a", "text": "A", "required": False, "status": "done"}]}
        with self.assertRaises(ValueError):
            self.desk.checklist("1.2.0", payload)

    def test_extra_fields_ignored(self):
        payload = {"version": "1.2.0", "extra": "ignored", "items": [
            {"id": "a", "text": " A ", "required": True, "status": "done", "other": 1, "notes": "x"}]}
        report = self.desk.checklist("1.2.0", payload)
        self.assertEqual(report["done"], [{"id": "a", "text": "A", "required": True, "status": "done"}])

    def test_unknown_version_and_missing_store(self):
        with self.assertRaises(ValueError):
            self.desk.checklist("9.9.9", self.payload())
        missing = ReleaseDesk(Path(self.temp.name) / "no-dir" / "releases.json")
        with self.assertRaises(ValueError):
            missing.checklist("1.2.0", self.payload())

    def test_version_mismatch_without_touching_store(self):
        with self.assertRaises(ValueError):
            self.desk.checklist("1.0.0", self.payload())

    def test_whole_store_validated(self):
        raw = b'{"1.2.0": [{"category": "Added", "text": "One"}], "9.9.9": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.desk.checklist("1.2.0", self.payload())
        self.assertEqual(self.path.read_bytes(), raw)
        raw = b'{"1.2.0": [], "1.2.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            self.desk.checklist("1.2.0", self.payload())
        self.assertEqual(str(caught.exception), "duplicate JSON object key")
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            self.desk.checklist("1.2.0", self.payload())

    def test_readonly_and_inputs_untouched(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        payload = self.payload()
        snapshot = json.loads(json.dumps(payload))
        not_ready = self.desk.checklist("1.2.0", payload)
        self.assertFalse(not_ready["ready"])
        self.assertEqual(payload, snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_cli_check(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        ready = {"version": "1.2.0", "items": [
            {"id": "docs", "text": " Notes ", "required": True, "status": "done"},
            {"id": "later", "text": "Later", "required": False, "status": "pending"}]}
        checklist.write_text(json.dumps(ready), encoding="utf-8")
        result = subprocess.run(prefix + ["check", "1.2.0", str(checklist)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {
            "version": "1.2.0", "ready": True,
            "done": [{"id": "docs", "text": "Notes", "required": True, "status": "done"}],
            "pending": [{"id": "later", "text": "Later", "required": False, "status": "pending"}],
            "blocked": []})
        not_ready = {"version": "1.2.0", "items": [
            {"id": "docs", "text": "Notes", "required": True, "status": "blocked"}]}
        checklist.write_text(json.dumps(not_ready), encoding="utf-8")
        result = subprocess.run(prefix + ["check", "1.2.0", str(checklist)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["ready"])

    def test_cli_check_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        checklist = Path(self.temp.name) / "checklist.json"
        cases = [
            "{not json",
            b"\xff\xfe",
            json.dumps({"version": "2.0.0", "items": [
                {"id": "a", "text": "A", "required": True, "status": "done"}]}),
            json.dumps({"version": "1.2.0", "items": [
                {"id": "a", "text": "A", "required": False, "status": "done"}]}),
            json.dumps({"version": "1.2.0", "items": [
                {"id": "a", "text": "A", "required": True, "status": "done"},
                {"id": "a", "text": "B", "required": False, "status": "pending"}]}),
            '{"version": "1.2.0", "version": "1.2.0", "items": []}',
        ]
        for case in cases:
            if isinstance(case, bytes):
                checklist.write_bytes(case)
            else:
                checklist.write_text(case, encoding="utf-8")
            result = subprocess.run(prefix + ["check", "1.2.0", str(checklist)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, case)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        checklist.write_text(json.dumps({"version": "9.9.9", "items": [
            {"id": "a", "text": "A", "required": True, "status": "done"}]}), encoding="utf-8")
        missing = subprocess.run(prefix + ["check", "9.9.9", str(checklist)], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stdout))
        absent = subprocess.run(prefix + ["check", "1.2.0", str(Path(self.temp.name) / "nope.json")],
                                capture_output=True, text=True)
        self.assertEqual(absent.returncode, 2)
        self.assertIn("error", json.loads(absent.stdout))
        self.assertEqual(self.path.read_bytes(), before)


class GenerateChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.2.0", [{"category": "Fixed", "text": "Retry empty exports"}])

    def template(self, **overrides):
        data = {
            "items": [
                {"id": "docs", "text": " Write notes ", "required": True},
                {"id": "fixed", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
                {"id": "added", "text": "Announce feature", "required": False, "categories": ["Added"]},
            ],
        }
        data.update(overrides)
        return data

    def test_filters_by_release_categories(self):
        result = self.desk.generate_checklist("1.2.0", self.template())
        self.assertEqual(set(result), {"version", "items"})
        self.assertEqual(result["version"], "1.2.0")
        self.assertEqual(result["items"], [
            {"id": "docs", "text": "Write notes", "required": True, "status": "pending"},
            {"id": "fixed", "text": "Verify fix", "required": True, "status": "pending"},
        ])

    def test_result_feeds_checklist_and_check(self):
        generated = self.desk.generate_checklist("1.2.0", self.template())
        report = self.desk.checklist("1.2.0", generated)
        self.assertEqual(set(report), {"version", "ready", "done", "pending", "blocked"})
        self.assertFalse(report["ready"])
        self.assertEqual([item["id"] for item in report["pending"]], ["docs", "fixed"])

    def test_multi_category_item_kept_once_per_release(self):
        self.desk.add("2.0.0", [
            {"category": "Added", "text": "One"},
            {"category": "Added", "text": "Two"},
            {"category": "Fixed", "text": "Three"},
        ])
        template = {"items": [
            {"id": "both", "text": "Both", "required": True, "categories": ["Added", "Fixed"]},
            {"id": "changed", "text": "Changed only", "required": False, "categories": ["Changed"]},
        ]}
        result = self.desk.generate_checklist("2.0.0", template)
        self.assertEqual([item["id"] for item in result["items"]], ["both"])

    def test_extra_fields_and_template_status_ignored(self):
        template = {"version": "9.9.9", "extra": 1, "items": [
            {"id": "a", "text": " A ", "required": True, "status": "done", "other": [1]}]}
        result = self.desk.generate_checklist("1.2.0", template)
        self.assertEqual(result, {"version": "1.2.0", "items": [
            {"id": "a", "text": "A", "required": True, "status": "pending"}]})

    def test_invalid_version_and_unknown_release(self):
        for version in (None, 1, "v1", "1.0", "1.0.0.0", "01.0.0"):
            with self.assertRaises(ValueError):
                self.desk.generate_checklist(version, self.template())
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("9.9.9", self.template())
        missing = ReleaseDesk(Path(self.temp.name) / "no-dir" / "releases.json")
        with self.assertRaises(ValueError):
            missing.generate_checklist("1.2.0", self.template())
        self.assertFalse(missing.path.parent.exists())

    def test_invalid_template_structures(self):
        invalid = [
            None, [], "x", 1,
            {"items": []}, {"items": {}}, {"items": None},
            {"items": [{"id": "a", "text": "A", "required": True}], "extra": []},
        ]
        for payload in invalid[:-1]:
            with self.assertRaises(ValueError):
                self.desk.generate_checklist("1.2.0", payload)
        # The extra field itself is fine; this one is valid.
        self.assertEqual(len(self.desk.generate_checklist("1.2.0", invalid[-1])["items"]), 1)

    def test_invalid_items(self):
        good = {"id": "a", "text": "A", "required": True}
        variants = [
            [], "x", None, 1,
            {**good, "id": None}, {**good, "id": 1}, {**good, "id": "  "},
            {**good, "id": "a\nb"}, {**good, "id": "a\rb"},
            {**good, "text": ""}, {**good, "text": "  "}, {**good, "text": 3}, {**good, "text": "x\ny"},
            {**good, "required": "yes"}, {**good, "required": 1}, {**good, "required": None},
            {**good, "categories": []}, {**good, "categories": "Fixed"},
            {**good, "categories": ["fixed"]}, {**good, "categories": ["Other"]},
            {**good, "categories": ["Fixed", "Fixed"]}, {**good, "categories": [1]},
            {**good, "categories": ["Added", "Added"]}, {**good, "categories": [["Fixed"]]},
        ]
        for item in variants:
            with self.assertRaises(ValueError):
                self.desk.generate_checklist("1.2.0", {"items": [item]})

    def test_unmatched_invalid_item_still_rejected(self):
        template = {"items": [
            {"id": "ok", "text": "OK", "required": True},
            {"id": "bad", "text": "A\nB", "required": True, "categories": ["Added"]},
        ]}
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", template)

    def test_duplicate_ids_rejected_case_sensitively(self):
        template = {"items": [
            {"id": "Same", "text": "One", "required": True},
            {"id": " Same ", "text": "Two", "required": False},
        ]}
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", template)
        distinct_case = {"items": [
            {"id": "Same", "text": "One", "required": True},
            {"id": "SAME", "text": "Two", "required": False},
        ]}
        result = self.desk.generate_checklist("1.2.0", distinct_case)
        self.assertEqual([item["id"] for item in result["items"]], ["Same", "SAME"])
        composed = "caf" + chr(0x00E9)
        decomposed = "caf" + "e" + chr(0x0301)
        template = {"items": [
            {"id": composed, "text": "One", "required": True},
            {"id": decomposed, "text": "Two", "required": False},
        ]}
        result = self.desk.generate_checklist("1.2.0", template)
        self.assertEqual([item["id"] for item in result["items"]], [composed, decomposed])

    def test_empty_selection_and_no_required_rejected(self):
        only_added = {"items": [
            {"id": "a", "text": "A", "required": True, "categories": ["Added"]}]}
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", only_added)
        no_required = {"items": [
            {"id": "a", "text": "A", "required": False},
            {"id": "b", "text": "B", "required": False, "categories": ["Fixed"]}]}
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", no_required)
        required_filtered = {"items": [
            {"id": "a", "text": "A", "required": False},
            {"id": "b", "text": "B", "required": True, "categories": ["Added"]}]}
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", required_filtered)

    def test_whole_store_validated(self):
        raw = b'{"1.2.0": [{"category": "Fixed", "text": "One"}], "9.9.9": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", self.template())
        self.assertEqual(self.path.read_bytes(), raw)
        raw = b'{"1.2.0": [], "1.2.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            self.desk.generate_checklist("1.2.0", self.template())
        self.assertEqual(str(caught.exception), "duplicate JSON object key")
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", self.template())

    def test_readonly_and_inputs_untouched(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        template = self.template()
        snapshot = json.loads(json.dumps(template))
        result = self.desk.generate_checklist("1.2.0", template)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(template, snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_cli_make_checklist(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        template = Path(self.temp.name) / "template.json"
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        result = subprocess.run(prefix + ["make-checklist", "1.2.0", str(template)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {
            "version": "1.2.0",
            "items": [
                {"id": "docs", "text": "Write notes", "required": True, "status": "pending"},
                {"id": "fixed", "text": "Verify fix", "required": True, "status": "pending"},
            ]})
        # The generated checklist is accepted by check as-is.
        generated = Path(self.temp.name) / "generated.json"
        generated.write_text(result.stdout, encoding="utf-8")
        checked = subprocess.run(prefix + ["check", "1.2.0", str(generated)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertFalse(json.loads(checked.stdout)["ready"])

    def test_cli_make_checklist_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        template = Path(self.temp.name) / "template.json"
        cases = [
            "{not json",
            b"\xff\xfe",
            json.dumps({"items": []}),
            json.dumps({"items": [{"id": "a", "text": "A", "required": True, "categories": ["Added"]}]}),
            json.dumps({"items": [{"id": "a", "text": "A", "required": False}]}),
            '{"items": [{"id": "a", "text": "A", "required": true}], "items": []}',
        ]
        for case in cases:
            if isinstance(case, bytes):
                template.write_bytes(case)
            else:
                template.write_text(case, encoding="utf-8")
            result = subprocess.run(prefix + ["make-checklist", "1.2.0", str(template)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, case)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        duplicate = '{"items": [{"id": "a", "id": "a", "text": "A", "required": true}]}'
        template.write_text(duplicate, encoding="utf-8")
        failed = subprocess.run(prefix + ["make-checklist", "1.2.0", str(template)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout), {"error": "duplicate JSON object key"})
        for version in ("v1", "9.9.9"):
            template.write_text(json.dumps(self.template()), encoding="utf-8")
            failed = subprocess.run(prefix + ["make-checklist", version, str(template)], capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        absent = subprocess.run(prefix + ["make-checklist", "1.2.0", str(Path(self.temp.name) / "nope.json")],
                                capture_output=True, text=True)
        self.assertEqual(absent.returncode, 2)
        self.assertIn("error", json.loads(absent.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is treated as empty, fails as unknown, and is not created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        failed = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "make-checklist", "1.2.0", str(template)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


class AuditChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.2.0", [{"category": "Fixed", "text": "Retry empty exports"}])

    def template(self, **overrides):
        data = {
            "items": [
                {"id": "docs", "text": " Write notes ", "required": True},
                {"id": "fixed", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
                {"id": "added", "text": "Announce feature", "required": False, "categories": ["Added"]},
            ],
        }
        data.update(overrides)
        return data

    def checklist(self, **overrides):
        data = {
            "version": "1.2.0",
            "items": [
                {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
                {"id": "fixed", "text": "Verify fix", "required": True, "status": "pending"},
            ],
        }
        data.update(overrides)
        return data

    def test_clean_match_report_shape(self):
        report = self.desk.audit_checklist("1.2.0", self.checklist(), self.template())
        self.assertEqual(set(report), {"version", "ready", "done", "pending", "blocked",
                                       "missing", "mismatched", "unexpected"})
        self.assertEqual(report["version"], "1.2.0")
        self.assertFalse(report["ready"])
        self.assertEqual(report["done"], [
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"}])
        self.assertEqual(report["pending"], [
            {"id": "fixed", "text": "Verify fix", "required": True, "status": "pending"}])
        self.assertEqual(report["blocked"], [])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["mismatched"], [])
        self.assertEqual(report["unexpected"], [])
        done = self.checklist()
        done["items"][1]["status"] = "done"
        self.assertTrue(self.desk.audit_checklist("1.2.0", done, self.template())["ready"])

    def test_missing_items_pending_in_template_order(self):
        template = {"items": [
            {"id": "one", "text": "One", "required": False},
            {"id": "two", "text": "Two", "required": True},
            {"id": "three", "text": "Three", "required": True},
        ]}
        checklist = {"version": "1.2.0", "items": [
            {"id": "three", "text": "Three", "required": True, "status": "done"}]}
        report = self.desk.audit_checklist("1.2.0", checklist, template)
        self.assertFalse(report["ready"])
        self.assertEqual(report["missing"], [
            {"id": "one", "text": "One", "required": False, "status": "pending"},
            {"id": "two", "text": "Two", "required": True, "status": "pending"}])
        # Missing optional items alone never block readiness.
        checklist["items"].append({"id": "two", "text": "Two", "required": True, "status": "done"})
        report = self.desk.audit_checklist("1.2.0", checklist, template)
        self.assertTrue(report["ready"])
        self.assertEqual([item["id"] for item in report["missing"]], ["one"])

    def test_mismatched_text_and_required(self):
        checklist = self.checklist()
        checklist["items"][1]["text"] = "Verify the fix"
        report = self.desk.audit_checklist("1.2.0", checklist, self.template())
        self.assertFalse(report["ready"])
        self.assertEqual(report["mismatched"], [{
            "expected": {"id": "fixed", "text": "Verify fix", "required": True, "status": "pending"},
            "actual": {"id": "fixed", "text": "Verify the fix", "required": True, "status": "pending"},
        }])
        flipped = self.checklist()
        flipped["items"][1]["required"] = False
        flipped["items"][1]["status"] = "done"
        report = self.desk.audit_checklist("1.2.0", flipped, self.template())
        self.assertFalse(report["ready"])
        self.assertEqual(report["mismatched"][0]["expected"]["required"], True)
        self.assertEqual(report["mismatched"][0]["actual"]["required"], False)

    def test_optional_mismatch_never_blocks(self):
        template = {"items": [
            {"id": "docs", "text": "Write notes", "required": True},
            {"id": "polish", "text": "Polish page", "required": False},
        ]}
        checklist = {"version": "1.2.0", "items": [
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "polish", "text": "Polish the page", "required": True, "status": "done"}]}
        report = self.desk.audit_checklist("1.2.0", checklist, template)
        self.assertTrue(report["ready"])
        self.assertEqual(report["mismatched"], [{
            "expected": {"id": "polish", "text": "Polish page", "required": False, "status": "pending"},
            "actual": {"id": "polish", "text": "Polish the page", "required": True, "status": "done"},
        }])

    def test_unexpected_in_checklist_order(self):
        checklist = self.checklist()
        checklist["items"][1]["status"] = "done"
        checklist["items"].insert(0, {"id": "extra", "text": "Extra", "required": False, "status": "blocked"})
        checklist["items"].append({"id": "more", "text": "More", "required": False, "status": "pending"})
        report = self.desk.audit_checklist("1.2.0", checklist, self.template())
        self.assertTrue(report["ready"])
        self.assertEqual(report["unexpected"], [
            {"id": "extra", "text": "Extra", "required": False, "status": "blocked"},
            {"id": "more", "text": "More", "required": False, "status": "pending"}])
        # An unexpected required item still follows the checklist readiness rules.
        checklist["items"][0]["required"] = True
        self.assertFalse(self.desk.audit_checklist("1.2.0", checklist, self.template())["ready"])

    def test_missing_and_mismatched_follow_template_order(self):
        template = {"items": [
            {"id": "a", "text": "A", "required": True},
            {"id": "b", "text": "B", "required": True},
            {"id": "c", "text": "C", "required": True},
            {"id": "d", "text": "D", "required": True},
        ]}
        checklist = {"version": "1.2.0", "items": [
            {"id": "d", "text": "D", "required": True, "status": "done"},
            {"id": "b", "text": "Bee", "required": True, "status": "done"},
            {"id": "a", "text": "A", "required": True, "status": "done"},
        ]}
        report = self.desk.audit_checklist("1.2.0", checklist, template)
        self.assertFalse(report["ready"])
        self.assertEqual([item["id"] for item in report["missing"]], ["c"])
        self.assertEqual([entry["expected"]["id"] for entry in report["mismatched"]], ["b"])
        template["items"].insert(0, {"id": "z", "text": "Z", "required": False})
        report = self.desk.audit_checklist("1.2.0", checklist, template)
        self.assertEqual([item["id"] for item in report["missing"]], ["z", "c"])

    def test_ids_case_sensitive_without_unicode_normalization(self):
        checklist = self.checklist()
        checklist["items"][1]["id"] = "FIXED"
        checklist["items"][1]["status"] = "done"
        report = self.desk.audit_checklist("1.2.0", checklist, self.template())
        self.assertFalse(report["ready"])
        self.assertEqual([item["id"] for item in report["missing"]], ["fixed"])
        self.assertEqual([item["id"] for item in report["unexpected"]], ["FIXED"])
        composed = "caf" + chr(0x00E9)
        decomposed = "caf" + "e" + chr(0x0301)
        template = {"items": [{"id": composed, "text": "Cafe", "required": True}]}
        checklist = {"version": "1.2.0", "items": [
            {"id": decomposed, "text": "Cafe", "required": True, "status": "done"}]}
        report = self.desk.audit_checklist("1.2.0", checklist, template)
        self.assertEqual([item["id"] for item in report["missing"]], [composed])
        self.assertEqual([item["id"] for item in report["unexpected"]], [decomposed])

    def test_invalid_version_and_unknown_release(self):
        for version in (None, 1, "v1", "1.0", "1.0.0.0", "01.0.0"):
            with self.assertRaises(ValueError):
                self.desk.audit_checklist(version, self.checklist(), self.template())
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("9.9.9", self.checklist(), self.template())
        missing = ReleaseDesk(Path(self.temp.name) / "no-dir" / "releases.json")
        with self.assertRaises(ValueError):
            missing.audit_checklist("1.2.0", self.checklist(), self.template())
        self.assertFalse(missing.path.parent.exists())

    def test_invalid_checklist_and_template(self):
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("1.2.0", {"version": "1.2.0", "items": []}, self.template())
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("1.2.0", self.checklist(version="1.3.0"), self.template())
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("1.2.0", self.checklist(), {"items": []})
        # Template items are fully validated even when the filter would drop them.
        template = self.template()
        template["items"][2]["text"] = "A\nB"
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("1.2.0", self.checklist(), template)

    def test_empty_selection_and_no_required_rejected(self):
        only_added = {"items": [
            {"id": "a", "text": "A", "required": True, "categories": ["Added"]}]}
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("1.2.0", self.checklist(), only_added)
        no_required = {"items": [
            {"id": "a", "text": "A", "required": False},
            {"id": "b", "text": "B", "required": False, "categories": ["Fixed"]}]}
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("1.2.0", self.checklist(), no_required)

    def test_whole_store_validated(self):
        raw = b'{"1.2.0": [{"category": "Fixed", "text": "One"}], "9.9.9": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("1.2.0", self.checklist(), self.template())
        self.assertEqual(self.path.read_bytes(), raw)
        raw = b'{"1.2.0": [], "1.2.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            self.desk.audit_checklist("1.2.0", self.checklist(), self.template())
        self.assertEqual(str(caught.exception), "duplicate JSON object key")
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            self.desk.audit_checklist("1.2.0", self.checklist(), self.template())

    def test_readonly_and_inputs_untouched(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        checklist, template = self.checklist(), self.template()
        snapshot = json.loads(json.dumps({"checklist": checklist, "template": template}))
        report = self.desk.audit_checklist("1.2.0", checklist, template)
        self.assertFalse(report["ready"])
        self.assertEqual({"checklist": checklist, "template": template}, snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_cli_audit_checklist(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        checklist.write_text(json.dumps(self.checklist()), encoding="utf-8")
        result = subprocess.run(prefix + ["audit-checklist", "1.2.0", str(checklist), str(template)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["ready"])
        self.assertEqual(report["missing"], [])
        ready = self.checklist()
        ready["items"][1]["status"] = "done"
        checklist.write_text(json.dumps(ready), encoding="utf-8")
        result = subprocess.run(prefix + ["audit-checklist", "1.2.0", str(checklist), str(template)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)["ready"])

    def test_cli_audit_checklist_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        cases = [
            "{not json",
            b"\xff\xfe",
            json.dumps({"version": "2.0.0", "items": [
                {"id": "a", "text": "A", "required": True, "status": "done"}]}),
            '{"version": "1.2.0", "version": "1.2.0", "items": []}',
        ]
        for case in cases:
            if isinstance(case, bytes):
                checklist.write_bytes(case)
            else:
                checklist.write_text(case, encoding="utf-8")
            result = subprocess.run(prefix + ["audit-checklist", "1.2.0", str(checklist), str(template)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, case)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        checklist.write_text(json.dumps(self.checklist()), encoding="utf-8")
        template.write_text('{"items": [{"id": "a", "id": "a", "text": "A", "required": true}]}',
                            encoding="utf-8")
        result = subprocess.run(prefix + ["audit-checklist", "1.2.0", str(checklist), str(template)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), {"error": "duplicate JSON object key"})
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        for version in ("v1", "9.9.9"):
            result = subprocess.run(prefix + ["audit-checklist", version, str(checklist), str(template)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        absent = subprocess.run(
            prefix + ["audit-checklist", "1.2.0", str(Path(self.temp.name) / "nope.json"), str(template)],
            capture_output=True, text=True)
        self.assertEqual(absent.returncode, 2)
        self.assertIn("error", json.loads(absent.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is treated as empty, fails as unknown, and is not created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        result = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "audit-checklist", "1.2.0", str(checklist), str(template)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


class ReconcileChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.2.0", [{"category": "Fixed", "text": "Retry empty exports"}])

    def template(self, **overrides):
        data = {
            "items": [
                {"id": "docs", "text": " Write notes ", "required": True},
                {"id": "fixed", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
                {"id": "added", "text": "Announce feature", "required": False, "categories": ["Added"]},
            ],
        }
        data.update(overrides)
        return data

    def checklist(self, **overrides):
        data = {
            "version": "1.2.0",
            "items": [
                {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
                {"id": "fixed", "text": "Verify fix", "required": True, "status": "blocked"},
            ],
        }
        data.update(overrides)
        return data

    def test_retained_reset_added_removed_classification(self):
        checklist = self.checklist()
        checklist["items"].append({"id": "legacy", "text": "Legacy", "required": False, "status": "done"})
        template = {"items": [
            {"id": "gate", "text": "Gate build", "required": True},
            {"id": "docs", "text": "Write notes", "required": True},
            {"id": "fixed", "text": "Verify the fix now", "required": True, "categories": ["Fixed"]},
            {"id": "added", "text": "Announce feature", "required": False, "categories": ["Added"]},
        ]}
        result = self.desk.reconcile_checklist("1.2.0", checklist, template)
        self.assertEqual(set(result), {"version", "items", "retained", "reset", "added", "removed"})
        self.assertEqual(result["version"], "1.2.0")
        self.assertEqual(result["items"], [
            {"id": "gate", "text": "Gate build", "required": True, "status": "pending"},
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "fixed", "text": "Verify the fix now", "required": True, "status": "pending"},
        ])
        self.assertEqual(result["retained"], ["docs"])
        self.assertEqual(result["reset"], ["fixed"])
        self.assertEqual(result["added"], ["gate"])
        self.assertEqual(result["removed"], ["legacy"])
        for item in result["items"]:
            self.assertEqual(set(item), {"id", "text", "required", "status"})

    def test_reorder_only_keeps_statuses(self):
        template = {"items": [
            {"id": "fixed", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
            {"id": "docs", "text": "Write notes", "required": True},
        ]}
        result = self.desk.reconcile_checklist("1.2.0", self.checklist(), template)
        self.assertEqual([item["id"] for item in result["items"]], ["fixed", "docs"])
        self.assertEqual(result["items"][0]["status"], "blocked")
        self.assertEqual(result["items"][1]["status"], "done")
        self.assertEqual(result["retained"], ["fixed", "docs"])
        self.assertEqual(result["reset"], [])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])

    def test_text_and_required_changes_reset_to_new_definition(self):
        text_change = {"items": [
            {"id": "docs", "text": "Write release notes", "required": True},
            {"id": "fixed", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
        ]}
        result = self.desk.reconcile_checklist("1.2.0", self.checklist(), text_change)
        self.assertEqual(result["reset"], ["docs"])
        self.assertEqual(result["items"][0],
                         {"id": "docs", "text": "Write release notes", "required": True, "status": "pending"})
        required_flip = {"items": [
            {"id": "docs", "text": "Write notes", "required": False},
            {"id": "fixed", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
        ]}
        result = self.desk.reconcile_checklist("1.2.0", self.checklist(), required_flip)
        self.assertEqual(result["reset"], ["docs"])
        self.assertEqual(result["retained"], ["fixed"])
        self.assertEqual(result["items"][0],
                         {"id": "docs", "text": "Write notes", "required": False, "status": "pending"})
        self.assertEqual(result["items"][1]["status"], "blocked")

    def test_added_items_pending_and_removed_orders(self):
        checklist = self.checklist()
        checklist["items"] = [
            {"id": "legacy", "text": "Legacy", "required": True, "status": "done"},
            {"id": "docs", "text": "Write notes", "required": True, "status": "pending"},
            {"id": "added", "text": "Announce feature", "required": False, "status": "done"},
            {"id": "gone", "text": "Gone", "required": False, "status": "blocked"},
        ]
        template = {"items": [
            {"id": "docs", "text": "Write notes", "required": True},
            {"id": "fixed", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
            {"id": "added", "text": "Announce feature", "required": False, "categories": ["Added"]},
        ]}
        result = self.desk.reconcile_checklist("1.2.0", checklist, template)
        self.assertEqual(result["added"], ["fixed"])
        self.assertEqual(result["removed"], ["legacy", "added", "gone"])
        self.assertEqual(result["retained"], ["docs"])
        self.assertEqual(result["reset"], [])
        self.assertEqual([item["id"] for item in result["items"]], ["docs", "fixed"])
        self.assertEqual(result["items"][1]["status"], "pending")

    def test_identical_template_retains_everything_with_empty_arrays(self):
        result = self.desk.reconcile_checklist("1.2.0", self.checklist(), self.template())
        self.assertEqual([item["id"] for item in result["items"]], ["docs", "fixed"])
        self.assertEqual(result["retained"], ["docs", "fixed"])
        self.assertEqual(result["reset"], [])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])

    def test_template_status_ignored_and_status_not_inferred(self):
        template = {"items": [
            {"id": "docs", "text": "Write notes differently", "required": True, "status": "done"},
            {"id": "fixed", "text": "Verify fix", "required": True, "status": "done", "categories": ["Fixed"]},
            {"id": "gate", "text": "Gate", "required": True, "status": "done"},
        ]}
        result = self.desk.reconcile_checklist("1.2.0", self.checklist(), template)
        self.assertEqual(result["items"][0]["status"], "pending")
        self.assertEqual(result["items"][1]["status"], "blocked")
        self.assertEqual(result["items"][2]["status"], "pending")
        self.assertEqual(result["reset"], ["docs"])
        self.assertEqual(result["retained"], ["fixed"])
        self.assertEqual(result["added"], ["gate"])

    def test_ids_trimmed_case_sensitive_no_unicode_normalization(self):
        checklist = self.checklist()
        checklist["items"][0]["id"] = " docs "
        result = self.desk.reconcile_checklist("1.2.0", checklist, self.template())
        self.assertEqual(result["retained"], ["docs", "fixed"])
        upper = self.checklist()
        upper["items"][1]["id"] = "FIXED"
        result = self.desk.reconcile_checklist("1.2.0", upper, self.template())
        self.assertEqual(result["added"], ["fixed"])
        self.assertEqual(result["removed"], ["FIXED"])
        composed = "caf" + chr(0x00E9)
        decomposed = "caf" + "e" + chr(0x0301)
        template = {"items": [{"id": composed, "text": "Cafe", "required": True}]}
        checklist = {"version": "1.2.0", "items": [
            {"id": decomposed, "text": "Cafe", "required": True, "status": "done"}]}
        result = self.desk.reconcile_checklist("1.2.0", checklist, template)
        self.assertEqual(result["added"], [composed])
        self.assertEqual(result["removed"], [decomposed])

    def test_result_feeds_check_and_audit(self):
        checklist = self.checklist()
        template = {"items": [
            {"id": "docs", "text": "Write notes", "required": True},
            {"id": "fixed", "text": "Verify the fix", "required": True, "categories": ["Fixed"]},
            {"id": "gate", "text": "Gate", "required": False},
        ]}
        result = self.desk.reconcile_checklist("1.2.0", checklist, template)
        report = self.desk.checklist("1.2.0", result)
        self.assertEqual(set(report), {"version", "ready", "done", "pending", "blocked"})
        self.assertFalse(report["ready"])
        self.assertEqual([item["id"] for item in report["done"]], ["docs"])
        self.assertEqual([item["id"] for item in report["pending"]], ["fixed", "gate"])
        audit = self.desk.audit_checklist("1.2.0", result, template)
        self.assertEqual(audit["missing"], [])
        self.assertEqual(audit["mismatched"], [])
        self.assertEqual(audit["unexpected"], [])
        self.assertFalse(audit["ready"])
        done = dict(result)
        done["items"] = [dict(item, status="done") for item in result["items"]]
        self.assertTrue(self.desk.audit_checklist("1.2.0", done, template)["ready"])

    def test_invalid_version_unknown_release_and_mismatch(self):
        for version in (None, 1, "v1", "1.0", "1.0.0.0", "01.0.0"):
            with self.assertRaises(ValueError):
                self.desk.reconcile_checklist(version, self.checklist(), self.template())
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("9.9.9", self.checklist(), self.template())
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.0.0", self.checklist(), self.template())
        missing = ReleaseDesk(Path(self.temp.name) / "no-dir" / "releases.json")
        with self.assertRaises(ValueError):
            missing.reconcile_checklist("1.2.0", self.checklist(), self.template())
        self.assertFalse(missing.path.parent.exists())

    def test_invalid_checklist_and_template(self):
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", {"version": "1.2.0", "items": []}, self.template())
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", self.checklist(version="1.3.0"), self.template())
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", self.checklist(), {"items": []})
        # A checklist that is all optional is invalid on its own, even when the
        # new template would reset or drop every item.
        optional = {"version": "1.2.0", "items": [
            {"id": "docs", "text": "Write notes", "required": False, "status": "done"}]}
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", optional, self.template())
        # Template items the filter would drop are still fully validated.
        template = self.template()
        template["items"][2]["text"] = "A\nB"
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", self.checklist(), template)

    def test_empty_selection_and_no_required_rejected(self):
        only_added = {"items": [
            {"id": "a", "text": "A", "required": True, "categories": ["Added"]}]}
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", self.checklist(), only_added)
        no_required = {"items": [
            {"id": "docs", "text": "Write notes", "required": False},
            {"id": "fixed", "text": "Verify fix", "required": False, "categories": ["Fixed"]}]}
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", self.checklist(), no_required)

    def test_whole_store_validated(self):
        raw = b'{"1.2.0": [{"category": "Fixed", "text": "One"}], "9.9.9": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", self.checklist(), self.template())
        self.assertEqual(self.path.read_bytes(), raw)
        raw = b'{"1.2.0": [], "1.2.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            self.desk.reconcile_checklist("1.2.0", self.checklist(), self.template())
        self.assertEqual(str(caught.exception), "duplicate JSON object key")
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            self.desk.reconcile_checklist("1.2.0", self.checklist(), self.template())

    def test_readonly_and_inputs_untouched(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        checklist, template = self.checklist(), self.template()
        snapshot = json.loads(json.dumps({"checklist": checklist, "template": template}))
        result = self.desk.reconcile_checklist("1.2.0", checklist, template)
        self.assertEqual(result["retained"], ["docs", "fixed"])
        self.assertEqual({"checklist": checklist, "template": template}, snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_cli_reconcile_checklist(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        old = self.checklist()
        old["items"].append({"id": "legacy", "text": "Legacy", "required": False, "status": "done"})
        checklist.write_text(json.dumps(old), encoding="utf-8")
        new_template = {"items": [
            {"id": "docs", "text": "Write notes", "required": True},
            {"id": "fixed", "text": "Verify the fix", "required": True, "categories": ["Fixed"]},
            {"id": "added", "text": "Announce feature", "required": False, "categories": ["Added"]},
        ]}
        template.write_text(json.dumps(new_template), encoding="utf-8")
        result = subprocess.run(prefix + ["reconcile-checklist", "1.2.0", str(checklist), str(template)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {
            "version": "1.2.0",
            "items": [
                {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
                {"id": "fixed", "text": "Verify the fix", "required": True, "status": "pending"},
            ],
            "retained": ["docs"],
            "reset": ["fixed"],
            "added": [],
            "removed": ["legacy"],
        })
        # The reconciled checklist is accepted by check and audit-checklist.
        updated = Path(self.temp.name) / "updated.json"
        updated.write_text(result.stdout, encoding="utf-8")
        checked = subprocess.run(prefix + ["check", "1.2.0", str(updated)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertFalse(json.loads(checked.stdout)["ready"])
        audited = subprocess.run(prefix + ["audit-checklist", "1.2.0", str(updated), str(template)],
                                 capture_output=True, text=True)
        self.assertEqual(audited.returncode, 0, audited.stderr)
        report = json.loads(audited.stdout)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["mismatched"], [])
        self.assertEqual(report["unexpected"], [])

    def test_cli_reconcile_checklist_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        cases = [
            "{not json",
            b"\xff\xfe",
            json.dumps({"version": "2.0.0", "items": [
                {"id": "a", "text": "A", "required": True, "status": "done"}]}),
            '{"version": "1.2.0", "version": "1.2.0", "items": []}',
        ]
        for case in cases:
            if isinstance(case, bytes):
                checklist.write_bytes(case)
            else:
                checklist.write_text(case, encoding="utf-8")
            result = subprocess.run(prefix + ["reconcile-checklist", "1.2.0", str(checklist), str(template)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, case)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        checklist.write_text(json.dumps(self.checklist()), encoding="utf-8")
        duplicate_template = '{"items": [{"id": "a", "id": "a", "text": "A", "required": true}]}'
        template.write_text(duplicate_template, encoding="utf-8")
        result = subprocess.run(prefix + ["reconcile-checklist", "1.2.0", str(checklist), str(template)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), {"error": "duplicate JSON object key"})
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        for version in ("v1", "9.9.9"):
            result = subprocess.run(prefix + ["reconcile-checklist", version, str(checklist), str(template)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        absent = subprocess.run(
            prefix + ["reconcile-checklist", "1.2.0", str(Path(self.temp.name) / "nope.json"), str(template)],
            capture_output=True, text=True)
        self.assertEqual(absent.returncode, 2)
        self.assertIn("error", json.loads(absent.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # Input files are never modified.
        self.assertEqual(json.loads(checklist.read_text(encoding="utf-8")), self.checklist())
        # A missing store is treated as empty, fails as unknown, and is not created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        result = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "reconcile-checklist", "1.2.0", str(checklist), str(template)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


class MigrateChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.0.0", [
            {"category": "Added", "text": "Feature A"},
            {"category": "Fixed", "text": "Fix one"},
            {"category": "Fixed", "text": "Fix one"},
        ])
        # Added changed; one of the two duplicate Fixed entries disappeared.
        self.desk.add("2.0.0", [
            {"category": "Added", "text": "Feature A2"},
            {"category": "Fixed", "text": "Fix one"},
        ])
        # Only Added changed; Fixed entries, including the duplicate, survive.
        self.desk.add("3.0.0", [
            {"category": "Added", "text": "Feature A2"},
            {"category": "Fixed", "text": "Fix one"},
            {"category": "Fixed", "text": "Fix one"},
        ])
        # Fixed disappeared as a category; Added is identical to 1.0.0.
        self.desk.add("4.0.0", [{"category": "Added", "text": "Feature A"}])
        # Changed-only release.
        self.desk.add("5.0.0", [{"category": "Changed", "text": "Tweak"}])

    def template(self, **overrides):
        data = {"items": [
            {"id": "docs", "text": "Write notes", "required": True},
            {"id": "added", "text": "Check added", "required": True, "categories": ["Added"]},
            {"id": "fixed", "text": "Check fixed", "required": False, "categories": ["Fixed"]},
            {"id": "both", "text": "Check both", "required": False, "categories": ["Added", "Fixed"]},
            {"id": "changed", "text": "Check changed", "required": False, "categories": ["Changed"]},
        ]}
        data.update(overrides)
        return data

    def checklist(self, version="1.0.0", **overrides):
        data = {"version": version, "items": [
            {"id": " docs ", "text": " Write notes ", "required": True, "status": "done"},
            {"id": "added", "text": "Check added", "required": True, "status": "done"},
            {"id": "fixed", "text": "Check fixed", "required": False, "status": "done"},
            {"id": "both", "text": "Check both", "required": False, "status": "blocked"},
            {"id": "legacy", "text": "Legacy", "required": False, "status": "done"},
        ]}
        data.update(overrides)
        return data

    def test_report_shape_and_all_reset_when_scoped_changes_differ(self):
        result = self.desk.migrate_checklist("1.0.0", "2.0.0", self.checklist(), self.template())
        self.assertEqual(set(result),
                         {"baseVersion", "version", "items", "retained", "reset", "added", "removed"})
        self.assertEqual(result["baseVersion"], "1.0.0")
        self.assertEqual(result["version"], "2.0.0")
        self.assertEqual([item["id"] for item in result["items"]], ["docs", "added", "fixed", "both"])
        for item in result["items"]:
            self.assertEqual(set(item), {"id", "text", "required", "status"})
            self.assertEqual(item["status"], "pending")
        self.assertEqual(result["retained"], [])
        self.assertEqual(result["reset"], ["docs", "added", "fixed", "both"])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], ["legacy"])

    def test_only_added_change_keeps_fixed_done(self):
        result = self.desk.migrate_checklist("1.0.0", "3.0.0", self.checklist(), self.template())
        by_id = {item["id"]: item for item in result["items"]}
        self.assertEqual(result["retained"], ["fixed"])
        self.assertEqual(result["reset"], ["docs", "added", "both"])
        self.assertEqual(by_id["fixed"]["status"], "done")
        self.assertEqual(by_id["docs"]["status"], "pending")
        self.assertEqual(by_id["both"]["status"], "pending")

    def test_duplicate_fixed_count_change_resets_fixed(self):
        result = self.desk.migrate_checklist("1.0.0", "2.0.0", self.checklist(), self.template())
        self.assertIn("fixed", result["reset"])

    def test_multi_category_item_compares_every_declared_category(self):
        # 3.0.0 keeps Fixed but changes Added: the Added+Fixed item must reset.
        result = self.desk.migrate_checklist("1.0.0", "3.0.0", self.checklist(), self.template())
        self.assertIn("both", result["reset"])
        # When neither declared category changes (same release) the blocked status is kept.
        same = self.desk.migrate_checklist("1.0.0", "1.0.0", self.checklist(), self.template())
        both = next(item for item in same["items"] if item["id"] == "both")
        self.assertEqual(both["status"], "blocked")
        self.assertIn("both", same["retained"])

    def test_categories_not_declared_compares_all_categories(self):
        # 4.0.0 keeps Added identical but loses every Fixed entry: the generic
        # item compares all categories, Fixed included, and must reset. The
        # Added+Fixed item is still selected (Added is present in the target)
        # but resets because Fixed changed; only the Added-only item is retained.
        result = self.desk.migrate_checklist("1.0.0", "4.0.0", self.checklist(), self.template())
        self.assertEqual(result["retained"], ["added"])
        self.assertEqual(result["reset"], ["docs", "both"])
        self.assertEqual([item["id"] for item in result["items"]], ["docs", "added", "both"])
        self.assertEqual(result["removed"], ["fixed", "legacy"])

    def test_same_version_migration(self):
        result = self.desk.migrate_checklist("1.0.0", "1.0.0", self.checklist(), self.template())
        self.assertEqual(result["baseVersion"], "1.0.0")
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["retained"], ["docs", "added", "fixed", "both"])
        self.assertEqual(result["reset"], [])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], ["legacy"])

    def test_reverse_order_migration(self):
        result = self.desk.migrate_checklist(
            "3.0.0", "1.0.0", self.checklist(version="3.0.0"), self.template())
        self.assertEqual(result["retained"], ["fixed"])
        self.assertEqual(result["reset"], ["docs", "added", "both"])
        self.assertEqual(result["removed"], ["legacy"])

    def test_definition_changes_reset_even_without_category_changes(self):
        redefined = self.template()
        redefined["items"][1]["text"] = "Check the added feature"
        result = self.desk.migrate_checklist("1.0.0", "4.0.0", self.checklist(), redefined)
        self.assertIn("added", result["reset"])
        item = next(entry for entry in result["items"] if entry["id"] == "added")
        self.assertEqual(item, {"id": "added", "text": "Check the added feature",
                                "required": True, "status": "pending"})
        flipped = self.template()
        flipped["items"][1]["required"] = False
        result = self.desk.migrate_checklist("1.0.0", "4.0.0", self.checklist(), flipped)
        self.assertIn("added", result["reset"])
        self.assertFalse(next(entry for entry in result["items"] if entry["id"] == "added")["required"])

    def test_new_items_added_pending_in_template_order(self):
        template = {"items": [
            {"id": "gate", "text": "Gate build", "required": True},
            {"id": "fixed", "text": "Check fixed", "required": False, "categories": ["Fixed"]},
            {"id": "docs", "text": "Write notes", "required": True},
            {"id": "added", "text": "Check added", "required": True, "categories": ["Added"]},
            {"id": "tail", "text": "Tail", "required": False, "categories": ["Added", "Fixed"]},
        ]}
        result = self.desk.migrate_checklist("1.0.0", "3.0.0", self.checklist(), template)
        self.assertEqual(result["added"], ["gate", "tail"])
        self.assertEqual([item["id"] for item in result["items"]],
                         ["gate", "fixed", "docs", "added", "tail"])
        for new_id in ("gate", "tail"):
            self.assertEqual(next(item for item in result["items"] if item["id"] == new_id)["status"],
                             "pending")

    def test_removed_follows_old_checklist_order(self):
        checklist = self.checklist()
        checklist["items"] = [
            {"id": "gone1", "text": "Gone one", "required": True, "status": "done"},
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "gone2", "text": "Gone two", "required": False, "status": "blocked"},
        ]
        result = self.desk.migrate_checklist("1.0.0", "4.0.0", checklist, self.template())
        self.assertEqual(result["removed"], ["gone1", "gone2"])

    def test_change_arrays_always_present_even_when_empty(self):
        result = self.desk.migrate_checklist("1.0.0", "1.0.0", self.checklist(), self.template())
        for key in ("retained", "reset", "added", "removed"):
            self.assertIsInstance(result[key], list)

    def test_result_feeds_check_and_audit_for_target_version(self):
        result = self.desk.migrate_checklist("1.0.0", "2.0.0", self.checklist(), self.template())
        report = self.desk.checklist("2.0.0", result)
        self.assertEqual(set(report), {"version", "ready", "done", "pending", "blocked"})
        self.assertEqual(report["version"], "2.0.0")
        self.assertFalse(report["ready"])
        audit = self.desk.audit_checklist("2.0.0", result, self.template())
        self.assertEqual(audit["missing"], [])
        self.assertEqual(audit["mismatched"], [])
        self.assertEqual(audit["unexpected"], [])

    def test_ids_trimmed_case_sensitive_no_unicode_normalization(self):
        # The checklist helper already submits " docs " which trims to docs.
        result = self.desk.migrate_checklist("1.0.0", "3.0.0", self.checklist(), self.template())
        self.assertIn("docs", result["reset"])
        upper = self.checklist()
        upper["items"][1]["id"] = "ADDED"
        result = self.desk.migrate_checklist("1.0.0", "3.0.0", upper, self.template())
        self.assertIn("added", result["added"])
        self.assertIn("ADDED", result["removed"])
        composed = "caf" + chr(0x00E9)
        decomposed = "caf" + "e" + chr(0x0301)
        template = {"items": [{"id": composed, "text": "Cafe", "required": True}]}
        checklist = {"version": "1.0.0", "items": [
            {"id": decomposed, "text": "Cafe", "required": True, "status": "done"}]}
        result = self.desk.migrate_checklist("1.0.0", "2.0.0", checklist, template)
        self.assertEqual(result["added"], [composed])
        self.assertEqual(result["removed"], [decomposed])

    def test_change_text_matching_trims_surrounding_whitespace(self):
        # Stored whitespace survives but diff comparison trims: Fixed still matches.
        self.path.write_text(json.dumps({
            "1.0.0": [{"category": "Fixed", "text": " Fix one "}],
            "3.0.0": [{"category": "Fixed", "text": "Fix one"}],
        }), encoding="utf-8")
        template = {"items": [{"id": "fixed", "text": "Check fixed",
                               "required": True, "categories": ["Fixed"]}]}
        checklist = {"version": "1.0.0", "items": [
            {"id": "fixed", "text": "Check fixed", "required": True, "status": "done"}]}
        result = self.desk.migrate_checklist("1.0.0", "3.0.0", checklist, template)
        self.assertEqual(result["retained"], ["fixed"])

    def test_invalid_versions_unknown_releases_and_mismatch(self):
        for base, target in ((None, "1.0.0"), ("v1", "1.0.0"), ("1.0.0", "1.0"),
                             ("01.0.0", "1.0.0"), ("1.0.0.0", "1.0.0")):
            with self.assertRaises(ValueError):
                self.desk.migrate_checklist(base, target, self.checklist(), self.template())
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("9.9.9", "2.0.0", self.checklist(), self.template())
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("1.0.0", "9.9.9", self.checklist(), self.template())
        # The checklist declares the base version, not the target.
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("2.0.0", "3.0.0", self.checklist(), self.template())
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("1.0.0", "2.0.0",
                                        self.checklist(version="2.0.0"), self.template())
        missing = ReleaseDesk(Path(self.temp.name) / "no-dir" / "releases.json")
        with self.assertRaises(ValueError):
            missing.migrate_checklist("1.0.0", "2.0.0", self.checklist(), self.template())
        self.assertFalse(missing.path.parent.exists())

    def test_invalid_checklist_and_template(self):
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("1.0.0", "2.0.0",
                                        {"version": "1.0.0", "items": []}, self.template())
        optional = {"version": "1.0.0", "items": [
            {"id": "a", "text": "A", "required": False, "status": "done"}]}
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("1.0.0", "2.0.0", optional, self.template())
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("1.0.0", "2.0.0", self.checklist(), {"items": []})
        # Template items the target filter would drop are still fully validated.
        template = self.template()
        template["items"][4]["text"] = "A\nB"
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("1.0.0", "2.0.0", self.checklist(), template)

    def test_empty_selection_and_no_required_rejected(self):
        only_added = {"items": [
            {"id": "a", "text": "A", "required": True, "categories": ["Added"]}]}
        with self.assertRaises(ValueError):
            # Target 5.0.0 only has Changed changes.
            self.desk.migrate_checklist("1.0.0", "5.0.0", self.checklist(), only_added)
        no_required = {"items": [
            {"id": "docs", "text": "Write notes", "required": False},
            {"id": "added", "text": "Check added", "required": False, "categories": ["Added"]}]}
        with self.assertRaises(ValueError):
            # Target 4.0.0 keeps the generic docs item, optional only.
            self.desk.migrate_checklist("1.0.0", "4.0.0", self.checklist(), no_required)

    def test_whole_store_validated(self):
        raw = b'{"1.0.0": [{"category": "Added", "text": "One"}], "9.9.9": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("1.0.0", "2.0.0", self.checklist(), self.template())
        self.assertEqual(self.path.read_bytes(), raw)
        raw = b'{"1.0.0": [], "1.0.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            self.desk.migrate_checklist("1.0.0", "2.0.0", self.checklist(), self.template())
        self.assertEqual(str(caught.exception), "duplicate JSON object key")
        self.path.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            self.desk.migrate_checklist("1.0.0", "2.0.0", self.checklist(), self.template())

    def test_readonly_inputs_untouched_and_result_detached(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        checklist, template = self.checklist(), self.template()
        snapshot = json.loads(json.dumps({"checklist": checklist, "template": template}))
        result = self.desk.migrate_checklist("1.0.0", "3.0.0", checklist, template)
        self.assertEqual(result["retained"], ["fixed"])
        self.assertEqual({"checklist": checklist, "template": template}, snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        # Mutating the result never reaches the passed payloads.
        result["items"][0]["status"] = "done"
        result["retained"].append("docs")
        self.assertEqual(checklist["items"][0]["status"], "done")
        again = self.desk.migrate_checklist("1.0.0", "3.0.0", checklist, template)
        self.assertEqual(again["retained"], ["fixed"])

    def test_cli_migrate_checklist(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        checklist.write_text(json.dumps(self.checklist()), encoding="utf-8")
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        result = subprocess.run(
            prefix + ["migrate-checklist", "1.0.0", "3.0.0", str(checklist), str(template)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["baseVersion"], "1.0.0")
        self.assertEqual(payload["version"], "3.0.0")
        self.assertEqual(payload["retained"], ["fixed"])
        self.assertEqual(payload["reset"], ["docs", "added", "both"])
        self.assertEqual(payload["removed"], ["legacy"])
        # The output is directly usable by check against the target version.
        migrated = Path(self.temp.name) / "migrated.json"
        migrated.write_text(result.stdout, encoding="utf-8")
        checked = subprocess.run(prefix + ["check", "3.0.0", str(migrated)],
                                 capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        audited = subprocess.run(
            prefix + ["audit-checklist", "3.0.0", str(migrated), str(template)],
            capture_output=True, text=True)
        self.assertEqual(audited.returncode, 0, audited.stderr)
        report = json.loads(audited.stdout)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["mismatched"], [])
        self.assertEqual(report["unexpected"], [])
        # Same-version and reverse-order migrations are accepted by the CLI.
        checklist_3 = Path(self.temp.name) / "checklist-3.json"
        checklist_3.write_text(json.dumps(self.checklist(version="3.0.0")), encoding="utf-8")
        same = subprocess.run(
            prefix + ["migrate-checklist", "3.0.0", "3.0.0", str(checklist_3), str(template)],
            capture_output=True, text=True)
        self.assertEqual(same.returncode, 0, same.stderr)

    def test_cli_migrate_checklist_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        cases = [
            "{not json",
            b"\xff\xfe",
            json.dumps({"version": "2.0.0", "items": [
                {"id": "a", "text": "A", "required": True, "status": "done"}]}),
            '{"version": "1.0.0", "version": "1.0.0", "items": []}',
        ]
        for case in cases:
            if isinstance(case, bytes):
                checklist.write_bytes(case)
            else:
                checklist.write_text(case, encoding="utf-8")
            result = subprocess.run(
                prefix + ["migrate-checklist", "1.0.0", "2.0.0", str(checklist), str(template)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, case)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        checklist.write_text(json.dumps(self.checklist()), encoding="utf-8")
        duplicate_template = '{"items": [{"id": "a", "id": "a", "text": "A", "required": true}]}'
        template.write_text(duplicate_template, encoding="utf-8")
        result = subprocess.run(
            prefix + ["migrate-checklist", "1.0.0", "2.0.0", str(checklist), str(template)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), {"error": "duplicate JSON object key"})
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        # Template syntax errors are reported just like checklist errors.
        template.write_text("{not json", encoding="utf-8")
        result = subprocess.run(
            prefix + ["migrate-checklist", "1.0.0", "2.0.0", str(checklist), str(template)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(set(json.loads(result.stdout)), {"error"})
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        for base, target in (("v1", "2.0.0"), ("1.0.0", "9.9.9"), ("9.9.9", "2.0.0")):
            result = subprocess.run(
                prefix + ["migrate-checklist", base, target, str(checklist), str(template)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, (base, target))
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        absent = subprocess.run(
            prefix + ["migrate-checklist", "1.0.0", "2.0.0",
                      str(Path(self.temp.name) / "nope.json"), str(template)],
            capture_output=True, text=True)
        self.assertEqual(absent.returncode, 2)
        self.assertIn("error", json.loads(absent.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is treated as empty, fails as unknown, and is not created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        result = subprocess.run(
            [sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
             "migrate-checklist", "1.0.0", "2.0.0", str(checklist), str(template)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


class ConfigDiffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.changes = [{"category": "Added", "text": "One"}]
        self.desk.add("1.0.0", self.changes)
        self.desk.add("2.0.0", [{"category": "Fixed", "text": "Two"}])

    def test_added_removed_changed_shape(self):
        base = {"keep": 1, "gone": {"a": 1}, "flag": True, "name": "old"}
        target = {"keep": 1, "fresh": [1, 2], "flag": False, "name": "new"}
        result = self.desk.diff_config("1.0.0", "2.0.0", base, target)
        self.assertEqual(set(result), {"baseVersion", "targetVersion", "added", "removed", "changed"})
        self.assertEqual(result["baseVersion"], "1.0.0")
        self.assertEqual(result["targetVersion"], "2.0.0")
        self.assertEqual(result["added"], [{"path": "/fresh", "value": [1, 2]}])
        self.assertEqual(result["removed"], [{"path": "/gone", "value": {"a": 1}}])
        self.assertEqual(result["changed"], [
            {"path": "/flag", "before": True, "after": False},
            {"path": "/name", "before": "old", "after": "new"},
        ])

    def test_nested_objects_recurse_and_whole_subobject_is_one_entry(self):
        base = {"db": {"host": "local", "pool": {"min": 1, "max": 5}, "keep": [1]}, "only": {"x": 1}}
        target = {"db": {"host": "remote", "pool": {"min": 2, "max": 5}, "keep": [1]},
                  "new": {"deep": {"a": [True, None]}}}
        result = self.desk.diff_config("1.0.0", "2.0.0", base, target)
        self.assertEqual(result["added"], [{"path": "/new", "value": {"deep": {"a": [True, None]}}}])
        self.assertEqual(result["removed"], [{"path": "/only", "value": {"x": 1}}])
        self.assertEqual(result["changed"], [
            {"path": "/db/host", "before": "local", "after": "remote"},
            {"path": "/db/pool/min", "before": 1, "after": 2},
        ])

    def test_object_versus_scalar_compares_whole_value(self):
        result = self.desk.diff_config("1.0.0", "2.0.0",
                                      {"a": {"b": 1}, "c": 1}, {"a": 5, "c": {"d": 2}})
        self.assertEqual(result["changed"], [
            {"path": "/a", "before": {"b": 1}, "after": 5},
            {"path": "/c", "before": 1, "after": {"d": 2}},
        ])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])

    def test_arrays_not_split_order_counts_and_objects_follow_rules(self):
        base = {"list": [1, 2], "objs": [{"a": 1}, {"b": 2}], "nested": [[1], [2]]}
        target = {"list": [2, 1], "objs": [{"a": 1}, {"b": 2}], "nested": [[1], [3]]}
        result = self.desk.diff_config("1.0.0", "2.0.0", base, target)
        self.assertEqual(result["changed"], [
            {"path": "/list", "before": [1, 2], "after": [2, 1]},
            {"path": "/nested", "before": [[1], [2]], "after": [[1], [3]]},
        ])
        # Key order inside array objects is ignored, so /objs is unchanged.
        reordered = {"objs": [{"a": 1}, {"b": 2, "extra": None}]}
        base_two = {"objs": [{"a": 1}, {"b": 2, "extra": None}]}
        self.assertEqual(self.desk.diff_config("1.0.0", "2.0.0", base_two, reordered)["changed"], [])

    def test_scalar_semantics(self):
        cases = [
            ({"v": True}, {"v": 1}),
            ({"v": 1}, {"v": "1"}),
            ({"v": None}, {"v": False}),
        ]
        for base, target in cases:
            self.assertEqual(len(self.desk.diff_config("1.0.0", "2.0.0", base, target)["changed"]), 1)
        # null differs from a missing field.
        result = self.desk.diff_config("1.0.0", "2.0.0", {"a": None}, {})
        self.assertEqual(result["removed"], [{"path": "/a", "value": None}])
        result = self.desk.diff_config("1.0.0", "2.0.0", {}, {"a": None})
        self.assertEqual(result["added"], [{"path": "/a", "value": None}])
        # Numerically equal integers and floats compare equal.
        result = self.desk.diff_config("1.0.0", "2.0.0", {"v": 1, "w": 1.5}, {"v": 1.0, "w": 1.50})
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["added"], [])
        # But genuinely different numbers and strings change.
        result = self.desk.diff_config("1.0.0", "2.0.0", {"v": 1}, {"v": 1.1})
        self.assertEqual(len(result["changed"]), 1)

    def test_pointer_escaping_empty_key_and_sorting(self):
        base = {"": 1, "a~b": 1, "a/b": 1, "z": {"中": 1, "a": 1}, "A": 1}
        target = {"": 2, "a~b": 2, "a/b": 2, "z": {"中": 2}, "B": 1}
        result = self.desk.diff_config("1.0.0", "2.0.0", base, target)
        self.assertEqual(result["added"], [{"path": "/B", "value": 1}])
        self.assertEqual(result["removed"], [{"path": "/A", "value": 1}, {"path": "/z/a", "value": 1}])
        self.assertEqual(result["changed"], [
            {"path": "/", "before": 1, "after": 2},
            {"path": "/a~0b", "before": 1, "after": 2},
            {"path": "/a~1b", "before": 1, "after": 2},
            {"path": "/z/中", "before": 1, "after": 2},
        ])
        for group in (result["added"], result["removed"], result["changed"]):
            paths = [entry["path"] for entry in group]
            self.assertEqual(paths, sorted(paths))

    def test_case_sensitive_keys_and_key_order_ignored(self):
        base = {"Key": 1, "order": {"b": 2, "a": 1}}
        target = {"key": 1, "order": {"a": 1, "b": 2}}
        result = self.desk.diff_config("1.0.0", "2.0.0", base, target)
        self.assertEqual(result["added"], [{"path": "/key", "value": 1}])
        self.assertEqual(result["removed"], [{"path": "/Key", "value": 1}])
        self.assertEqual(result["changed"], [])

    def test_identical_configs_empty_arrays(self):
        config = {"a": {"b": [1, True, None, "x"]}, "c": []}
        result = self.desk.diff_config("1.0.0", "1.0.0", config, dict(config))
        self.assertEqual(result, {"baseVersion": "1.0.0", "targetVersion": "1.0.0",
                                  "added": [], "removed": [], "changed": []})
        empty = self.desk.diff_config("1.0.0", "2.0.0", {}, {})
        self.assertEqual((empty["added"], empty["removed"], empty["changed"]), ([], [], []))

    def test_same_version_and_reverse_comparison(self):
        base = {"only_base": 1, "shared": "x"}
        target = {"only_target": 2, "shared": "y"}
        same = self.desk.diff_config("1.0.0", "1.0.0", base, target)
        self.assertEqual(same["added"], [{"path": "/only_target", "value": 2}])
        self.assertEqual(same["removed"], [{"path": "/only_base", "value": 1}])
        reverse = self.desk.diff_config("2.0.0", "1.0.0", target, base)
        self.assertEqual(reverse["added"], [{"path": "/only_base", "value": 1}])
        self.assertEqual(reverse["removed"], [{"path": "/only_target", "value": 2}])
        self.assertEqual(reverse["changed"], [{"path": "/shared", "before": "y", "after": "x"}])

    def test_invalid_and_unknown_versions(self):
        for base, target in ((None, "1.0.0"), ("v1", "1.0.0"), ("1.0", "1.0.0"),
                             ("1.0.0.0", "1.0.0"), ("01.0.0", "1.0.0")):
            with self.assertRaises(ValueError):
                self.desk.diff_config(base, target, {}, {})
        for base, target in (("9.9.9", "1.0.0"), ("1.0.0", "9.9.9")):
            with self.assertRaises(ValueError) as caught:
                self.desk.diff_config(base, target, {}, {})
            self.assertEqual(str(caught.exception), "unknown release")

    def test_invalid_store_still_rejected(self):
        raw = json.dumps({"1.0.0": [{"category": "Added", "text": "One"}], "2.0.0": []}).encode()
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.desk.diff_config("1.0.0", "2.0.0", {}, {})
        raw = b'{"1.0.0": [], "1.0.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            self.desk.diff_config("1.0.0", "2.0.0", {}, {})
        self.assertEqual(str(caught.exception), "duplicate JSON object key")

    def test_missing_store_is_empty_and_unknown(self):
        missing = Path(self.temp.name) / "no-dir" / "releases.json"
        desk = ReleaseDesk(missing)
        with self.assertRaises(ValueError):
            desk.diff_config("1.0.0", "2.0.0", {}, {})
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_non_object_roots(self):
        for value in (None, [], "x", 1, True, [{}]):
            with self.assertRaises(ValueError):
                self.desk.diff_config("1.0.0", "2.0.0", value, {})
            with self.assertRaises(ValueError):
                self.desk.diff_config("1.0.0", "2.0.0", {}, value)

    def test_non_string_keys_non_json_values_and_non_finite(self):
        cases = [
            {1: "x"},
            {"a": object()},
            {"a": {1, 2}},
            {"a": (1,)},
            {"a": float("nan")},
            {"a": float("inf")},
            {"a": [float("-inf")]},
            {"a": [{"b": object()}]},
        ]
        for config in cases:
            with self.assertRaises(ValueError):
                self.desk.diff_config("1.0.0", "2.0.0", config, {})
            with self.assertRaises(ValueError):
                self.desk.diff_config("1.0.0", "2.0.0", {}, config)

    def test_circular_references_rejected(self):
        cycle = {"a": 1}
        cycle["self"] = cycle
        with self.assertRaises(ValueError):
            self.desk.diff_config("1.0.0", "2.0.0", cycle, {})
        array_cycle = [1]
        array_cycle.append(array_cycle)
        with self.assertRaises(ValueError):
            self.desk.diff_config("1.0.0", "2.0.0", {"a": array_cycle}, {})
        # An acyclic shared subobject is fine.
        shared = {"x": 1}
        diamond = {"a": shared, "b": {"c": shared}}
        self.assertEqual(self.desk.diff_config("1.0.0", "2.0.0", diamond, diamond)["changed"], [])

    def test_readonly_inputs_store_and_files_untouched(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        base = {"gone": {"a": [1]}, "v": 1}
        target = {"fresh": {"b": 2}, "v": 2}
        base_snapshot = json.loads(json.dumps(base))
        target_snapshot = json.loads(json.dumps(target))
        result = self.desk.diff_config("1.0.0", "2.0.0", base, target)
        self.assertTrue(result["added"] and result["removed"] and result["changed"])
        self.assertEqual(base, base_snapshot)
        self.assertEqual(target, target_snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_cli_diff_config(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        base_file = Path(self.temp.name) / "base.json"
        target_file = Path(self.temp.name) / "target.json"
        base_file.write_text(json.dumps({"db": {"host": "local", "pool": 2}, "dropped": True}), encoding="utf-8")
        target_file.write_text(json.dumps({"db": {"host": "remote", "pool": 2}, "added": None}), encoding="utf-8")
        result = subprocess.run(prefix + ["diff-config", "1.0.0", "2.0.0",
                                          str(base_file), str(target_file)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {
            "baseVersion": "1.0.0",
            "targetVersion": "2.0.0",
            "added": [{"path": "/added", "value": None}],
            "removed": [{"path": "/dropped", "value": True}],
            "changed": [{"path": "/db/host", "before": "local", "after": "remote"}],
        })
        identical = json.dumps({"a": 1})
        base_file.write_text(identical, encoding="utf-8")
        target_file.write_text(identical, encoding="utf-8")
        same = subprocess.run(prefix + ["diff-config", "2.0.0", "2.0.0",
                                        str(base_file), str(target_file)],
                              capture_output=True, text=True)
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertEqual(json.loads(same.stdout)["changed"], [])

    def test_cli_diff_config_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        base_file = Path(self.temp.name) / "base.json"
        target_file = Path(self.temp.name) / "target.json"
        good = json.dumps({"a": 1})
        cases = [
            ("{not json", good),
            (b"\xff\xfe", good),
            (good, '{"a": 1, "a": 2}'),
            (json.dumps([1, 2]), good),
            (json.dumps({"a": float("nan")}), good),
        ]
        for base_raw, target_raw in cases:
            if isinstance(base_raw, bytes):
                base_file.write_bytes(base_raw)
            else:
                base_file.write_text(base_raw, encoding="utf-8")
            target_file.write_text(target_raw, encoding="utf-8")
            failed = subprocess.run(prefix + ["diff-config", "1.0.0", "2.0.0",
                                              str(base_file), str(target_file)],
                                    capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, (base_raw, target_raw))
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        target_file.write_text(good, encoding="utf-8")
        for arguments in (
            ["diff-config", "v1", "2.0.0", str(base_file), str(target_file)],
            ["diff-config", "1.0.0", "9.9.9", str(base_file), str(target_file)],
            ["diff-config", "1.0.0", "2.0.0", str(Path(self.temp.name) / "nope.json"), str(target_file)],
        ):
            base_file.write_text(good, encoding="utf-8")
            failed = subprocess.run(prefix + arguments, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, arguments)
            self.assertIn("error", json.loads(failed.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is treated as empty and is not created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        failed = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "diff-config", "1.0.0", "2.0.0", str(base_file), str(target_file)],
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stdout))
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


class ConfigPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        self.desk.add("2.0.0", [{"category": "Fixed", "text": "Two"}])

    def preview(self, base, target, current, base_version="1.0.0", target_version="2.0.0"):
        return self.desk.preview_config(base_version, target_version, base, target, current)

    def test_report_shape_and_disjoint_changes_both_kept(self):
        result = self.preview(
            {"port": 80, "timeout": 30},
            {"port": 8080, "timeout": 30},
            {"port": 80, "timeout": 60})
        self.assertEqual(set(result), {"baseVersion", "targetVersion", "canApply", "config", "conflicts"})
        self.assertEqual(result["baseVersion"], "1.0.0")
        self.assertEqual(result["targetVersion"], "2.0.0")
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"port": 8080, "timeout": 60})
        self.assertEqual(result["conflicts"], [])

    def test_same_field_changed_differently_conflicts_and_keeps_current(self):
        result = self.preview({"port": 80}, {"port": 8080}, {"port": 9000})
        self.assertFalse(result["canApply"])
        self.assertEqual(result["config"], {"port": 9000})
        self.assertEqual(result["conflicts"], [{
            "path": "/port",
            "base": {"present": True, "value": 80},
            "target": {"present": True, "value": 8080},
            "current": {"present": True, "value": 9000}}])

    def test_current_matching_target_is_kept_even_when_plan_changes(self):
        result = self.preview({"port": 80}, {"port": 8080}, {"port": 8080})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"port": 8080})

    def test_current_matching_base_adopts_target(self):
        result = self.preview({"port": 80, "name": "old"}, {"port": 8080, "name": "new"},
                              {"port": 80, "name": "old"})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"port": 8080, "name": "new"})

    def test_additions_and_deletions(self):
        added = self.preview({"a": 1}, {"a": 1, "n": 2}, {"a": 1})
        self.assertEqual(added["config"], {"a": 1, "n": 2})
        removed = self.preview({"a": 1, "g": 9}, {"a": 1}, {"a": 1, "g": 9})
        self.assertEqual(removed["config"], {"a": 1})
        # The current side deleting the same field independently is fine.
        both_removed = self.preview({"a": 1, "g": 9}, {"a": 1}, {"a": 1})
        self.assertTrue(both_removed["canApply"])
        self.assertEqual(both_removed["config"], {"a": 1})
        # Independently adding the same key to different values conflicts.
        both_added = self.preview({}, {"k": "T"}, {"k": "C"})
        self.assertFalse(both_added["canApply"])
        self.assertEqual(both_added["config"], {"k": "C"})
        self.assertEqual(both_added["conflicts"][0]["path"], "/k")

    def test_plan_unchanged_fields_keep_current_including_local_edits(self):
        result = self.preview({"a": 1, "b": 2}, {"a": 1, "b": 2}, {"a": 9, "b": 2, "mine": True})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"a": 9, "b": 2, "mine": True})

    def test_nested_objects_preview_per_subfield(self):
        base = {"db": {"host": "h", "port": 1, "keep": "x"}}
        target = {"db": {"host": "h2", "port": 1, "keep": "x"}}
        current = {"db": {"host": "h", "port": 2, "keep": "x"}}
        result = self.preview(base, target, current)
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"db": {"host": "h2", "port": 2, "keep": "x"}})
        # Same nested subkey changed differently conflicts at the subpath only.
        clash = self.preview({"db": {"port": 1}}, {"db": {"port": 2}}, {"db": {"port": 3}})
        self.assertFalse(clash["canApply"])
        self.assertEqual(clash["config"], {"db": {"port": 3}})
        self.assertEqual([entry["path"] for entry in clash["conflicts"]], ["/db/port"])

    def test_whole_subobjects_added_or_removed_never_split(self):
        adopted = self.preview({}, {"o": {"x": 1}}, {})
        self.assertTrue(adopted["canApply"])
        self.assertEqual(adopted["config"], {"o": {"x": 1}})
        clash = self.preview({}, {"o": {"x": 1}}, {"o": 2})
        self.assertFalse(clash["canApply"])
        self.assertEqual(clash["config"], {"o": 2})
        self.assertEqual([entry["path"] for entry in clash["conflicts"]], ["/o"])
        delete_vs_edit = self.preview({"o": {"x": 1}}, {}, {"o": {"x": 2}})
        self.assertFalse(delete_vs_edit["canApply"])
        self.assertEqual(delete_vs_edit["config"], {"o": {"x": 2}})
        self.assertEqual([entry["path"] for entry in delete_vs_edit["conflicts"]], ["/o"])

    def test_arrays_and_type_changes_are_whole_conflicts(self):
        array_clash = self.preview({"l": [1]}, {"l": [1, 2]}, {"l": [1, 3]})
        self.assertEqual([entry["path"] for entry in array_clash["conflicts"]], ["/l"])
        self.assertEqual(array_clash["config"], {"l": [1, 3]})
        # An array the current side did not touch adopts the target array.
        array_adopt = self.preview({"l": [1]}, {"l": [1, 2]}, {"l": [1]})
        self.assertTrue(array_adopt["canApply"])
        self.assertEqual(array_adopt["config"], {"l": [1, 2]})
        type_clash = self.preview({"a": 1}, {"a": {"x": 1}}, {"a": 2})
        self.assertEqual([entry["path"] for entry in type_clash["conflicts"]], ["/a"])
        self.assertEqual(type_clash["config"], {"a": 2})

    def test_missing_differs_from_null(self):
        # Target drops a null the current side never changed: deletion adopted.
        result = self.preview({"a": None}, {}, {"a": None})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {})
        # Target adds a null onto a current config without the field.
        result = self.preview({}, {"a": None}, {})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"a": None})
        # Current holds null while the target changes the base scalar: conflict.
        result = self.preview({"a": 1}, {"a": 2}, {"a": None})
        self.assertFalse(result["canApply"])
        side = result["conflicts"][0]
        self.assertEqual(side["current"], {"present": True, "value": None})

    def test_conflict_sides_only_carry_value_when_present(self):
        result = self.preview({"a": 1}, {"a": 1, "b": 2}, {"a": 1, "c": 3})
        # Plan adds b, current adds c: no overlap, both enter the preview.
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"a": 1, "b": 2, "c": 3})
        clash = self.preview({"a": 1}, {}, {"a": 2})
        entry = clash["conflicts"][0]
        self.assertEqual(set(entry), {"path", "base", "target", "current"})
        self.assertEqual(entry["target"], {"present": False})
        self.assertNotIn("value", entry["target"])
        self.assertEqual(entry["base"], {"present": True, "value": 1})

    def test_conflicts_sorted_by_unicode_code_point(self):
        base = {"": 0, "a~b": 0, "a/b": 0, "z": 0, "A": 0, "中": 0}
        target = {k: 1 for k in base}
        current = {k: 2 for k in base}
        result = self.preview(base, target, current)
        paths = [entry["path"] for entry in result["conflicts"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(paths), 6)
        self.assertEqual(paths[0], "/")

    def test_unrelated_changes_still_enter_config_with_conflicts(self):
        result = self.preview(
            {"port": 80, "timeout": 30, "note": "x"},
            {"port": 8080, "timeout": 30, "note": "y"},
            {"port": 9000, "timeout": 60, "note": "x"})
        self.assertFalse(result["canApply"])
        self.assertEqual(result["config"], {"port": 9000, "timeout": 60, "note": "y"})
        self.assertEqual([entry["path"] for entry in result["conflicts"]], ["/port"])

    def test_same_version_and_reverse_order_allowed(self):
        base = {"a": 1}
        target = {"a": 2}
        same = self.preview(base, target, dict(base), "1.0.0", "1.0.0")
        self.assertEqual(same["config"], {"a": 2})
        reverse = self.preview(target, base, dict(target), "2.0.0", "1.0.0")
        self.assertEqual(reverse["config"], {"a": 1})

    def test_invalid_and_unknown_versions(self):
        for base, target in ((None, "1.0.0"), ("v1", "1.0.0"), ("1.0", "1.0.0"),
                             ("1.0.0.0", "1.0.0"), ("01.0.0", "1.0.0")):
            with self.assertRaises(ValueError):
                self.desk.preview_config(base, target, {}, {}, {})
        for base, target in (("9.9.9", "1.0.0"), ("1.0.0", "9.9.9")):
            with self.assertRaises(ValueError) as caught:
                self.desk.preview_config(base, target, {}, {}, {})
            self.assertEqual(str(caught.exception), "unknown release")

    def test_all_three_configs_validated(self):
        good = {"a": 1}
        for bad in (None, [], "x", 1, True, {1: "x"}, {"a": float("nan")}, {"a": object()}):
            with self.assertRaises(ValueError):
                self.desk.preview_config("1.0.0", "2.0.0", bad, good, good)
            with self.assertRaises(ValueError):
                self.desk.preview_config("1.0.0", "2.0.0", good, bad, good)
            with self.assertRaises(ValueError):
                self.desk.preview_config("1.0.0", "2.0.0", good, good, bad)
        cycle = {"a": 1}
        cycle["self"] = cycle
        with self.assertRaises(ValueError):
            self.desk.preview_config("1.0.0", "2.0.0", good, good, cycle)

    def test_invalid_store_rejected_and_missing_store_unknown(self):
        raw = b'{"1.0.0": [], "1.0.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            self.preview({}, {}, {})
        self.assertEqual(str(caught.exception), "duplicate JSON object key")
        missing = Path(self.temp.name) / "no-dir" / "releases.json"
        desk = ReleaseDesk(missing)
        with self.assertRaises(ValueError):
            desk.preview_config("1.0.0", "2.0.0", {}, {}, {})
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_readonly_inputs_store_and_files_untouched(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        base = {"port": 80, "gone": {"a": [1]}}
        target = {"port": 8080}
        current = {"port": 9000, "local": 5}
        snapshots = [json.loads(json.dumps(payload)) for payload in (base, target, current)]
        result = self.preview(base, target, current)
        self.assertFalse(result["canApply"])
        self.assertEqual(base, snapshots[0])
        self.assertEqual(target, snapshots[1])
        self.assertEqual(current, snapshots[2])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_cli_preview_config(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        base_file = Path(self.temp.name) / "base.json"
        target_file = Path(self.temp.name) / "target.json"
        current_file = Path(self.temp.name) / "current.json"
        base_file.write_text(json.dumps({"port": 80, "timeout": 30}), encoding="utf-8")
        target_file.write_text(json.dumps({"port": 8080, "timeout": 30}), encoding="utf-8")
        current_file.write_text(json.dumps({"port": 80, "timeout": 60}), encoding="utf-8")
        result = subprocess.run(prefix + ["preview-config", "1.0.0", "2.0.0",
                                          str(base_file), str(target_file), str(current_file)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {
            "baseVersion": "1.0.0", "targetVersion": "2.0.0", "canApply": True,
            "config": {"port": 8080, "timeout": 60}, "conflicts": []})
        # Conflicts still print the result as a single line with exit 0.
        current_file.write_text(json.dumps({"port": 9000, "timeout": 30}), encoding="utf-8")
        conflicted = subprocess.run(prefix + ["preview-config", "1.0.0", "2.0.0",
                                              str(base_file), str(target_file), str(current_file)],
                                    capture_output=True, text=True)
        self.assertEqual(conflicted.returncode, 0, conflicted.stderr)
        payload = json.loads(conflicted.stdout)
        self.assertFalse(payload["canApply"])
        self.assertEqual(payload["config"], {"port": 9000, "timeout": 30})
        self.assertEqual(len(payload["conflicts"]), 1)

    def test_cli_preview_config_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        base_file = Path(self.temp.name) / "base.json"
        target_file = Path(self.temp.name) / "target.json"
        current_file = Path(self.temp.name) / "current.json"
        good = json.dumps({"a": 1})
        cases = [
            ("{not json", good, good),
            (b"\xff\xfe", good, good),
            (good, '{"a": 1, "a": 2}', good),
            (good, good, '{"a": {"b": 1, "b": 2, "b": 3}}'),
            (json.dumps([1, 2]), good, good),
            (good, json.dumps({"a": float("nan")}), good),
            (good, good, json.dumps(None)),
        ]
        for base_raw, target_raw, current_raw in cases:
            for path, raw in ((base_file, base_raw), (target_file, target_raw), (current_file, current_raw)):
                if isinstance(raw, bytes):
                    path.write_bytes(raw)
                else:
                    path.write_text(raw, encoding="utf-8")
            failed = subprocess.run(prefix + ["preview-config", "1.0.0", "2.0.0",
                                              str(base_file), str(target_file), str(current_file)],
                                    capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, (base_raw, target_raw, current_raw))
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        for arguments in (
            ["preview-config", "v1", "2.0.0", str(base_file), str(target_file), str(current_file)],
            ["preview-config", "1.0.0", "9.9.9", str(base_file), str(target_file), str(current_file)],
            ["preview-config", "1.0.0", "2.0.0", str(Path(self.temp.name) / "nope.json"),
             str(target_file), str(current_file)],
        ):
            base_file.write_text(good, encoding="utf-8")
            target_file.write_text(good, encoding="utf-8")
            current_file.write_text(good, encoding="utf-8")
            failed = subprocess.run(prefix + arguments, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, arguments)
            self.assertIn("error", json.loads(failed.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is treated as empty, reports unknown versions, and is not created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        failed = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "preview-config", "1.0.0", "2.0.0",
                                 str(base_file), str(target_file), str(current_file)],
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout), {"error": "unknown release"})
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


class ConfigResolveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        self.desk.add("2.0.0", [{"category": "Fixed", "text": "Two"}])

    def resolve(self, base, target, current, decisions, base_version="1.0.0", target_version="2.0.0"):
        return self.desk.resolve_config(base_version, target_version, base, target, current, decisions)

    def conflict_paths(self, base, target, current):
        preview = self.desk.preview_config("1.0.0", "2.0.0", base, target, current)
        return [entry["path"] for entry in preview["conflicts"]]

    def test_report_shape_empty_decisions_matches_preview(self):
        base = {"port": 80}, {"port": 8080}, {"port": 9000}
        result = self.resolve(*base, {})
        self.assertEqual(set(result), {"baseVersion", "targetVersion", "canApply",
                                       "config", "conflicts", "resolved"})
        self.assertFalse(result["canApply"])
        self.assertEqual(result["config"], {"port": 9000})
        self.assertEqual(result["resolved"], [])
        preview = self.desk.preview_config("1.0.0", "2.0.0", *base)
        self.assertEqual(result["config"], preview["config"])
        self.assertEqual(result["conflicts"], preview["conflicts"])

    def test_choose_target_adopts_full_target_state(self):
        result = self.resolve({"port": 80}, {"port": 8080}, {"port": 9000}, {"/port": "target"})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["config"], {"port": 8080})
        self.assertEqual(result["resolved"], [{"path": "/port", "choice": "target"}])

    def test_choose_current_keeps_current_state(self):
        result = self.resolve({"port": 80}, {"port": 8080}, {"port": 9000}, {"/port": "current"})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"port": 9000})
        self.assertEqual(result["resolved"], [{"path": "/port", "choice": "current"}])

    def test_missing_chosen_side_deletes_field(self):
        # Target deletes a field the current side edited: choosing target drops it.
        target_delete = self.resolve({"a": 1, "k": 9}, {"a": 1}, {"a": 1, "k": 2},
                                     {"/k": "target"})
        self.assertEqual(target_delete["config"], {"a": 1})
        self.assertTrue(target_delete["canApply"])
        # Current lacks a field the plan changed: choosing current leaves it absent.
        current_missing = self.resolve({"a": 1}, {"a": 2}, {}, {"/a": "current"})
        self.assertEqual(current_missing["config"], {})
        self.assertTrue(current_missing["canApply"])

    def test_null_side_value_is_kept_as_value(self):
        result = self.resolve({"a": 1}, {"a": None}, {"a": 2}, {"/a": "target"})
        self.assertIn("a", result["config"])
        self.assertIsNone(result["config"]["a"])
        self.assertTrue(result["canApply"])

    def test_partial_choices_leave_remaining_conflicts(self):
        base = {"port": 80, "timeout": 30, "note": "x"}
        target = {"port": 8080, "timeout": 3, "note": "y"}
        current = {"port": 9000, "timeout": 60, "note": "x"}
        self.assertEqual(self.conflict_paths(base, target, current), ["/port", "/timeout"])
        result = self.resolve(base, target, current, {"/port": "target"})
        self.assertFalse(result["canApply"])
        self.assertEqual(result["config"], {"port": 8080, "timeout": 60, "note": "y"})
        self.assertEqual([entry["path"] for entry in result["conflicts"]], ["/timeout"])
        self.assertEqual(result["resolved"], [{"path": "/port", "choice": "target"}])

    def test_unrelated_changes_and_independent_edits_kept(self):
        result = self.resolve(
            {"port": 80, "timeout": 30},
            {"port": 8080, "timeout": 30},
            {"port": 9000, "timeout": 60, "mine": True},
            {"/port": "target"})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"port": 8080, "timeout": 60, "mine": True})

    def test_arrays_type_changes_and_whole_objects_decided_as_whole(self):
        array_result = self.resolve({"l": [1]}, {"l": [1, 2]}, {"l": [1, 3]}, {"/l": "target"})
        self.assertEqual(array_result["config"], {"l": [1, 2]})
        type_result = self.resolve({"a": 1}, {"a": {"x": 1}}, {"a": 2}, {"/a": "current"})
        self.assertEqual(type_result["config"], {"a": 2})
        object_result = self.resolve({}, {"o": {"x": 1}}, {"o": 2}, {"/o": "target"})
        self.assertEqual(object_result["config"], {"o": {"x": 1}})

    def test_subpaths_of_whole_conflicts_are_unknown_paths(self):
        base, target, current = {"db": {"port": 1}}, {"db": [1]}, {"db": {"port": 2}}
        self.assertEqual(self.conflict_paths(base, target, current), ["/db"])
        with self.assertRaises(ValueError):
            self.resolve(base, target, current, {"/db/port": "target"})

    def test_nested_conflict_decided_independently(self):
        base = {"db": {"host": "h", "port": 1}}
        target = {"db": {"host": "h2", "port": 2}}
        current = {"db": {"host": "C", "port": 3}}
        result = self.resolve(base, target, current, {"/db/host": "target", "/db/port": "current"})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"db": {"host": "h2", "port": 3}})
        paths = [entry["path"] for entry in result["resolved"]]
        self.assertEqual(paths, ["/db/host", "/db/port"])

    def test_paths_matched_verbatim_with_pointer_escaping(self):
        base = {"a~b": 1, "a/b": 1, "": 1}
        target = {"a~b": 2, "a/b": 2, "": 2}
        current = {"a~b": 3, "a/b": 3, "": 3}
        result = self.resolve(base, target, current,
                              {"/a~0b": "target", "/a~1b": "current", "/": "target"})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"a~b": 2, "a/b": 3, "": 2})
        for raw in ("/a~b", "/a/b", "", " ", "/a~0b "):
            with self.assertRaises(ValueError):
                self.resolve(base, target, current, {raw: "target"})

    def test_conflicts_and_resolved_sorted_by_unicode_code_point(self):
        keys = ["", "a~b", "a/b", "z", "A", "中"]
        base = {key: 0 for key in keys}
        target = {key: 1 for key in keys}
        current = {key: 2 for key in keys}
        decisions = {"/" + key.replace("~", "~0").replace("/", "~1"): "target" for key in keys[:3]}
        result = self.resolve(base, target, current, decisions)
        resolved_paths = [entry["path"] for entry in result["resolved"]]
        conflict_paths = [entry["path"] for entry in result["conflicts"]]
        self.assertEqual(resolved_paths, sorted(resolved_paths))
        self.assertEqual(conflict_paths, sorted(conflict_paths))
        self.assertEqual(len(conflict_paths), 3)

    def test_invalid_decisions(self):
        base, target, current = {"a": 1}, {"a": 2}, {"a": 3}
        for decisions in ([], None, "x", 1, True):
            with self.assertRaises(ValueError):
                self.resolve(base, target, current, decisions)
        with self.assertRaises(ValueError):
            self.resolve(base, target, current, {1: "target"})
        for choice in ("yes", "", "TARGET", "Current", None, 0, True, ["target"]):
            with self.assertRaises(ValueError):
                self.resolve(base, target, current, {"/a": choice})
        for path in ("/b", "/a/x", "", "a", "/a/"):
            with self.assertRaises(ValueError):
                self.resolve(base, target, current, {path: "target"})

    def test_custom_value_confirms_new_scalar(self):
        # The documented example: 80 vs 8080 vs 9000, confirm 9001, keep timeout.
        result = self.resolve(
            {"port": 80, "timeout": 30}, {"port": 8080, "timeout": 30},
            {"port": 9000, "timeout": 60},
            {"/port": {"present": True, "value": 9001}})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"port": 9001, "timeout": 60})
        self.assertEqual(result["resolved"], [{"path": "/port", "choice": "custom"}])
        self.assertEqual(result["conflicts"], [])

    def test_custom_value_equal_to_a_side_is_still_handled_custom(self):
        for value in (8080, 9000):
            result = self.resolve({"port": 80}, {"port": 8080}, {"port": 9000},
                                  {"/port": {"present": True, "value": value}})
            self.assertEqual(result["config"], {"port": value})
            self.assertTrue(result["canApply"])
            self.assertEqual(result["resolved"], [{"path": "/port", "choice": "custom"}])

    def test_custom_value_accepts_all_json_types_including_null(self):
        cases = (None, True, "x", [1, 2], {"nested": {"k": [True, None]}})
        for value in cases:
            result = self.resolve({"a": 1}, {"a": 2}, {"a": 3},
                                  {"/a": {"present": True, "value": value}})
            self.assertEqual(result["config"], {"a": value})
            self.assertEqual(result["resolved"], [{"path": "/a", "choice": "custom"}])
        null_result = self.resolve({"a": 1}, {"a": 2}, {"a": 3},
                                   {"/a": {"present": True, "value": None}})
        self.assertIn("a", null_result["config"])
        self.assertIsNone(null_result["config"]["a"])

    def test_custom_present_false_deletes_field(self):
        result = self.resolve({"port": 80, "keep": 1}, {"port": 8080, "keep": 1},
                              {"port": 9000, "keep": 1},
                              {"/port": {"present": False}})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"keep": 1})
        self.assertEqual(result["resolved"], [{"path": "/port", "choice": "custom"}])

    def test_custom_value_replaces_objects_arrays_and_types_wholesale(self):
        # A custom object never recursively merges into an existing object.
        object_result = self.resolve(
            {"db": {"host": "h", "port": 1}}, {"db": {"host": "h2", "port": 2}},
            {"db": {"host": "C", "port": 3}},
            {"/db/host": {"present": True, "value": {"only": "this"}}})
        self.assertEqual(object_result["config"],
                         {"db": {"host": {"only": "this"}, "port": 3}})
        # Whole-object conflict replaced, no subpath merge.
        whole = self.resolve({"db": {"x": 1}}, {"db": [1]}, {"db": {"x": 2}},
                             {"/db": {"present": True, "value": {"z": 0}}})
        self.assertEqual(whole["config"], {"db": {"z": 0}})
        # Type changes replace outright.
        typed = self.resolve({"a": 1}, {"a": 2}, {"a": 3},
                             {"/a": {"present": True, "value": ["now", "array"]}})
        self.assertEqual(typed["config"], {"a": ["now", "array"]})

    def test_mixed_string_and_custom_decisions(self):
        result = self.resolve(
            {"port": 80, "timeout": 30, "note": "x"},
            {"port": 8080, "timeout": 3, "note": "y"},
            {"port": 9000, "timeout": 60, "note": "x"},
            {"/port": "target", "/timeout": {"present": True, "value": 45}})
        self.assertTrue(result["canApply"])
        self.assertEqual(result["config"], {"port": 8080, "timeout": 45, "note": "y"})
        self.assertEqual(result["resolved"],
                         [{"path": "/port", "choice": "target"},
                          {"path": "/timeout", "choice": "custom"}])
        # A custom choice can be partial just like a string choice.
        partial = self.resolve(
            {"port": 80, "timeout": 30}, {"port": 8080, "timeout": 3},
            {"port": 9000, "timeout": 60},
            {"/port": {"present": False}})
        self.assertFalse(partial["canApply"])
        self.assertEqual(partial["config"], {"timeout": 60})
        self.assertEqual([entry["path"] for entry in partial["conflicts"]], ["/timeout"])

    def test_custom_path_must_be_exact_conflict_path(self):
        base, target, current = {"db": {"port": 1}}, {"db": [1]}, {"db": {"port": 2}}
        custom = {"present": True, "value": 1}
        with self.assertRaises(ValueError):
            self.resolve(base, target, current, {"/db/port": custom})
        with self.assertRaises(ValueError):
            self.resolve({"a": 1}, {"a": 2}, {"a": 3}, {"/b": custom})
        with self.assertRaises(ValueError):
            self.resolve({"a": 1}, {"a": 2}, {"a": 3}, {"": custom})

    def test_invalid_custom_decision_objects(self):
        base, target, current = {"a": 1}, {"a": 2}, {"a": 3}
        invalid_objects = (
            {},
            {"value": 1},
            {"present": True},
            {"present": False, "value": 1},
            {"present": 1},
            {"present": 0},
            {"present": "true"},
            {"present": None},
            {"present": True, "value": 1, "extra": 2},
            {"present": False, "extra": 2},
            {"present": True, "value": float("nan")},
            {"present": True, "value": float("inf")},
            {"present": True, "value": object()},
            {"present": True, "value": {1: 2}},
            {"present": True, "value": [float("-inf")]},
            {"present": True, "value": {"k": {"n": object()}}},
        )
        for choice in invalid_objects:
            with self.assertRaises(ValueError, msg=choice):
                self.resolve(base, target, current, {"/a": choice})
        cycle = {"x": 1}
        cycle["self"] = cycle
        with self.assertRaises(ValueError):
            self.resolve(base, target, current, {"/a": {"present": True, "value": cycle}})

    def test_custom_decisions_inputs_untouched_and_result_detached(self):
        base, target, current = {"a": 1}, {"a": 2}, {"a": 3}
        value = {"nested": [1, 2]}
        decisions = {"/a": {"present": True, "value": value}}
        snapshot = json.loads(json.dumps(decisions))
        result = self.resolve(base, target, current, decisions)
        self.assertEqual(decisions, snapshot)
        # Mutating the returned config never reaches the decisions object.
        result["config"]["a"]["nested"].append(3)
        self.assertEqual(decisions["/a"]["value"], {"nested": [1, 2]})
        # Mutating the input after the call never reaches the result.
        decisions["/a"]["value"]["nested"].append(99)
        self.assertEqual(result["config"]["a"]["nested"], [1, 2, 3])

    def test_versions_store_and_configs_validated_like_preview(self):
        good = {"a": 1}
        for base_version, target_version in ((None, "1.0.0"), ("v1", "1.0.0"),
                                             ("9.9.9", "1.0.0"), ("1.0.0", "9.9.9")):
            with self.assertRaises(ValueError):
                self.desk.resolve_config(base_version, target_version, good, good, good, {})
        for bad in (None, [], "x", 1, True, {1: "x"}, {"a": float("nan")}, {"a": object()}):
            with self.assertRaises(ValueError):
                self.desk.resolve_config("1.0.0", "2.0.0", bad, good, good, {})
            with self.assertRaises(ValueError):
                self.desk.resolve_config("1.0.0", "2.0.0", good, bad, good, {})
            with self.assertRaises(ValueError):
                self.desk.resolve_config("1.0.0", "2.0.0", good, good, bad, {})
        raw = b'{"1.0.0": [], "1.0.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.resolve(good, good, good, {})

    def test_same_version_and_reverse_order_allowed(self):
        same = self.resolve({"a": 1}, {"a": 2}, {"a": 3}, {"/a": "target"}, "1.0.0", "1.0.0")
        self.assertEqual(same["config"], {"a": 2})
        reverse = self.resolve({"a": 2}, {"a": 1}, {"a": 3}, {"/a": "current"}, "2.0.0", "1.0.0")
        self.assertEqual(reverse["config"], {"a": 3})

    def test_readonly_inputs_store_untouched_and_results_detached(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        base = {"port": 80, "gone": {"a": [1]}}
        target = {"port": 8080, "gone": {"a": [2]}}
        current = {"port": 9000, "gone": {"a": [3]}}
        decisions = {"/port": "target", "/gone/a": "current"}
        snapshots = [json.loads(json.dumps(payload)) for payload in (base, target, current)]
        result = self.resolve(base, target, current, dict(decisions))
        self.assertTrue(result["canApply"])
        self.assertEqual(base, snapshots[0])
        self.assertEqual(target, snapshots[1])
        self.assertEqual(current, snapshots[2])
        self.assertEqual(decisions, {"/port": "target", "/gone/a": "current"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        # Mutating the result never reaches the input objects.
        result["config"]["gone"]["a"].append(4)
        self.assertEqual(current["gone"]["a"], [3])

    def test_cli_resolve_config(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        base_file = Path(self.temp.name) / "base.json"
        target_file = Path(self.temp.name) / "target.json"
        current_file = Path(self.temp.name) / "current.json"
        decisions_file = Path(self.temp.name) / "decisions.json"
        base_file.write_text(json.dumps({"port": 80, "timeout": 30}), encoding="utf-8")
        target_file.write_text(json.dumps({"port": 8080, "timeout": 30}), encoding="utf-8")
        current_file.write_text(json.dumps({"port": 9000, "timeout": 60}), encoding="utf-8")
        decisions_file.write_text(json.dumps({"/port": "target"}), encoding="utf-8")
        result = subprocess.run(prefix + ["resolve-config", "1.0.0", "2.0.0",
                                          str(base_file), str(target_file),
                                          str(current_file), str(decisions_file)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {
            "baseVersion": "1.0.0", "targetVersion": "2.0.0", "canApply": True,
            "config": {"port": 8080, "timeout": 60},
            "conflicts": [], "resolved": [{"path": "/port", "choice": "target"}]})
        # A target that also changes timeout leaves one unchosen conflict.
        target_file.write_text(json.dumps({"port": 8080, "timeout": 3}), encoding="utf-8")
        partial = subprocess.run(prefix + ["resolve-config", "1.0.0", "2.0.0",
                                           str(base_file), str(target_file),
                                           str(current_file), str(decisions_file)],
                                 capture_output=True, text=True)
        self.assertEqual(partial.returncode, 0, partial.stderr)
        payload = json.loads(partial.stdout)
        self.assertFalse(payload["canApply"])
        self.assertEqual([entry["path"] for entry in payload["conflicts"]], ["/timeout"])
        # Empty decisions are valid and still exit 0.
        decisions_file.write_text("{}", encoding="utf-8")
        empty = subprocess.run(prefix + ["resolve-config", "1.0.0", "2.0.0",
                                         str(base_file), str(target_file),
                                         str(current_file), str(decisions_file)],
                               capture_output=True, text=True)
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertEqual(json.loads(empty.stdout)["resolved"], [])
        # A custom decision confirms a new value and keeps independent edits.
        target_file.write_text(json.dumps({"port": 8080, "timeout": 30}), encoding="utf-8")
        decisions_file.write_text(json.dumps({"/port": {"present": True, "value": 9001}}),
                                  encoding="utf-8")
        custom = subprocess.run(prefix + ["resolve-config", "1.0.0", "2.0.0",
                                          str(base_file), str(target_file),
                                          str(current_file), str(decisions_file)],
                                capture_output=True, text=True)
        self.assertEqual(custom.returncode, 0, custom.stderr)
        self.assertEqual(json.loads(custom.stdout), {
            "baseVersion": "1.0.0", "targetVersion": "2.0.0", "canApply": True,
            "config": {"port": 9001, "timeout": 60},
            "conflicts": [], "resolved": [{"path": "/port", "choice": "custom"}]})
        # Strings and custom objects may be mixed in the same decisions file;
        # present false deletes the field, and the exit stays 0 with a conflict.
        target_file.write_text(json.dumps({"port": 8080, "timeout": 3}), encoding="utf-8")
        decisions_file.write_text(json.dumps({"/port": {"present": False}}), encoding="utf-8")
        deleted = subprocess.run(prefix + ["resolve-config", "1.0.0", "2.0.0",
                                           str(base_file), str(target_file),
                                           str(current_file), str(decisions_file)],
                                 capture_output=True, text=True)
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        deleted_payload = json.loads(deleted.stdout)
        self.assertFalse(deleted_payload["canApply"])
        self.assertEqual(deleted_payload["config"], {"timeout": 60})
        self.assertEqual(deleted_payload["resolved"], [{"path": "/port", "choice": "custom"}])

    def test_cli_resolve_config_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        base_file = Path(self.temp.name) / "base.json"
        target_file = Path(self.temp.name) / "target.json"
        current_file = Path(self.temp.name) / "current.json"
        decisions_file = Path(self.temp.name) / "decisions.json"
        good = json.dumps({"a": 1})
        cases = [
            ("{not json",),
            (b"\xff\xfe",),
            ('{"x": 1, "x": 2}',),
            (json.dumps([1, 2]),),
            (json.dumps(None),),
            (json.dumps({"/a": "yes"}),),
            (json.dumps({"/b": "target"}),),
            (json.dumps({"/a": {"present": True}}),),
            (json.dumps({"/a": {"present": False, "value": 1}}),),
            (json.dumps({"/a": {"present": "true", "value": 1}}),),
            (json.dumps({"/a": {"present": 1, "value": 1}}),),
            (json.dumps({"/a": {"present": True, "value": 1, "extra": 2}}),),
            (json.dumps({"/a": {"present": True, "value": 1e999}}),),
            ('{"/a": {"present": true, "value": {"x": 1, "x": 2}}}',),
            ('{"/a": {"present": true, "value": [1]}, "/a": "current"}',),
        ]
        base_file.write_text(good, encoding="utf-8")
        target_file.write_text(json.dumps({"a": 2}), encoding="utf-8")
        current_file.write_text(json.dumps({"a": 3}), encoding="utf-8")
        for (raw,) in cases:
            if isinstance(raw, bytes):
                decisions_file.write_bytes(raw)
            else:
                decisions_file.write_text(raw, encoding="utf-8")
            failed = subprocess.run(prefix + ["resolve-config", "1.0.0", "2.0.0",
                                             str(base_file), str(target_file),
                                             str(current_file), str(decisions_file)],
                                   capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, raw)
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        # A missing decisions file fails with OSError and creates nothing.
        missing = Path(self.temp.name) / "nope.json"
        failed = subprocess.run(prefix + ["resolve-config", "1.0.0", "2.0.0",
                                         str(base_file), str(target_file),
                                         str(current_file), str(missing)],
                               capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stdout))
        self.assertFalse(missing.exists())
        # Bad versions fail like preview-config.
        decisions_file.write_text("{}", encoding="utf-8")
        for arguments in (
            ["resolve-config", "v1", "2.0.0", str(base_file), str(target_file),
             str(current_file), str(decisions_file)],
            ["resolve-config", "1.0.0", "9.9.9", str(base_file), str(target_file),
             str(current_file), str(decisions_file)],
        ):
            failed = subprocess.run(prefix + arguments, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, arguments)
            self.assertIn("error", json.loads(failed.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is treated as empty, reports unknown versions, and is not created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        failed = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "resolve-config", "1.0.0", "2.0.0",
                                 str(base_file), str(target_file),
                                 str(current_file), str(decisions_file)],
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout), {"error": "unknown release"})
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


class ConfigApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        self.desk.add("2.0.0", [{"category": "Fixed", "text": "Two"}])
        self.current = Path(self.temp.name) / "current.json"

    def write_current(self, config):
        self.current.write_text(json.dumps(config), encoding="utf-8")

    def apply(self, base, target, expected, decisions, base_version="1.0.0", target_version="2.0.0"):
        return self.desk.apply_config(base_version, target_version, base, target,
                                      expected, decisions, self.current)

    def test_report_shape_and_file_replaced_on_change(self):
        self.write_current({"port": 9000, "timeout": 60})
        result = self.apply({"port": 80, "timeout": 30}, {"port": 8080, "timeout": 30},
                            {"port": 9000, "timeout": 60}, {"/port": "target"})
        self.assertEqual(set(result), {"baseVersion", "targetVersion", "changed",
                                       "config", "resolved"})
        self.assertTrue(result["changed"])
        self.assertEqual(result["config"], {"port": 8080, "timeout": 60})
        self.assertEqual(result["resolved"], [{"path": "/port", "choice": "target"}])
        raw = self.current.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(json.loads(raw.decode("utf-8")), {"port": 8080, "timeout": 60})

    def test_no_change_keeps_bytes_and_mtime(self):
        self.write_current({"port": 8080, "timeout": 60})
        before = self.current.read_bytes()
        mtime = self.current.stat().st_mtime_ns
        result = self.apply({"port": 80, "timeout": 30}, {"port": 8080, "timeout": 30},
                            {"port": 8080, "timeout": 60}, {})
        self.assertFalse(result["changed"])
        self.assertEqual(result["config"], {"port": 8080, "timeout": 60})
        self.assertEqual(result["resolved"], [])
        self.assertEqual(self.current.read_bytes(), before)
        self.assertEqual(self.current.stat().st_mtime_ns, mtime)

    def test_semantic_equality_ignores_key_order_and_number_type(self):
        self.current.write_text('{"b": 1.0, "a": [true, null]}', encoding="utf-8")
        before = self.current.read_bytes()
        result = self.apply({"a": [True, None], "b": 1}, {"a": [True, None], "b": 1},
                            {"b": 1, "a": [True, None]}, {})
        self.assertFalse(result["changed"])
        self.assertEqual(self.current.read_bytes(), before)

    def test_expected_mismatch_rejected_without_write(self):
        self.write_current({"port": 9000})
        before = self.current.read_bytes()
        with self.assertRaises(ValueError):
            self.apply({"port": 80}, {"port": 8080}, {"port": 80}, {})
        with self.assertRaises(ValueError):
            self.apply({"port": 80}, {"port": 8080}, {"port": 9000, "extra": 1}, {})
        self.assertEqual(self.current.read_bytes(), before)

    def test_unresolved_conflicts_rejected_without_write(self):
        self.write_current({"port": 9000})
        before = self.current.read_bytes()
        with self.assertRaises(ValueError):
            self.apply({"port": 80}, {"port": 8080}, {"port": 9000}, {})
        with self.assertRaises(ValueError):
            self.apply({"port": 80, "timeout": 30}, {"port": 8080, "timeout": 3},
                       {"port": 9000, "timeout": 60}, {"/port": "target"})
        self.assertEqual(self.current.read_bytes(), before)

    def test_empty_decisions_valid_without_conflicts(self):
        self.write_current({"port": 80, "mine": True})
        result = self.apply({"port": 80}, {"port": 8080}, {"port": 80, "mine": True}, {})
        self.assertTrue(result["changed"])
        self.assertEqual(result["config"], {"port": 8080, "mine": True})
        self.assertEqual(result["resolved"], [])

    def test_current_and_custom_choices(self):
        self.write_current({"port": 9000, "timeout": 60})
        kept = self.apply({"port": 80, "timeout": 30}, {"port": 8080, "timeout": 30},
                          {"port": 9000, "timeout": 60}, {"/port": "current"})
        self.assertFalse(kept["changed"])
        self.assertEqual(kept["config"], {"port": 9000, "timeout": 60})
        custom = self.apply({"port": 80, "timeout": 30}, {"port": 8080, "timeout": 30},
                            {"port": 9000, "timeout": 60},
                            {"/port": {"present": True, "value": 9001}})
        self.assertTrue(custom["changed"])
        self.assertEqual(custom["config"], {"port": 9001, "timeout": 60})
        self.assertEqual(custom["resolved"], [{"path": "/port", "choice": "custom"}])
        self.write_current({"port": 9000, "timeout": 60})
        deleted = self.apply({"port": 80, "timeout": 30}, {"port": 8080, "timeout": 30},
                             {"port": 9000, "timeout": 60}, {"/port": {"present": False}})
        self.assertEqual(deleted["config"], {"timeout": 60})

    def test_non_conflict_decision_paths_rejected_without_write(self):
        self.write_current({"a": 3})
        before = self.current.read_bytes()
        for decisions in ({"/b": "target"}, {"/a/x": "target"}, {"": "current"}):
            with self.assertRaises(ValueError):
                self.apply({"a": 1}, {"a": 2}, {"a": 3}, decisions)
        self.assertEqual(self.current.read_bytes(), before)

    def test_missing_target_raises_oserror_and_creates_nothing(self):
        missing = Path(self.temp.name) / "missing-dir" / "current.json"
        with self.assertRaises(OSError):
            self.desk.apply_config("1.0.0", "2.0.0", {"a": 1}, {"a": 2}, {"a": 1}, {}, missing)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_symlink_target_rejected(self):
        self.write_current({"a": 1})
        link = Path(self.temp.name) / "link.json"
        link.symlink_to(self.current)
        with self.assertRaises(ValueError):
            self.desk.apply_config("1.0.0", "2.0.0", {"a": 1}, {"a": 2}, {"a": 1}, {}, link)
        self.assertEqual(self.current.read_bytes(), json.dumps({"a": 1}).encode("utf-8"))

    def test_target_must_not_be_store_alias_or_hard_link(self):
        with self.assertRaises(ValueError):
            self.desk.apply_config("1.0.0", "2.0.0", {"a": 1}, {"a": 2}, {"a": 1}, {}, self.path)
        alias = Path(self.temp.name) / ".." / Path(self.temp.name).name / "releases.json"
        with self.assertRaises(ValueError):
            self.desk.apply_config("1.0.0", "2.0.0", {"a": 1}, {"a": 2}, {"a": 1}, {}, alias)
        hard = Path(self.temp.name) / "hard.json"
        os.link(self.path, hard)
        with self.assertRaises(ValueError):
            self.desk.apply_config("1.0.0", "2.0.0", {"a": 1}, {"a": 2}, {"a": 1}, {}, hard)
        # The store is never touched by these failures.
        self.assertEqual(self.desk.versions(), ["1.0.0", "2.0.0"])

    def test_invalid_versions_configs_decisions_and_store(self):
        self.write_current({"a": 1})
        good = {"a": 1}
        for base_version, target_version in ((None, "1.0.0"), ("v1", "1.0.0"),
                                             ("9.9.9", "1.0.0"), ("1.0.0", "9.9.9")):
            with self.assertRaises(ValueError):
                self.desk.apply_config(base_version, target_version, good, good, good, {}, self.current)
        for bad in (None, [], "x", 1, True, {1: "x"}, {"a": float("nan")}):
            for position in range(3):
                configs = [good, good, good]
                configs[position] = bad
                with self.assertRaises(ValueError):
                    self.desk.apply_config("1.0.0", "2.0.0", *configs, {}, self.current)
        for decisions in ([], None, "x", {"/a": "yes"}, {"/a": {"present": True}}):
            with self.assertRaises(ValueError):
                self.desk.apply_config("1.0.0", "2.0.0", good, good, good, decisions, self.current)
        raw = b'{"1.0.0": [], "1.0.0": []}'
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.apply(good, good, good, {})
        # A missing store is treated as empty and reports unknown releases.
        empty_desk = ReleaseDesk(Path(self.temp.name) / "nope" / "releases.json")
        with self.assertRaises(ValueError):
            empty_desk.apply_config("1.0.0", "2.0.0", good, good, good, {}, self.current)
        self.assertEqual(self.current.read_bytes(), json.dumps({"a": 1}).encode("utf-8"))

    def test_current_file_read_errors_preserve_bytes(self):
        store_before = self.path.read_bytes()
        for raw in (b"{not json", b"\xff\xfe", b'{"a": 1, "a": 2}',
                    b"[1, 2]", b"null", b"1", b"\"x\"", b""):
            self.current.write_bytes(raw)
            with self.assertRaises(ValueError, msg=raw):
                self.apply({"a": 1}, {"a": 2}, {"a": 1}, {})
            self.assertEqual(self.current.read_bytes(), raw)
        self.assertEqual(self.path.read_bytes(), store_before)

    def test_inputs_untouched_and_result_detached(self):
        self.write_current({"port": 9000, "gone": {"a": [3]}})
        store_before = self.path.read_bytes()
        base = {"port": 80, "gone": {"a": [1]}}
        target = {"port": 8080, "gone": {"a": [2]}}
        expected = {"port": 9000, "gone": {"a": [3]}}
        decisions = {"/port": "target", "/gone/a": {"present": True, "value": [9]}}
        snapshots = [json.loads(json.dumps(payload)) for payload in (base, target, expected, decisions)]
        result = self.apply(base, target, expected, decisions)
        self.assertEqual([base, target, expected, decisions], snapshots)
        self.assertEqual(self.path.read_bytes(), store_before)
        result["config"]["gone"]["a"].append(4)
        self.assertEqual(expected["gone"]["a"], [3])
        self.assertEqual(decisions["/gone/a"]["value"], [9])

    def test_same_version_and_reverse_order_allowed(self):
        self.write_current({"a": 3})
        same = self.apply({"a": 1}, {"a": 2}, {"a": 3}, {"/a": "target"}, "1.0.0", "1.0.0")
        self.assertEqual(same["config"], {"a": 2})
        self.write_current({"a": 3})
        reverse = self.apply({"a": 2}, {"a": 1}, {"a": 3}, {"/a": "current"}, "2.0.0", "1.0.0")
        self.assertFalse(reverse["changed"])
        self.assertEqual(reverse["config"], {"a": 3})

    def command(self, *extra):
        return [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path),
                "apply-config", *extra]

    def write_inputs(self, base, target, expected, decisions):
        files = {}
        for name, payload in (("base", base), ("target", target),
                              ("expected", expected), ("decisions", decisions)):
            file = Path(self.temp.name) / f"{name}.json"
            file.write_text(json.dumps(payload), encoding="utf-8")
            files[name] = file
        return files

    def test_cli_apply_config(self):
        files = self.write_inputs({"port": 80, "timeout": 30}, {"port": 8080, "timeout": 30},
                                  {"port": 9000, "timeout": 60}, {"/port": "target"})
        self.write_current({"port": 9000, "timeout": 60})
        paths = [str(files[name]) for name in ("base", "target", "expected", "decisions")]
        result = subprocess.run(self.command("1.0.0", "2.0.0", *paths, str(self.current)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {
            "baseVersion": "1.0.0", "targetVersion": "2.0.0", "changed": True,
            "config": {"port": 8080, "timeout": 60},
            "resolved": [{"path": "/port", "choice": "target"}]})
        self.assertEqual(json.loads(self.current.read_text(encoding="utf-8")),
                         {"port": 8080, "timeout": 60})
        # Applying the same plan again reports no change and keeps the bytes.
        before = self.current.read_bytes()
        again = subprocess.run(self.command("1.0.0", "2.0.0", *paths, str(self.current)),
                               capture_output=True, text=True)
        self.assertEqual(again.returncode, 2)
        self.assertIn("error", json.loads(again.stdout))
        self.assertEqual(self.current.read_bytes(), before)
        # A matching expected snapshot and no leftover decisions make the
        # second run a no-change success.
        files["expected"].write_text(json.dumps({"port": 8080, "timeout": 60}), encoding="utf-8")
        files["decisions"].write_text("{}", encoding="utf-8")
        steady = subprocess.run(self.command("1.0.0", "2.0.0", *paths, str(self.current)),
                                capture_output=True, text=True)
        self.assertEqual(steady.returncode, 0, steady.stderr)
        self.assertFalse(json.loads(steady.stdout)["changed"])
        self.assertEqual(self.current.read_bytes(), before)

    def test_cli_apply_config_errors(self):
        files = self.write_inputs({"a": 1}, {"a": 2}, {"a": 3}, {"/a": "target"})
        self.write_current({"a": 3})
        paths = [str(files[name]) for name in ("base", "target", "expected", "decisions")]
        before = self.current.read_bytes()
        store_before = self.path.read_bytes()
        # Bad versions fail before anything is read or written.
        for arguments in (self.command("v1", "2.0.0", *paths, str(self.current)),
                          self.command("1.0.0", "9.9.9", *paths, str(self.current))):
            failed = subprocess.run(arguments, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, arguments)
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        # Empty decisions leave the conflict unresolved.
        files["decisions"].write_text("{}", encoding="utf-8")
        failed = subprocess.run(self.command("1.0.0", "2.0.0", *paths, str(self.current)),
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        files["expected"].write_text(json.dumps({"a": 4}), encoding="utf-8")
        failed = subprocess.run(self.command("1.0.0", "2.0.0", *paths, str(self.current)),
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        # Malformed input files fail like everywhere else.
        files["expected"].write_text(json.dumps({"a": 3}), encoding="utf-8")
        files["decisions"].write_bytes(b'{"x": 1, "x": 2}')
        failed = subprocess.run(self.command("1.0.0", "2.0.0", *paths, str(self.current)),
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        # The target must not alias the store or any input file.
        files["decisions"].write_text(json.dumps({"/a": "target"}), encoding="utf-8")
        for target in (str(self.path), str(files["base"]), str(files["target"]),
                       str(files["expected"]), str(files["decisions"])):
            failed = subprocess.run(self.command("1.0.0", "2.0.0", *paths, target),
                                    capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, target)
            self.assertIn("error", json.loads(failed.stdout))
        # A missing target fails with an error and creates nothing.
        missing = Path(self.temp.name) / "missing-dir" / "current.json"
        failed = subprocess.run(self.command("1.0.0", "2.0.0", *paths, str(missing)),
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())
        self.assertEqual(self.current.read_bytes(), before)
        self.assertEqual(self.path.read_bytes(), store_before)


class UpdateChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.2.0", [{"category": "Added", "text": "One"},
                                {"category": "Fixed", "text": "Two"}])
        self.checklist = Path(self.temp.name) / "checklist.json"

    def write_checklist(self, items=None, version="1.2.0", raw=None):
        if raw is not None:
            self.checklist.write_bytes(raw)
            return
        if items is None:
            items = [
                {"id": "docs", "text": " Write notes ", "required": True, "status": "done"},
                {"id": "sign", "text": "Sign build", "required": True, "status": "pending"},
                {"id": "nice", "text": "Polish page", "required": False, "status": "blocked"},
            ]
        self.checklist.write_text(json.dumps({"version": version, "items": items}), encoding="utf-8")

    def test_updates_statuses_report_shape_and_file(self):
        self.write_checklist()
        result = self.desk.update_checklist("1.2.0", [
            {"id": "sign", "expected": "pending", "status": "done"},
            {"id": "nice", "expected": "blocked", "status": "pending"}], self.checklist)
        self.assertEqual(set(result), {"version", "changed", "items", "updated", "ready"})
        self.assertTrue(result["changed"])
        self.assertTrue(result["ready"])
        # updated follows checklist order, not updates-argument order.
        self.assertEqual(result["updated"], ["sign", "nice"])
        self.assertEqual(result["items"], [
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "sign", "text": "Sign build", "required": True, "status": "done"},
            {"id": "nice", "text": "Polish page", "required": False, "status": "pending"}])
        for item in result["items"]:
            self.assertEqual(set(item), {"id", "text", "required", "status"})
        raw = self.checklist.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        on_disk = json.loads(raw.decode("utf-8"))
        self.assertEqual(set(on_disk), {"version", "items"})
        self.assertEqual(on_disk["version"], "1.2.0")
        self.assertEqual([item["status"] for item in on_disk["items"]],
                         ["done", "done", "pending"])

    def test_no_change_keeps_bytes_and_mtime(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        mtime = self.checklist.stat().st_mtime_ns
        result = self.desk.update_checklist("1.2.0", [
            {"id": "docs", "expected": "done", "status": "done"},
            {"id": "sign", "expected": "pending", "status": "pending"}], self.checklist)
        self.assertFalse(result["changed"])
        self.assertEqual(result["updated"], [])
        self.assertFalse(result["ready"])
        self.assertEqual(self.checklist.read_bytes(), before)
        self.assertEqual(self.checklist.stat().st_mtime_ns, mtime)

    def test_empty_updates_change_nothing_but_validate_everything(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        result = self.desk.update_checklist("1.2.0", [], self.checklist)
        self.assertFalse(result["changed"])
        self.assertEqual(result["updated"], [])
        self.assertEqual(self.checklist.read_bytes(), before)
        for version in (None, 1, "v1", "1.0", "01.0.0"):
            with self.assertRaises(ValueError):
                self.desk.update_checklist(version, [], self.checklist)
        with self.assertRaises(ValueError):
            self.desk.update_checklist("9.9.9", [], self.checklist)

    def test_expected_status_mismatch_rejects_whole_batch(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        store_before = self.path.read_bytes()
        for updates in (
            [{"id": "sign", "expected": "done", "status": "done"}],
            [{"id": "docs", "expected": "pending", "status": "done"},
             {"id": "sign", "expected": "pending", "status": "done"}],
            # The expected status is checked even when nothing would change.
            [{"id": "docs", "expected": "blocked", "status": "done"}],
        ):
            with self.assertRaises(ValueError, msg=updates):
                self.desk.update_checklist("1.2.0", updates, self.checklist)
        self.assertEqual(self.checklist.read_bytes(), before)
        self.assertEqual(self.path.read_bytes(), store_before)

    def test_unknown_id_rejected_without_write(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [
                {"id": "docs", "expected": "done", "status": "pending"},
                {"id": "nope", "expected": "pending", "status": "done"}], self.checklist)
        self.assertEqual(self.checklist.read_bytes(), before)

    def test_invalid_update_structures(self):
        self.write_checklist()
        good = {"id": "docs", "expected": "done", "status": "pending"}
        cases = [
            None, {}, "x", 1, (),
            [None], [[]], ["x"], [1], [{}],
            [{"id": "docs", "expected": "done"}],
            [{"id": "docs", "status": "pending"}],
            [{"expected": "done", "status": "pending"}],
            [{"id": "docs", "expected": "done", "status": "pending", "extra": 1}],
            [{**good, "id": None}], [{**good, "id": 1}], [{**good, "id": "  "}],
            [{**good, "id": "a\nb"}], [{**good, "id": "a\rb"}],
            [{**good, "expected": "DONE"}], [{**good, "expected": None}],
            [{**good, "expected": 1}],
            [{**good, "status": "started"}], [{**good, "status": None}], [{**good, "status": True}],
        ]
        for updates in cases:
            with self.assertRaises(ValueError, msg=repr(updates)):
                self.desk.update_checklist("1.2.0", updates, self.checklist)

    def test_duplicate_ids_rejected_after_trim(self):
        self.write_checklist()
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [
                {"id": " docs ", "expected": "done", "status": "pending"},
                {"id": "docs", "expected": "done", "status": "blocked"}], self.checklist)

    def test_ids_case_sensitive_without_unicode_normalization(self):
        composed = "caf" + chr(0x00E9)
        decomposed = "caf" + "e" + chr(0x0301)
        self.write_checklist([
            {"id": "Same", "text": "A", "required": True, "status": "pending"},
            {"id": composed, "text": "B", "required": False, "status": "pending"}])
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [
                {"id": "same", "expected": "pending", "status": "done"}], self.checklist)
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [
                {"id": decomposed, "expected": "pending", "status": "done"}], self.checklist)
        result = self.desk.update_checklist("1.2.0", [
            {"id": " Same ", "expected": "pending", "status": "done"}], self.checklist)
        self.assertEqual(result["updated"], ["Same"])

    def test_any_status_can_switch_to_any_status(self):
        self.write_checklist([
            {"id": "a", "text": "A", "required": True, "status": "done"},
            {"id": "b", "text": "B", "required": False, "status": "pending"},
            {"id": "c", "text": "C", "required": False, "status": "blocked"}])
        for target in ("done", "pending", "blocked"):
            self.write_checklist([
                {"id": "a", "text": "A", "required": True, "status": "done"},
                {"id": "b", "text": "B", "required": False, "status": "pending"},
                {"id": "c", "text": "C", "required": False, "status": "blocked"}])
            result = self.desk.update_checklist("1.2.0", [
                {"id": "a", "expected": "done", "status": target},
                {"id": "b", "expected": "pending", "status": target},
                {"id": "c", "expected": "blocked", "status": target}], self.checklist)
            self.assertEqual([item["status"] for item in result["items"]],
                             [target, target, target])

    def test_version_mismatch_and_invalid_checklist(self):
        self.write_checklist(version="1.0.0")
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [], self.checklist)
        for raw in (b"{not json", b"\xff\xfe", b'{"version": "1.2.0", "version": "1.2.0", "items": []}',
                    b"[1, 2]", b"null", b"1", b'"x"', b"",
                    json.dumps({"version": "1.2.0", "items": []}).encode("utf-8"),
                    json.dumps({"version": "v1", "items": [
                        {"id": "a", "text": "A", "required": True, "status": "done"}]}).encode("utf-8")):
            self.write_checklist(raw=raw)
            with self.assertRaises(ValueError, msg=raw):
                self.desk.update_checklist("1.2.0", [], self.checklist)
            self.assertEqual(self.checklist.read_bytes(), raw)

    def test_missing_target_raises_oserror_and_creates_nothing(self):
        missing = Path(self.temp.name) / "no-dir" / "checklist.json"
        with self.assertRaises(OSError):
            self.desk.update_checklist("1.2.0", [], missing)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_symlink_target_rejected(self):
        self.write_checklist()
        link = Path(self.temp.name) / "link.json"
        link.symlink_to(self.checklist)
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [], link)

    def test_target_must_not_be_store_alias_or_hard_link(self):
        self.write_checklist()
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [], self.path)
        alias = Path(self.temp.name) / ".." / Path(self.temp.name).name / "releases.json"
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [], alias)
        hard = Path(self.temp.name) / "hard.json"
        os.link(self.path, hard)
        with self.assertRaises(ValueError):
            self.desk.update_checklist("1.2.0", [], hard)

    def test_missing_store_treated_as_empty(self):
        desk = ReleaseDesk(Path(self.temp.name) / "no-store" / "releases.json")
        self.write_checklist()
        with self.assertRaises(ValueError):
            desk.update_checklist("1.2.0", [], self.checklist)

    def test_inputs_untouched_and_result_detached(self):
        self.write_checklist()
        store_before = self.path.read_bytes()
        updates = [{"id": "sign", "expected": "pending", "status": "done"}]
        snapshot = json.loads(json.dumps(updates))
        result = self.desk.update_checklist("1.2.0", updates, self.checklist)
        self.assertEqual(updates, snapshot)
        result["items"][1]["status"] = "blocked"
        self.assertEqual(updates[0]["status"], "done")
        # The store is never modified.
        self.assertEqual(self.path.read_bytes(), store_before)

    def command(self, *extra):
        return [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path),
                "update-checklist", *extra]

    def test_cli_update_checklist(self):
        self.write_checklist()
        updates_file = Path(self.temp.name) / "updates.json"
        updates_file.write_text(json.dumps([
            {"id": "sign", "expected": "pending", "status": "done"}]), encoding="utf-8")
        result = subprocess.run(self.command("1.2.0", str(updates_file), str(self.checklist)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        report = json.loads(result.stdout)
        self.assertEqual(set(report), {"version", "changed", "items", "updated", "ready"})
        self.assertTrue(report["changed"])
        self.assertEqual(report["updated"], ["sign"])
        self.assertTrue(report["ready"])
        self.assertTrue(self.checklist.read_bytes().endswith(b"\n"))
        # A second identical batch verifies the stored status and changes nothing.
        before = self.checklist.read_bytes()
        mtime = self.checklist.stat().st_mtime_ns
        steady = subprocess.run(self.command("1.2.0", str(updates_file), str(self.checklist)),
                                capture_output=True, text=True)
        self.assertEqual(steady.returncode, 2)
        self.assertIn("error", json.loads(steady.stdout))
        # With the matching expected status the no-change run succeeds.
        updates_file.write_text(json.dumps([
            {"id": "sign", "expected": "done", "status": "done"}]), encoding="utf-8")
        steady = subprocess.run(self.command("1.2.0", str(updates_file), str(self.checklist)),
                                capture_output=True, text=True)
        self.assertEqual(steady.returncode, 0, steady.stderr)
        report = json.loads(steady.stdout)
        self.assertFalse(report["changed"])
        self.assertEqual(report["updated"], [])
        self.assertEqual(self.checklist.read_bytes(), before)
        self.assertEqual(self.checklist.stat().st_mtime_ns, mtime)
        # An empty batch is a valid single-line success.
        updates_file.write_text("[]", encoding="utf-8")
        empty = subprocess.run(self.command("1.2.0", str(updates_file), str(self.checklist)),
                               capture_output=True, text=True)
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertFalse(json.loads(empty.stdout)["changed"])

    def test_cli_update_checklist_errors(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        updates_file = Path(self.temp.name) / "updates.json"
        cases = [
            b"{not json",
            b"\xff\xfe",
            b'[{"id": "sign", "expected": "pending", "expected": "done", "status": "done"}]',
            json.dumps({}).encode("utf-8"),
            json.dumps([{"id": "sign", "expected": "blocked", "status": "done"}]).encode("utf-8"),
            json.dumps([{"id": "nope", "expected": "pending", "status": "done"}]).encode("utf-8"),
            json.dumps([{"id": "sign", "expected": "pending", "status": "weird"}]).encode("utf-8"),
        ]
        for raw in cases:
            updates_file.write_bytes(raw)
            failed = subprocess.run(self.command("1.2.0", str(updates_file), str(self.checklist)),
                                    capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, raw)
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
            self.assertEqual(self.checklist.read_bytes(), before)
        # Bad and unknown versions fail the same way.
        updates_file.write_text("[]", encoding="utf-8")
        for arguments in (self.command("v1", str(updates_file), str(self.checklist)),
                          self.command("9.9.9", str(updates_file), str(self.checklist))):
            failed = subprocess.run(arguments, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, arguments)
            self.assertIn("error", json.loads(failed.stdout))
        # The target checklist must not be the updates file (alias or hard link).
        same = subprocess.run(self.command("1.2.0", str(self.checklist), str(self.checklist)),
                              capture_output=True, text=True)
        self.assertEqual(same.returncode, 2)
        self.assertIn("error", json.loads(same.stdout))
        alias = Path(self.temp.name) / ".." / Path(self.temp.name).name / "updates.json"
        hard = Path(self.temp.name) / "hard.json"
        os.link(updates_file, hard)
        for target in (str(alias), str(hard)):
            failed = subprocess.run(self.command("1.2.0", str(updates_file), target),
                                    capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, target)
        # Pointing the target at the store is rejected by the API.
        failed = subprocess.run(self.command("1.2.0", str(updates_file), str(self.path)),
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        # A missing updates file or target fails with an error and creates nothing.
        missing_target = Path(self.temp.name) / "missing-dir" / "checklist.json"
        failed = subprocess.run(
            self.command("1.2.0", str(Path(self.temp.name) / "nope.json"),
                         str(self.checklist)), capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        failed = subprocess.run(
            self.command("1.2.0", str(updates_file), str(missing_target)),
            capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertFalse(missing_target.exists())
        self.assertFalse(missing_target.parent.exists())
        self.assertEqual(self.checklist.read_bytes(), before)


class PreviewUpdateChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.2.0", [{"category": "Added", "text": "One"},
                                {"category": "Fixed", "text": "Two"}])
        self.checklist = Path(self.temp.name) / "checklist.json"

    def write_checklist(self, items=None, version="1.2.0", raw=None):
        if raw is not None:
            self.checklist.write_bytes(raw)
            return
        if items is None:
            items = [
                {"id": "docs", "text": " Write notes ", "required": True, "status": "done"},
                {"id": "sign", "text": "Sign build", "required": True, "status": "pending"},
                {"id": "nice", "text": "Polish page", "required": False, "status": "blocked"},
            ]
        self.checklist.write_text(json.dumps({"version": version, "items": items}), encoding="utf-8")

    def test_preview_shape_and_planned_result(self):
        self.write_checklist()
        result = self.desk.preview_update_checklist("1.2.0", [
            {"id": "sign", "expected": "pending", "status": "done"},
            {"id": "nice", "expected": "blocked", "status": "pending"}], self.checklist)
        self.assertEqual(set(result),
                         {"version", "canUpdate", "changed", "items", "updated",
                          "ready", "conflicts"})
        self.assertTrue(result["canUpdate"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["conflicts"], [])
        # updated follows checklist order, not updates-argument order.
        self.assertEqual(result["updated"], ["sign", "nice"])
        self.assertEqual(result["items"], [
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "sign", "text": "Sign build", "required": True, "status": "done"},
            {"id": "nice", "text": "Polish page", "required": False, "status": "pending"}])
        for item in result["items"]:
            self.assertEqual(set(item), {"id", "text", "required", "status"})

    def test_preview_never_writes_even_when_changes_planned(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        mtime = self.checklist.stat().st_mtime_ns
        store_before = self.path.read_bytes()
        result = self.desk.preview_update_checklist("1.2.0", [
            {"id": "sign", "expected": "pending", "status": "done"}], self.checklist)
        self.assertTrue(result["changed"])
        self.assertEqual(self.checklist.read_bytes(), before)
        self.assertEqual(self.checklist.stat().st_mtime_ns, mtime)
        self.assertEqual(self.path.read_bytes(), store_before)

    def test_preview_empty_batch_validates_store_and_checklist(self):
        self.write_checklist()
        result = self.desk.preview_update_checklist("1.2.0", [], self.checklist)
        self.assertTrue(result["canUpdate"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["updated"], [])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual([item["status"] for item in result["items"]],
                         ["done", "pending", "blocked"])
        self.write_checklist(version="1.0.0")
        with self.assertRaises(ValueError):
            self.desk.preview_update_checklist("1.2.0", [], self.checklist)
        with self.assertRaises(ValueError):
            self.desk.preview_update_checklist("9.9.9", [], self.checklist)

    def test_preview_conflicts_reported_in_updates_order(self):
        self.write_checklist()
        result = self.desk.preview_update_checklist("1.2.0", [
            {"id": "sign", "expected": "pending", "status": "done"},
            {"id": "nope", "expected": "pending", "status": "done"},
            {"id": "docs", "expected": "blocked", "status": "pending"},
            {"id": " also-missing ", "expected": "done", "status": "blocked"}], self.checklist)
        self.assertFalse(result["canUpdate"])
        self.assertFalse(result["changed"])
        self.assertEqual(result["updated"], [])
        self.assertEqual(result["conflicts"], [
            {"id": "nope", "expected": "pending", "actual": None,
             "status": "done", "reason": "unknown-id"},
            {"id": "docs", "expected": "blocked", "actual": "done",
             "status": "pending", "reason": "status-mismatch"},
            {"id": "also-missing", "expected": "done", "actual": None,
             "status": "blocked", "reason": "unknown-id"}])
        for conflict in result["conflicts"]:
            self.assertEqual(set(conflict), {"id", "expected", "actual", "status", "reason"})
        # The otherwise-valid first update is not applied: items are the
        # normalized original checklist and ready reflects it.
        self.assertEqual(result["items"], [
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "sign", "text": "Sign build", "required": True, "status": "pending"},
            {"id": "nice", "text": "Polish page", "required": False, "status": "blocked"}])
        self.assertFalse(result["ready"])

    def test_preview_ready_reflects_original_even_with_conflict(self):
        self.write_checklist([
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "nice", "text": "Polish page", "required": False, "status": "pending"}])
        result = self.desk.preview_update_checklist("1.2.0", [
            {"id": "nice", "expected": "done", "status": "done"}], self.checklist)
        self.assertFalse(result["canUpdate"])
        self.assertEqual(result["conflicts"][0]["reason"], "status-mismatch")
        # All required items are done in the untouched original checklist.
        self.assertTrue(result["ready"])

    def test_preview_satisfied_target_still_checks_expected(self):
        self.write_checklist()
        # Wrong expected although actual already equals the target status.
        mismatch = self.desk.preview_update_checklist("1.2.0", [
            {"id": "docs", "expected": "blocked", "status": "done"}], self.checklist)
        self.assertFalse(mismatch["canUpdate"])
        self.assertEqual(mismatch["conflicts"], [
            {"id": "docs", "expected": "blocked", "actual": "done",
             "status": "done", "reason": "status-mismatch"}])
        # Correct expected with an already-satisfied target plans no change.
        steady = self.desk.preview_update_checklist("1.2.0", [
            {"id": "docs", "expected": "done", "status": "done"}], self.checklist)
        self.assertTrue(steady["canUpdate"])
        self.assertFalse(steady["changed"])
        self.assertEqual(steady["updated"], [])
        self.assertEqual(steady["conflicts"], [])

    def test_preview_matches_write_plan(self):
        self.write_checklist()
        updates = [
            {"id": "sign", "expected": "pending", "status": "done"},
            {"id": "nice", "expected": "blocked", "status": "pending"}]
        preview = self.desk.preview_update_checklist("1.2.0", updates, self.checklist)
        written = self.desk.update_checklist("1.2.0", updates, self.checklist)
        self.assertEqual(preview["items"], written["items"])
        self.assertEqual(preview["updated"], written["updated"])
        self.assertEqual(preview["ready"], written["ready"])
        self.assertEqual(preview["changed"], written["changed"])

    def test_preview_inputs_untouched_and_result_detached(self):
        self.write_checklist()
        updates = [{"id": "sign", "expected": "pending", "status": "done"}]
        snapshot = json.loads(json.dumps(updates))
        result = self.desk.preview_update_checklist("1.2.0", updates, self.checklist)
        result["items"][1]["status"] = "blocked"
        result["conflicts"].append({"id": "x"})
        self.assertEqual(updates, snapshot)
        again = self.desk.preview_update_checklist("1.2.0", updates, self.checklist)
        self.assertEqual(again["items"][1]["status"], "done")
        self.assertEqual(again["conflicts"], [])

    def test_preview_invalid_structures_and_duplicate_ids_raise(self):
        self.write_checklist()
        good = {"id": "docs", "expected": "done", "status": "pending"}
        cases = [
            None, {}, "x", 1,
            [None], [{}],
            [{"id": "docs", "expected": "done"}],
            [{"id": "docs", "expected": "done", "status": "pending", "extra": 1}],
            [{**good, "id": None}], [{**good, "id": "  "}], [{**good, "id": "a\nb"}],
            [{**good, "expected": "DONE"}], [{**good, "status": None}],
        ]
        for updates in cases:
            with self.assertRaises(ValueError, msg=repr(updates)):
                self.desk.preview_update_checklist("1.2.0", updates, self.checklist)
        with self.assertRaises(ValueError):
            self.desk.preview_update_checklist("1.2.0", [
                {"id": " docs ", "expected": "done", "status": "pending"},
                {"id": "docs", "expected": "done", "status": "blocked"}], self.checklist)

    def test_preview_invalid_versions_raise(self):
        self.write_checklist()
        for version in (None, 1, "v1", "1.0", "01.0.0"):
            with self.assertRaises(ValueError):
                self.desk.preview_update_checklist(version, [], self.checklist)

    def test_preview_bad_checklist_bytes_raise_and_preserve_file(self):
        for raw in (b"{not json", b"\xff\xfe",
                    b'{"version": "1.2.0", "version": "1.2.0", "items": []}',
                    b"[1, 2]", b"null", b"",
                    json.dumps({"version": "1.2.0", "items": []}).encode("utf-8")):
            self.write_checklist(raw=raw)
            with self.assertRaises(ValueError, msg=raw):
                self.desk.preview_update_checklist("1.2.0", [], self.checklist)
            self.assertEqual(self.checklist.read_bytes(), raw)

    def test_preview_path_rules_match_write(self):
        self.write_checklist()
        missing = Path(self.temp.name) / "no-dir" / "checklist.json"
        with self.assertRaises(OSError):
            self.desk.preview_update_checklist("1.2.0", [], missing)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())
        link = Path(self.temp.name) / "link.json"
        link.symlink_to(self.checklist)
        with self.assertRaises(ValueError):
            self.desk.preview_update_checklist("1.2.0", [], link)
        with self.assertRaises(ValueError):
            self.desk.preview_update_checklist("1.2.0", [], self.path)
        alias = Path(self.temp.name) / ".." / Path(self.temp.name).name / "releases.json"
        with self.assertRaises(ValueError):
            self.desk.preview_update_checklist("1.2.0", [], alias)
        hard = Path(self.temp.name) / "hard.json"
        os.link(self.path, hard)
        with self.assertRaises(ValueError):
            self.desk.preview_update_checklist("1.2.0", [], hard)

    def test_preview_missing_store_treated_as_empty(self):
        desk = ReleaseDesk(Path(self.temp.name) / "no-store" / "releases.json")
        self.write_checklist()
        with self.assertRaises(ValueError):
            desk.preview_update_checklist("1.2.0", [], self.checklist)

    def command(self, *extra):
        return [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path),
                "update-checklist", *extra]

    def test_cli_dry_run_plans_without_writing(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        mtime = self.checklist.stat().st_mtime_ns
        updates_file = Path(self.temp.name) / "updates.json"
        updates_file.write_text(json.dumps([
            {"id": "sign", "expected": "pending", "status": "done"}]), encoding="utf-8")
        result = subprocess.run(
            self.command("1.2.0", str(updates_file), str(self.checklist), "--dry-run"),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        report = json.loads(result.stdout)
        self.assertEqual(set(report),
                         {"version", "canUpdate", "changed", "items", "updated",
                          "ready", "conflicts"})
        self.assertTrue(report["canUpdate"])
        self.assertTrue(report["changed"])
        self.assertEqual(report["updated"], ["sign"])
        self.assertEqual(report["conflicts"], [])
        self.assertEqual(self.checklist.read_bytes(), before)
        self.assertEqual(self.checklist.stat().st_mtime_ns, mtime)
        # The plan predicts the real write, which still requires no flag.
        written = subprocess.run(
            self.command("1.2.0", str(updates_file), str(self.checklist)),
            capture_output=True, text=True)
        self.assertEqual(written.returncode, 0, written.stderr)
        self.assertEqual(set(json.loads(written.stdout)),
                         {"version", "changed", "items", "updated", "ready"})
        self.assertNotEqual(self.checklist.read_bytes(), before)

    def test_cli_dry_run_conflicts_exit_0(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        updates_file = Path(self.temp.name) / "updates.json"
        updates_file.write_text(json.dumps([
            {"id": "nope", "expected": "pending", "status": "done"},
            {"id": "docs", "expected": "blocked", "status": "pending"}]), encoding="utf-8")
        result = subprocess.run(
            self.command("1.2.0", str(updates_file), str(self.checklist), "--dry-run"),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["canUpdate"])
        self.assertFalse(report["changed"])
        self.assertEqual(report["updated"], [])
        self.assertEqual([c["reason"] for c in report["conflicts"]],
                         ["unknown-id", "status-mismatch"])
        self.assertIsNone(report["conflicts"][0]["actual"])
        self.assertEqual(self.checklist.read_bytes(), before)

    def test_cli_dry_run_empty_batch_exit_0(self):
        self.write_checklist()
        updates_file = Path(self.temp.name) / "updates.json"
        updates_file.write_text("[]", encoding="utf-8")
        result = subprocess.run(
            self.command("1.2.0", str(updates_file), str(self.checklist), "--dry-run"),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["canUpdate"])
        self.assertEqual(report["conflicts"], [])

    def test_cli_dry_run_errors_exit_2_and_write_nothing(self):
        self.write_checklist()
        before = self.checklist.read_bytes()
        updates_file = Path(self.temp.name) / "updates.json"
        cases = [
            b"{not json",
            b"\xff\xfe",
            b'[{"id": "sign", "expected": "pending", "expected": "done", "status": "done"}]',
            json.dumps({}).encode("utf-8"),
            json.dumps([{"id": "sign", "expected": "weird", "status": "done"}]).encode("utf-8"),
        ]
        for raw in cases:
            updates_file.write_bytes(raw)
            failed = subprocess.run(
                self.command("1.2.0", str(updates_file), str(self.checklist), "--dry-run"),
                capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, raw)
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
            self.assertEqual(self.checklist.read_bytes(), before)
        updates_file.write_text("[]", encoding="utf-8")
        for arguments in (
            self.command("v1", str(updates_file), str(self.checklist), "--dry-run"),
            self.command("9.9.9", str(updates_file), str(self.checklist), "--dry-run"),
            self.command("1.2.0", str(updates_file),
                         str(Path(self.temp.name) / "missing" / "checklist.json"), "--dry-run"),
            self.command("1.2.0", str(Path(self.temp.name) / "nope.json"),
                         str(self.checklist), "--dry-run"),
            self.command("1.2.0", str(self.checklist), str(self.checklist), "--dry-run"),
        ):
            failed = subprocess.run(arguments, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, arguments)
            self.assertIn("error", json.loads(failed.stdout))
        self.assertEqual(self.checklist.read_bytes(), before)


class ReleaseRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.0.0", [{"category": "Fixed", "text": "Older fix"}])
        self.desk.add("1.2.0", [
            {"category": "Fixed", "text": " Retry exports "},
            {"category": "Fixed", "text": "Retry exports"},
            {"category": "Added", "text": "Export receipts"},
        ])

    def template(self, **overrides):
        data = {"items": [
            {"id": "docs", "text": " Write notes ", "required": True},
            {"id": "fixed", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
            {"id": "added", "text": "Announce feature", "required": False, "categories": ["Added"]},
        ]}
        data.update(overrides)
        return data

    def checklist(self, **overrides):
        data = {"version": "1.2.0", "items": [
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "fixed", "text": "Verify fix", "required": True, "status": "done"},
        ]}
        data.update(overrides)
        return data

    def rollback(self, target="1.0.0", steps=(" Stop service ", "Redeploy", "Redeploy")):
        value = list(steps) if isinstance(steps, (tuple, list)) else steps
        return {"targetVersion": target, "steps": value}

    def test_record_shape_and_field_content(self):
        record = self.desk.release_record("1.2.0", self.checklist(), self.template(), self.rollback())
        self.assertEqual(set(record), {"version", "changes", "notes", "audit", "rollback"})
        self.assertEqual(record["version"], "1.2.0")
        self.assertEqual(record["changes"], [
            {"category": "Fixed", "text": "Retry exports"},
            {"category": "Fixed", "text": "Retry exports"},
            {"category": "Added", "text": "Export receipts"},
        ])
        for change in record["changes"]:
            self.assertEqual(set(change), {"category", "text"})
        self.assertEqual(record["notes"], self.desk.notes("1.2.0"))
        self.assertEqual(set(record["audit"]),
                         {"version", "ready", "done", "pending", "blocked",
                          "missing", "mismatched", "unexpected"})
        self.assertTrue(record["audit"]["ready"])
        self.assertEqual([item["id"] for item in record["audit"]["done"]], ["docs", "fixed"])
        self.assertEqual([item["id"] for item in record["audit"]["missing"]], ["added"])
        self.assertEqual(record["rollback"], {
            "targetVersion": "1.0.0",
            "steps": ["Stop service", "Redeploy", "Redeploy"]})
        self.assertEqual(set(record["rollback"]), {"targetVersion", "steps"})

    def test_null_target_version(self):
        record = self.desk.release_record("1.2.0", self.checklist(), self.template(),
                                          self.rollback(target=None))
        self.assertIsNone(record["rollback"]["targetVersion"])
        self.assertEqual(record["rollback"]["steps"], ["Stop service", "Redeploy", "Redeploy"])

    def test_not_ready_audit_raises_without_optional_blocking(self):
        pending = self.checklist()
        pending["items"][1]["status"] = "pending"
        with self.assertRaises(ValueError):
            self.desk.release_record("1.2.0", pending, self.template(), self.rollback())
        # An optional template mismatch/missing never blocks the record.
        template = {"items": [
            {"id": "docs", "text": "Write notes", "required": True},
            {"id": "opt", "text": "Optional thing", "required": False},
        ]}
        checklist = {"version": "1.2.0", "items": [
            {"id": "docs", "text": "Write notes", "required": True, "status": "done"},
            {"id": "opt", "text": "Different", "required": True, "status": "done"},
        ]}
        record = self.desk.release_record("1.2.0", checklist, template, self.rollback())
        self.assertTrue(record["audit"]["ready"])
        self.assertEqual(len(record["audit"]["mismatched"]), 1)

    def test_rollback_target_version_validation(self):
        for target in ("1.2.0", "2.0.0", "9.9.9", "v1", "1.0", "01.0.0", 1, True, False, {}):
            with self.assertRaises(ValueError):
                self.desk.release_record("1.2.0", self.checklist(), self.template(),
                                         self.rollback(target=target))

    def test_rollback_structure_and_steps_validation(self):
        good = self.rollback()
        invalid_rollbacks = [
            None, [], "x", 1,
            {"steps": good["steps"]},
            {"targetVersion": None, "steps": good["steps"], "extra": 1},
            {"targetVersion": None},
            self.rollback(steps=[]),
            self.rollback(steps=[" "]),
            self.rollback(steps=[""]),
            self.rollback(steps=["a\nb"]),
            self.rollback(steps=["a\rb"]),
            self.rollback(steps=["a", 1]),
            self.rollback(steps="a"),
            self.rollback(steps={"a": 1}),
            self.rollback(steps=None),
        ]
        for rollback in invalid_rollbacks:
            with self.assertRaises(ValueError):
                self.desk.release_record("1.2.0", self.checklist(), self.template(), rollback)

    def test_invalid_version_checklist_template_propagate(self):
        for version in (None, 1, "v1", "1.0", "1.0.0.0", "01.0.0"):
            with self.assertRaises(ValueError):
                self.desk.release_record(version, self.checklist(), self.template(), self.rollback())
        with self.assertRaises(ValueError):
            self.desk.release_record("9.9.9", self.checklist(), self.template(), self.rollback())
        with self.assertRaises(ValueError):
            self.desk.release_record("1.2.0", {"version": "1.2.0", "items": []},
                                     self.template(), self.rollback())
        with self.assertRaises(ValueError):
            self.desk.release_record("1.2.0", self.checklist(version="1.0.0"),
                                     self.template(), self.rollback())
        with self.assertRaises(ValueError):
            self.desk.release_record("1.2.0", self.checklist(), {"items": []}, self.rollback())

    def test_missing_store_is_empty_and_unknown(self):
        missing = Path(self.temp.name) / "no-dir" / "releases.json"
        desk = ReleaseDesk(missing)
        with self.assertRaises(ValueError):
            desk.release_record("1.2.0", self.checklist(), self.template(), self.rollback())
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_deterministic_detached_and_readonly(self):
        before, mtime = self.path.read_bytes(), self.path.stat().st_mtime_ns
        checklist, template, rollback = self.checklist(), self.template(), self.rollback()
        snapshot = json.loads(json.dumps({"checklist": checklist, "template": template,
                                          "rollback": rollback}))
        first = self.desk.release_record("1.2.0", checklist, template, rollback)
        second = self.desk.release_record("1.2.0", checklist, template, rollback)
        self.assertEqual(first, second)
        self.assertNotIn("time", json.dumps(first))
        # Mutating nested returned data never reaches inputs or later records.
        first["changes"].append({"category": "Fixed", "text": "HACK"})
        first["audit"]["done"] = []
        first["rollback"]["steps"].append("HACK")
        first["notes"] = "changed"
        self.assertEqual({"checklist": checklist, "template": template, "rollback": rollback},
                         snapshot)
        third = self.desk.release_record("1.2.0", checklist, template, rollback)
        self.assertEqual(len(third["changes"]), 3)
        self.assertEqual(len(third["audit"]["done"]), 2)
        self.assertEqual(len(third["rollback"]["steps"]), 3)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_cli_record_release(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        rollback = Path(self.temp.name) / "rollback.json"
        checklist.write_text(json.dumps(self.checklist()), encoding="utf-8")
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        rollback.write_text(json.dumps(self.rollback()), encoding="utf-8")
        result = subprocess.run(
            prefix + ["record-release", "1.2.0", str(checklist), str(template), str(rollback)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        record = json.loads(result.stdout)
        self.assertEqual(set(record), {"version", "changes", "notes", "audit", "rollback"})
        self.assertEqual(record["version"], "1.2.0")
        self.assertEqual(record["rollback"]["steps"], ["Stop service", "Redeploy", "Redeploy"])
        # Inputs and store are untouched.
        self.assertEqual(json.loads(rollback.read_text(encoding="utf-8")), self.rollback())

    def test_cli_not_ready_and_bad_version_fail(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        rollback = Path(self.temp.name) / "rollback.json"
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        rollback.write_text(json.dumps(self.rollback()), encoding="utf-8")
        not_ready = self.checklist()
        not_ready["items"][0]["status"] = "blocked"
        checklist.write_text(json.dumps(not_ready), encoding="utf-8")
        result = subprocess.run(
            prefix + ["record-release", "1.2.0", str(checklist), str(template), str(rollback)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(set(json.loads(result.stdout)), {"error"})
        for version in ("v1", "9.9.9"):
            checklist.write_text(json.dumps(self.checklist()), encoding="utf-8")
            failed = subprocess.run(
                prefix + ["record-release", version, str(checklist), str(template), str(rollback)],
                capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, version)
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})

    def test_cli_file_encoding_syntax_and_duplicate_keys(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        rollback = Path(self.temp.name) / "rollback.json"
        good_checklist, good_template = json.dumps(self.checklist()), json.dumps(self.template())
        good_rollback = json.dumps(self.rollback())
        bad_files = {
            str(checklist): [b"\xff\xfe", b"{not json",
                             b'{"version": "1.2.0", "version": "1.2.0", "items": []}'],
            str(template): [b"\xff\xfe", b"{not json",
                            b'{"items": [{"id": "a", "id": "a", "text": "A", "required": true}]}'],
            str(rollback): [b"\xff\xfe", b"{not json",
                            b'{"targetVersion": null, "targetVersion": null, "steps": ["a"]}',
                            b'{"targetVersion": null, "steps": [{"a": 1, "a": 2}]}'],
        }
        good = {str(checklist): good_checklist, str(template): good_template,
                str(rollback): good_rollback}
        for target, raws in bad_files.items():
            for name, raw in good.items():
                Path(name).write_text(raw, encoding="utf-8")
            for raw in raws:
                Path(target).write_bytes(raw)
                failed = subprocess.run(
                    prefix + ["record-release", "1.2.0", str(checklist), str(template),
                              str(rollback)],
                    capture_output=True, text=True)
                self.assertEqual(failed.returncode, 2, raw)
                self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        missing = subprocess.run(
            prefix + ["record-release", "1.2.0",
                      str(Path(self.temp.name) / "nope.json"), str(template), str(rollback)],
            capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stdout))

    def test_cli_output_writes_newline_json(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        rollback = Path(self.temp.name) / "rollback.json"
        for path, payload in ((checklist, self.checklist()), (template, self.template()),
                              (rollback, self.rollback())):
            path.write_text(json.dumps(payload), encoding="utf-8")
        target = Path(self.temp.name) / "record.json"
        result = subprocess.run(
            prefix + ["record-release", "1.2.0", str(checklist), str(template), str(rollback),
                      "--output", str(target)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        console = json.loads(result.stdout)
        raw = target.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(json.loads(raw.decode("utf-8")), console)

    def test_cli_output_existing_symlink_alias_hardlink_rejected(self):
        script = str(ROOT / "release_desk.py")
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        rollback = Path(self.temp.name) / "rollback.json"
        for path, payload in ((checklist, self.checklist()), (template, self.template()),
                              (rollback, self.rollback())):
            path.write_text(json.dumps(payload), encoding="utf-8")
        arguments = [str(checklist), str(template), str(rollback)]
        # Existing regular file keeps its bytes.
        existing = Path(self.temp.name) / "existing.json"
        existing.write_text("PREVIOUS", encoding="utf-8")
        failed = subprocess.run(
            [sys.executable, script, "--store", str(self.path), "record-release", "1.2.0"]
            + arguments + ["--output", str(existing)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(existing.read_text(encoding="utf-8"), "PREVIOUS")
        # Symlink to the store and a dangling symlink.
        link = self.path.parent / "link.json"
        link.symlink_to(self.path)
        failed = subprocess.run(
            [sys.executable, script, "--store", str(self.path), "record-release", "1.2.0"]
            + arguments + ["--output", str(link)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        dangling = self.path.parent / "dangling.json"
        dangling.symlink_to("nothing")
        failed = subprocess.run(
            [sys.executable, script, "--store", str(self.path), "record-release", "1.2.0"]
            + arguments + ["--output", str(dangling)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        # A hard link to an input and a textual alias of the store.
        hard = self.path.parent / "hard.json"
        os.link(rollback, hard)
        failed = subprocess.run(
            [sys.executable, script, "--store", str(self.path), "record-release", "1.2.0"]
            + arguments + ["--output", str(hard)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        alias = str(self.path.parent) + "/./" + self.path.name
        failed = subprocess.run(
            [sys.executable, script, "--store", str(self.path), "record-release", "1.2.0"]
            + arguments + ["--output", alias], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)

    def test_cli_missing_output_parent_is_oserror_and_creates_nothing(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        checklist = Path(self.temp.name) / "checklist.json"
        template = Path(self.temp.name) / "template.json"
        rollback = Path(self.temp.name) / "rollback.json"
        for path, payload in ((checklist, self.checklist()), (template, self.template()),
                              (rollback, self.rollback())):
            path.write_text(json.dumps(payload), encoding="utf-8")
        target = Path(self.temp.name) / "missing-dir" / "record.json"
        failed = subprocess.run(
            prefix + ["record-release", "1.2.0", str(checklist), str(template), str(rollback),
                      "--output", str(target)],
            capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("error", json.loads(failed.stdout))
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())


class PreviewMergeChecklistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.2.0", [{"category": "Added", "text": "One"},
                                {"category": "Fixed", "text": "Two"}])

    @staticmethod
    def payload(items, version="1.2.0"):
        return {"version": version, "items": items}

    def base_payload(self):
        return self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending"},
            {"id": "b", "text": "Beta", "required": True, "status": "pending"},
            {"id": "c", "text": "Gamma", "required": False, "status": "done"},
            {"id": "d", "text": "Delta", "required": False, "status": "pending"},
        ])

    def test_clean_merge_combines_both_sides(self):
        incoming = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending"},
            {"id": "b", "text": "Beta", "required": True, "status": "done"},
            {"id": "d", "text": "Delta", "required": False, "status": "pending"},
            {"id": "e", "text": "Epsilon", "required": False, "status": "pending"},
        ])
        current = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "done"},
            {"id": "b", "text": "Beta", "required": True, "status": "pending"},
            {"id": "c", "text": "Gamma", "required": False, "status": "done"},
            {"id": "d", "text": "Delta", "required": False, "status": "pending"},
        ])
        result = self.desk.preview_merge_checklist(
            "1.2.0", self.base_payload(), incoming, current)
        self.assertEqual(set(result), {"version", "canMerge", "ready", "items", "conflicts"})
        self.assertEqual(result["version"], "1.2.0")
        self.assertTrue(result["canMerge"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["conflicts"], [])
        # Current-side order first, then incoming-only items in incoming order.
        self.assertEqual(result["items"], [
            {"id": "a", "text": "Alpha", "required": True, "status": "done"},
            {"id": "b", "text": "Beta", "required": True, "status": "done"},
            {"id": "d", "text": "Delta", "required": False, "status": "pending"},
            {"id": "e", "text": "Epsilon", "required": False, "status": "pending"}])
        for item in result["items"]:
            self.assertEqual(set(item), {"id", "text", "required", "status"})

    def test_independent_current_edits_and_deletions_survive(self):
        # Incoming matches the base everywhere: every current edit is kept.
        current = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "done"},
            {"id": "own", "text": "Own", "required": False, "status": "blocked"},
        ])
        base = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending"},
            {"id": "gone", "text": "Gone", "required": False, "status": "pending"},
        ])
        result = self.desk.preview_merge_checklist("1.2.0", base, base, current)
        self.assertTrue(result["canMerge"])
        self.assertEqual(result["items"], [
            {"id": "a", "text": "Alpha", "required": True, "status": "done"},
            {"id": "own", "text": "Own", "required": False, "status": "blocked"}])
        # Current matching the incoming side is kept even when it differs from base.
        again = self.desk.preview_merge_checklist("1.2.0", base, current, current)
        self.assertEqual(again["items"], result["items"])

    def test_conflict_keeps_current_and_reports_whole_items(self):
        base = self.payload([
            {"id": "x", "text": "Xray", "required": True, "status": "pending"},
            {"id": "y", "text": "Yank", "required": False, "status": "pending"},
        ])
        incoming = self.payload([
            {"id": "x", "text": "Xray", "required": True, "status": "done"},
        ])
        current = self.payload([
            {"id": "x", "text": "Xray", "required": True, "status": "blocked"},
            {"id": "y", "text": "Yank", "required": False, "status": "done"},
        ])
        result = self.desk.preview_merge_checklist("1.2.0", base, incoming, current)
        self.assertFalse(result["canMerge"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["items"], [
            {"id": "x", "text": "Xray", "required": True, "status": "blocked"},
            {"id": "y", "text": "Yank", "required": False, "status": "done"}])
        self.assertEqual(result["conflicts"], [
            {"id": "x",
             "base": {"id": "x", "text": "Xray", "required": True, "status": "pending"},
             "incoming": {"id": "x", "text": "Xray", "required": True, "status": "done"},
             "current": {"id": "x", "text": "Xray", "required": True, "status": "blocked"}},
            {"id": "y",
             "base": {"id": "y", "text": "Yank", "required": False, "status": "pending"},
             "incoming": None,
             "current": {"id": "y", "text": "Yank", "required": False, "status": "done"}}])

    def test_conflicts_sorted_by_id_code_point(self):
        base = self.payload([
            {"id": "b", "text": "Bee", "required": True, "status": "pending"},
            {"id": "A", "text": "Ay", "required": False, "status": "pending"},
        ])
        incoming = self.payload([
            {"id": "b", "text": "Bee", "required": True, "status": "done"},
            {"id": "A", "text": "Ay", "required": False, "status": "done"},
        ])
        current = self.payload([
            {"id": "b", "text": "Bee", "required": True, "status": "blocked"},
            {"id": "A", "text": "Ay", "required": False, "status": "blocked"},
        ])
        result = self.desk.preview_merge_checklist("1.2.0", base, incoming, current)
        self.assertEqual([entry["id"] for entry in result["conflicts"]], ["A", "b"])

    def test_extra_fields_ignored_in_comparison(self):
        base = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending", "note": 1},
        ])
        incoming = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending", "note": 2},
        ])
        current = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "done"},
        ])
        result = self.desk.preview_merge_checklist("1.2.0", base, incoming, current)
        self.assertTrue(result["canMerge"])
        self.assertEqual(result["items"], [
            {"id": "a", "text": "Alpha", "required": True, "status": "done"}])

    def test_result_validation_empty_or_no_required(self):
        # Every current item is adopted away or lost to a conflict and no
        # incoming item enters: the preview would be empty.
        base = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending"},
            {"id": "b", "text": "Beta", "required": True, "status": "pending"},
        ])
        incoming = self.payload([
            {"id": "b", "text": "Beta", "required": True, "status": "done"},
        ])
        current = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending"},
        ])
        with self.assertRaises(ValueError):
            self.desk.preview_merge_checklist("1.2.0", base, incoming, current)
        # Only optional items survive a clean merge: no required item remains.
        steady_incoming = self.payload([
            {"id": "b", "text": "Beta", "required": True, "status": "pending"},
        ])
        demoted_current = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending"},
            {"id": "b", "text": "Beta", "required": False, "status": "pending"},
        ])
        with self.assertRaises(ValueError):
            self.desk.preview_merge_checklist("1.2.0", base, steady_incoming, demoted_current)
        # The same rule applies when the preview also reports conflicts.
        incoming_with_add = self.payload([
            {"id": "b", "text": "Beta", "required": True, "status": "done"},
            {"id": "o", "text": "Opt", "required": False, "status": "pending"},
        ])
        with self.assertRaises(ValueError):
            self.desk.preview_merge_checklist("1.2.0", base, incoming_with_add, current)

    def test_validation_errors(self):
        valid = self.base_payload()
        for version in (None, "v1", "1.0", "9.9.9"):
            with self.assertRaises(ValueError):
                self.desk.preview_merge_checklist(version, valid, valid, valid)
        # Declared versions must match the queried version in every input.
        mismatched = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "pending"},
        ], version="1.0.0")
        for position in range(3):
            inputs = [valid, valid, valid]
            inputs[position] = mismatched
            with self.assertRaises(ValueError):
                self.desk.preview_merge_checklist("1.2.0", *inputs)
        # Every input must satisfy the checklist rules on its own.
        invalid = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "weird"},
        ])
        for position in range(3):
            inputs = [valid, valid, valid]
            inputs[position] = invalid
            with self.assertRaises(ValueError):
                self.desk.preview_merge_checklist("1.2.0", *inputs)
        # An invalid store is rejected, and a missing store reports unknown.
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.desk.preview_merge_checklist("1.2.0", valid, valid, valid)
        desk = ReleaseDesk(Path(self.temp.name) / "missing" / "releases.json")
        with self.assertRaises(ValueError):
            desk.preview_merge_checklist("1.2.0", valid, valid, valid)

    def test_inputs_untouched_and_result_detached(self):
        base = self.base_payload()
        incoming = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "done"},
            {"id": "b", "text": "Beta", "required": True, "status": "pending"},
            {"id": "c", "text": "Gamma", "required": False, "status": "done"},
            {"id": "d", "text": "Delta", "required": False, "status": "pending"},
        ])
        current = self.payload([
            {"id": "a", "text": "Alpha", "required": True, "status": "blocked"},
            {"id": "b", "text": "Beta", "required": True, "status": "pending"},
            {"id": "c", "text": "Gamma", "required": False, "status": "done"},
            {"id": "d", "text": "Delta", "required": False, "status": "pending"},
        ])
        snapshot = json.loads(json.dumps([base, incoming, current]))
        store_before = self.path.read_bytes()
        result = self.desk.preview_merge_checklist("1.2.0", base, incoming, current)
        self.assertFalse(result["canMerge"])
        self.assertEqual([base, incoming, current], snapshot)
        result["items"][0]["status"] = "done"
        result["conflicts"][0]["current"]["status"] = "done"
        self.assertEqual([base, incoming, current], snapshot)
        self.assertEqual(self.path.read_bytes(), store_before)

    def command(self, *extra):
        return [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path),
                "merge-checklist", *extra]

    def write_files(self, base, incoming, current):
        paths = []
        for name, payload in (("base.json", base), ("incoming.json", incoming),
                              ("current.json", current)):
            path = Path(self.temp.name) / name
            path.write_text(json.dumps(payload), encoding="utf-8")
            paths.append(path)
        return paths

    def test_cli_merge_checklist_success_with_conflicts(self):
        base, incoming, current = self.write_files(
            self.payload([
                {"id": "x", "text": "Xray", "required": True, "status": "pending"},
            ]),
            self.payload([
                {"id": "x", "text": "Xray", "required": True, "status": "done"},
            ]),
            self.payload([
                {"id": "x", "text": "Xray", "required": True, "status": "blocked"},
            ]))
        before = [path.read_bytes() for path in (self.path, base, incoming, current)]
        result = subprocess.run(
            self.command("1.2.0", str(base), str(incoming), str(current)),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        report = json.loads(result.stdout)
        self.assertEqual(set(report), {"version", "canMerge", "ready", "items", "conflicts"})
        self.assertFalse(report["canMerge"])
        self.assertEqual(len(report["conflicts"]), 1)
        self.assertEqual([path.read_bytes() for path in (self.path, base, incoming, current)],
                         before)

    def test_cli_merge_checklist_clean_merge(self):
        base, incoming, current = self.write_files(
            self.base_payload(),
            self.payload([
                {"id": "a", "text": "Alpha", "required": True, "status": "pending"},
                {"id": "b", "text": "Beta", "required": True, "status": "done"},
                {"id": "c", "text": "Gamma", "required": False, "status": "done"},
                {"id": "d", "text": "Delta", "required": False, "status": "pending"},
            ]),
            self.base_payload())
        result = subprocess.run(
            self.command("1.2.0", str(base), str(incoming), str(current)),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["canMerge"])
        self.assertEqual(report["conflicts"], [])
        self.assertEqual([item["status"] for item in report["items"]],
                         ["pending", "done", "done", "pending"])

    def test_cli_merge_checklist_errors(self):
        base, incoming, current = self.write_files(
            self.base_payload(), self.base_payload(), self.base_payload())
        before = [path.read_bytes() for path in (self.path, base, incoming, current)]
        # Non-UTF-8 bytes, JSON syntax errors and duplicate keys at any level.
        for raw in (b"\xff\xfe", b"{not json",
                    b'{"version": "1.2.0", "items": [{"id": "a", "id": "b",'
                    b' "text": "T", "required": true, "status": "done"}]}'):
            incoming.write_bytes(raw)
            failed = subprocess.run(
                self.command("1.2.0", str(base), str(incoming), str(current)),
                capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, raw)
            self.assertEqual(set(json.loads(failed.stdout)), {"error"})
        incoming.write_text(json.dumps(self.base_payload()), encoding="utf-8")
        # Bad and unknown versions and a declared-version mismatch.
        mismatched = Path(self.temp.name) / "mismatched.json"
        mismatched.write_text(json.dumps(self.payload(
            [{"id": "a", "text": "Alpha", "required": True, "status": "done"}],
            version="1.0.0")), encoding="utf-8")
        for arguments in (self.command("v1", str(base), str(incoming), str(current)),
                          self.command("9.9.9", str(base), str(incoming), str(current)),
                          self.command("1.2.0", str(mismatched), str(incoming), str(current)),
                          self.command("1.2.0", str(base), str(mismatched), str(current)),
                          self.command("1.2.0", str(base), str(incoming), str(mismatched))):
            failed = subprocess.run(arguments, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, arguments)
            self.assertIn("error", json.loads(failed.stdout))
        # A missing input file fails without creating anything.
        missing = Path(self.temp.name) / "missing.json"
        failed = subprocess.run(
            self.command("1.2.0", str(missing), str(incoming), str(current)),
            capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertFalse(missing.exists())
        # Nothing was modified and no file or directory was created.
        self.assertEqual([path.read_bytes() for path in (self.path, base, incoming, current)],
                         before)
        self.assertEqual({path.name for path in Path(self.temp.name).iterdir()},
                         {"releases.json", "base.json", "incoming.json",
                          "current.json", "mismatched.json"})


if __name__ == "__main__":
    unittest.main()
