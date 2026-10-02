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


class DiffConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "releases.json"
        self.desk = ReleaseDesk(self.path)
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        self.desk.add("2.0.0", [{"category": "Fixed", "text": "Two"}])

    def write_config(self, name, content):
        path = Path(self.temp.name) / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        elif isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def test_added_removed_changed_and_nested_recursion(self):
        base = {
            "keep": 1,
            "drop": "gone",
            "nested": {"both": {"x": 1, "y": 2}, "only_base": [1, 2]},
            "alter": True,
        }
        target = {
            "keep": 1,
            "nested": {"both": {"x": 1, "y": 3}, "only_target": {"deep": None}},
            "alter": 1,
            "fresh": {"sub": "object"},
        }
        result = self.desk.diff_config("1.0.0", "2.0.0", base, target)
        self.assertEqual(result["baseVersion"], "1.0.0")
        self.assertEqual(result["targetVersion"], "2.0.0")
        # A sub-object present on only one side is a single entry.
        self.assertEqual(result["added"], [
            {"path": "/fresh", "value": {"sub": "object"}},
            {"path": "/nested/only_target", "value": {"deep": None}},
        ])
        self.assertEqual(result["removed"], [
            {"path": "/drop", "value": "gone"},
            {"path": "/nested/only_base", "value": [1, 2]},
        ])
        # A boolean never equals a number.
        self.assertEqual(result["changed"], [
            {"path": "/alter", "before": True, "after": 1},
            {"path": "/nested/both/y", "before": 2, "after": 3},
        ])

    def test_identical_configs_and_numeric_equivalence(self):
        config = {"a": 1, "b": [1.0, {"c": None}], "d": {"e": "x"}}
        other = {"d": {"e": "x"}, "b": [1, {"c": None}], "a": 1.0}
        result = self.desk.diff_config("1.0.0", "1.0.0", config, other)
        self.assertEqual(result, {"baseVersion": "1.0.0", "targetVersion": "1.0.0",
                                  "added": [], "removed": [], "changed": []})

    def test_arrays_are_compared_wholesale_and_null_differs_from_missing(self):
        base = {"list": [1, 2], "present": None, "typed": 1}
        target = {"list": [2, 1], "typed": "1"}
        result = self.desk.diff_config("1.0.0", "2.0.0", base, target)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [{"path": "/present", "value": None}])
        self.assertEqual(result["changed"], [
            {"path": "/list", "before": [1, 2], "after": [2, 1]},
            {"path": "/typed", "before": 1, "after": "1"},
        ])

    def test_path_escaping_empty_keys_and_code_point_order(self):
        base = {"a/b": 1, "t~ilde": 1, "": 1, "Z": 1, "z": 1}
        target = {"a/b": 2, "t~ilde": 2, "": 2, "Z": 2, "z": 2}
        result = self.desk.diff_config("2.0.0", "1.0.0", base, target)
        self.assertEqual([entry["path"] for entry in result["changed"]],
                         ["/", "/Z", "/a~1b", "/t~0ilde", "/z"])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])

    def test_same_version_reverse_and_no_mutation(self):
        base = {"x": {"y": [1]}, "gone": 1}
        target = {"x": {"y": [1], "z": 2}}
        base_snapshot = json.loads(json.dumps(base))
        target_snapshot = json.loads(json.dumps(target))
        forward = self.desk.diff_config("1.0.0", "1.0.0", base, target)
        self.assertEqual(forward["added"], [{"path": "/x/z", "value": 2}])
        self.assertEqual(forward["removed"], [{"path": "/gone", "value": 1}])
        reverse = self.desk.diff_config("1.0.0", "1.0.0", target, base)
        self.assertEqual(reverse["added"], [{"path": "/gone", "value": 1}])
        self.assertEqual(reverse["removed"], [{"path": "/x/z", "value": 2}])
        # Inputs are not mutated and results share no containers with them.
        forward["added"][0]["value"] = 99
        forward["removed"][0]["value"] = 99
        self.assertEqual(base, base_snapshot)
        self.assertEqual(target, target_snapshot)

    def test_config_validation_errors(self):
        for bad in ([], "text", None, 3):
            with self.assertRaises(ValueError):
                self.desk.diff_config("1.0.0", "2.0.0", bad, {})
        for bad in ({1: "x"}, {"a": (1, 2)}, {"a": {"b", "c"}},
                    {"a": float("inf")}, {"a": [float("nan")]}):
            with self.assertRaises(ValueError):
                self.desk.diff_config("1.0.0", "2.0.0", bad, {})
        cyclic = {}
        cyclic["self"] = cyclic
        with self.assertRaises(ValueError):
            self.desk.diff_config("1.0.0", "2.0.0", cyclic, {})
        with self.assertRaises(ValueError):
            self.desk.diff_config("1.0.0", "2.0.0", {}, {"ok": 1, "bad": object()})

    def test_version_and_store_validation_errors(self):
        for base, target in (("v1", "1.0.0"), ("1.0.0", "9.9.9"), (None, "1.0.0")):
            with self.assertRaises(ValueError):
                self.desk.diff_config(base, target, {}, {})
        self.path.write_text(json.dumps({"1.0.0": [], "2.0.0": [{"category": "Added", "text": "Two"}]}),
                             encoding="utf-8")
        with self.assertRaises(ValueError):
            self.desk.diff_config("1.0.0", "2.0.0", {}, {})
        # A missing store is treated as empty and reports the version as unknown.
        missing = ReleaseDesk(Path(self.temp.name) / "missing-dir" / "releases.json")
        with self.assertRaises(ValueError):
            missing.diff_config("1.0.0", "2.0.0", {}, {})
        self.assertFalse(missing.path.exists())
        self.assertFalse(missing.path.parent.exists())

    def test_cli_diff_config(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        base_file = self.write_config("base.json", {"a": 1, "b": {"c": "x"}})
        target_file = self.write_config("target.json", {"a": 2, "b": {"c": "x", "d": [True]}})
        before = self.path.read_bytes()
        result = subprocess.run(prefix + ["diff-config", "1.0.0", "2.0.0", str(base_file), str(target_file)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {"baseVersion": "1.0.0", "targetVersion": "2.0.0",
                                   "added": [{"path": "/b/d", "value": [True]}],
                                   "removed": [],
                                   "changed": [{"path": "/a", "before": 1, "after": 2}]})
        # Neither the store nor the config files are modified.
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(json.loads(base_file.read_text(encoding="utf-8")), {"a": 1, "b": {"c": "x"}})
        self.assertEqual(json.loads(target_file.read_text(encoding="utf-8")),
                         {"a": 2, "b": {"c": "x", "d": [True]}})

    def test_cli_diff_config_errors(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        before = self.path.read_bytes()
        good = self.write_config("good.json", {"a": 1})
        cases = [
            "{not json",
            b"\xff\xfe",
            '{"a": 1, "a": 2}',
            "[1, 2]",
            '{"deep": {"x": 1, "x": 2}}',
            '{"n": NaN}',
        ]
        for case in cases:
            bad = self.write_config("bad.json", case)
            result = subprocess.run(prefix + ["diff-config", "1.0.0", "2.0.0", str(bad), str(good)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, case)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        for base, target in (("v1", "1.0.0"), ("1.0.0", "9.9.9")):
            result = subprocess.run(prefix + ["diff-config", base, target, str(good), str(good)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(set(json.loads(result.stdout)), {"error"})
        absent = subprocess.run(
            prefix + ["diff-config", "1.0.0", "2.0.0", str(Path(self.temp.name) / "nope.json"), str(good)],
            capture_output=True, text=True)
        self.assertEqual(absent.returncode, 2)
        self.assertIn("error", json.loads(absent.stdout))
        self.assertEqual(self.path.read_bytes(), before)
        # A missing store is treated as empty, fails as unknown, and is not created.
        store = Path(self.temp.name) / "missing-dir" / "releases.json"
        result = subprocess.run([sys.executable, str(ROOT / "release_desk.py"), "--store", str(store),
                                 "diff-config", "1.0.0", "2.0.0", str(good), str(good)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())


if __name__ == "__main__":
    unittest.main()
