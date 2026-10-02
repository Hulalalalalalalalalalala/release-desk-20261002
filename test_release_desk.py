import json
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


if __name__ == "__main__":
    unittest.main()
