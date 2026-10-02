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


class MakeChecklistTests(unittest.TestCase):
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
                {"id": "fix", "text": "Verify fix", "required": True, "categories": ["Fixed"]},
                {"id": "feat", "text": "Announce feature", "required": False, "categories": ["Added"]},
            ],
        }
        data.update(overrides)
        return data

    def test_filters_by_release_categories_and_keeps_order(self):
        result = self.desk.generate_checklist("1.2.0", self.template())
        self.assertEqual(set(result), {"version", "items"})
        self.assertEqual(result, {"version": "1.2.0", "items": [
            {"id": "docs", "text": "Write notes", "required": True, "status": "pending"},
            {"id": "fix", "text": "Verify fix", "required": True, "status": "pending"},
        ]})

    def test_multi_category_item_and_single_occurrence(self):
        self.desk.add("2.0.0", [
            {"category": "Added", "text": "One"},
            {"category": "Added", "text": "Two"},
            {"category": "Fixed", "text": "Three"},
        ])
        template = {"items": [
            {"id": "both", "text": "Both", "required": True, "categories": ["Added", "Fixed"]},
        ]}
        result = self.desk.generate_checklist("2.0.0", template)
        self.assertEqual([item["id"] for item in result["items"]], ["both"])

    def test_template_status_and_extra_fields_ignored(self):
        template = {"extra": 1, "items": [
            {"id": "a", "text": "A", "required": True, "status": "done", "other": [1]},
        ]}
        result = self.desk.generate_checklist("1.2.0", template)
        self.assertEqual(result["items"], [
            {"id": "a", "text": "A", "required": True, "status": "pending"}])

    def test_result_feeds_existing_checklist(self):
        result = self.desk.generate_checklist("1.2.0", self.template())
        report = self.desk.checklist("1.2.0", result)
        self.assertEqual(set(report), {"version", "ready", "done", "pending", "blocked"})
        self.assertFalse(report["ready"])
        self.assertEqual([item["id"] for item in report["pending"]], ["docs", "fix"])

    def test_invalid_items_rejected_even_when_not_matching(self):
        # The Added-only item would be filtered out, but is still validated.
        bad = {"id": "feat", "text": "x\ny", "required": True, "categories": ["Added"]}
        template = self.template()
        template["items"][2] = bad
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", template)

    def test_invalid_template_structures(self):
        good = {"id": "a", "text": "A", "required": True}
        invalid = [
            None, [], "x", 1, {},
            {"items": []}, {"items": {}}, {"items": None},
            {"items": [["x"]]}, {"items": ["x"]}, {"items": [None]},
            {"items": [{**good, "id": "  "}]},
            {"items": [{**good, "id": "a\nb"}]},
            {"items": [{**good, "id": 1}]},
            {"items": [{**good, "text": ""}]},
            {"items": [{**good, "text": "a\rb"}]},
            {"items": [{**good, "required": 1}]},
            {"items": [{**good, "required": "yes"}]},
            {"items": [{**good, "categories": []}]},
            {"items": [{**good, "categories": "Added"}]},
            {"items": [{**good, "categories": ["added"]}]},
            {"items": [{**good, "categories": ["Other"]}]},
            {"items": [{**good, "categories": [1]}]},
            {"items": [{**good, "categories": ["Added", "Added"]}]},
            {"items": [{**good, "categories": ["Fixed", "Added", "Fixed"]}]},
            {"items": [good, {**good, "id": " a "}]},
            {"items": [good, {**good, "id": "A", "text": "B"}, {**good}]},
        ]
        for template in invalid:
            with self.assertRaises(ValueError, msg=repr(template)):
                self.desk.generate_checklist("1.2.0", template)

    def test_categories_may_omit_or_cover_all(self):
        template = {"items": [
            {"id": "any", "text": "Any", "required": True},
            {"id": "all", "text": "All", "required": False,
             "categories": ["Added", "Changed", "Fixed"]},
        ]}
        result = self.desk.generate_checklist("1.2.0", template)
        self.assertEqual([item["id"] for item in result["items"]], ["any", "all"])

    def test_ids_case_sensitive_without_unicode_normalization(self):
        template = {"items": [
            {"id": "Same", "text": "One", "required": True},
            {"id": "same", "text": "Two", "required": False},
            {"id": "caf" + chr(0x00E9), "text": "Composed", "required": False},
            {"id": "caf" + "e" + chr(0x0301), "text": "Decomposed", "required": False},
        ]}
        result = self.desk.generate_checklist("1.2.0", template)
        self.assertEqual(len(result["items"]), 4)

    def test_empty_after_filter_and_no_required_rejected(self):
        only_added = {"items": [
            {"id": "feat", "text": "Feature", "required": True, "categories": ["Added"]}]}
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", only_added)
        no_required = {"items": [
            {"id": "opt", "text": "Optional", "required": False},
            {"id": "fix", "text": "Fix", "required": False, "categories": ["Fixed"]}]}
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", no_required)
        # A required item that is filtered out does not count.
        required_filtered = {"items": [
            {"id": "opt", "text": "Optional", "required": False},
            {"id": "feat", "text": "Feature", "required": True, "categories": ["Added"]}]}
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("1.2.0", required_filtered)

    def test_invalid_and_unknown_version(self):
        for version in (None, 1, "v1", "1.0", "1.0.0.0", "01.0.0"):
            with self.assertRaises(ValueError):
                self.desk.generate_checklist(version, self.template())
        with self.assertRaises(ValueError):
            self.desk.generate_checklist("9.9.9", self.template())
        missing = ReleaseDesk(Path(self.temp.name) / "no-dir" / "releases.json")
        with self.assertRaises(ValueError):
            missing.generate_checklist("1.2.0", self.template())
        self.assertFalse((Path(self.temp.name) / "no-dir").exists())

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
        self.desk.generate_checklist("1.2.0", template)
        self.assertEqual(template, snapshot)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_cli_make_checklist(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        template = Path(self.temp.name) / "template.json"
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        result = subprocess.run(prefix + ["make-checklist", "1.2.0", str(template)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(json.loads(result.stdout), {"version": "1.2.0", "items": [
            {"id": "docs", "text": "Write notes", "required": True, "status": "pending"},
            {"id": "fix", "text": "Verify fix", "required": True, "status": "pending"},
        ]})
        # The generated checklist can be checked directly.
        generated = Path(self.temp.name) / "generated.json"
        generated.write_text(result.stdout, encoding="utf-8")
        checked = subprocess.run(prefix + ["check", "1.2.0", str(generated)],
                                 capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertFalse(json.loads(checked.stdout)["ready"])

    def test_cli_make_checklist_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        template = Path(self.temp.name) / "template.json"
        cases = [
            "{not json",
            b"\xff\xfe",
            "[]",
            '{"items": []}',
            '{"items": [{"id": "a", "text": "A", "required": True, "categories": []}]}',
            '{"items": [{"id": "a", "text": "A", "required": true, "required": true}]}',
            '{"items": [{"id": "a", "text": "A", "required": true}], "items": []}',
            json.dumps({"items": [
                {"id": "feat", "text": "Feature", "required": True, "categories": ["Added"]}]}),
        ]
        for case in cases:
            if isinstance(case, bytes):
                template.write_bytes(case)
            else:
                template.write_text(case, encoding="utf-8")
            result = subprocess.run(prefix + ["make-checklist", "1.2.0", str(template)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, case)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
            self.assertEqual(result.stdout.count("\n"), 1)
        unknown = subprocess.run(prefix + ["make-checklist", "9.9.9", str(template)],
                                 capture_output=True, text=True)
        self.assertEqual(unknown.returncode, 2)
        absent = subprocess.run(prefix + ["make-checklist", "1.2.0", str(Path(self.temp.name) / "nope.json")],
                                capture_output=True, text=True)
        self.assertEqual(absent.returncode, 2)
        self.assertIn("error", json.loads(absent.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is treated as empty, then fails as unknown release,
        # and neither the store nor its parent directories are created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        template.write_text(json.dumps(self.template()), encoding="utf-8")
        failed = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "make-checklist", "1.2.0", str(template)],
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


if __name__ == "__main__":
    unittest.main()
