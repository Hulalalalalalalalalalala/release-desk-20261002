import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from release_desk import ReleaseDesk, _loads_json

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


    def test_store_rejects_duplicate_keys_everywhere(self):
        change = '{"category":"Added","text":"One"}'
        cases = {
            "duplicate version keys, even with identical values":
                ('{"1.0.0":[' + change + '],"1.0.0":[' + change + ']}').encode(),
            "duplicate keys inside a change object":
                b'{"1.0.0":[{"category":"Added","text":"One","text":"Two"}]}',
            "duplicate keys in an ignored extra field":
                b'{"9.9.9":[{"category":"Added","text":"One","note":"a","note":"b"}]}',
        }
        for label, raw in cases.items():
            with self.subTest(label):
                self.path.write_bytes(raw)
                for action in (
                    lambda: self.desk.releases(),
                    lambda: self.desk.versions(),
                    lambda: self.desk.notes("1.0.0"),
                    lambda: self.desk.diff("1.0.0", "1.0.0"),
                    lambda: self.desk.add("2.0.0", [{"category": "Added", "text": "x"}]),
                    lambda: self.desk.import_releases({"2.0.0": [{"category": "Added", "text": "x"}]}),
                ):
                    with self.assertRaisesRegex(ValueError, "^duplicate JSON object key$"):
                        action()
                self.assertEqual(self.path.read_bytes(), raw)

    def test_duplicate_key_matching_is_exact_and_scoped(self):
        valid = [
            b'{"Text":"x","text":"y","category":"Added"}',
            b'{"1.0.0":[{"category":"Added","text":"x","meta":{"text":"nested"}}]}',
            b'[{"category":"Added","text":"the \\"text\\" field \\\\ looks like \\"category\\""}]',
        ]
        invalid = [
            b'{"1.0.0":[{"category":"Added","text":"x","te\\u0078t":"y"}]}',
            b'[{"category":"Added","category":"Fixed","text":"x"}]',
        ]
        for raw in valid:
            self.assertTrue(_loads_json(raw.decode("utf-8")) is not None)
        for raw in invalid:
            with self.assertRaisesRegex(ValueError, "^duplicate JSON object key$"):
                _loads_json(raw.decode("utf-8"))

    def test_identical_duplicate_records_still_legal(self):
        changes = [
            {"category": "Fixed", "text": "Retry"},
            {"category": "Fixed", "text": "Retry"},
        ]
        result = self.desk.add("1.0.0", changes)
        self.assertEqual(result["changes"], 2)
        self.assertEqual(len(self.desk.releases()["1.0.0"]), 2)

    def test_in_memory_objects_keep_existing_rules(self):
        self.assertEqual(self.desk.add("1.0.0", [{"category": "Added", "text": " x"}])["changes"], 1)
        self.assertEqual(self.desk.import_releases({"1.0.0": [{"category": "Added", "text": "x"}]})["skipped"], ["1.0.0"])

    def test_cli_add_rejects_duplicate_keys_without_creating_store(self):
        nested = Path(self.temp.name) / "state" / "releases.json"
        candidate = Path(self.temp.name) / "changes.json"
        candidate.write_text(
            '[{"category":"Added","text":"One","text":"Two"}]', encoding="utf-8")
        failed = subprocess.run(
            [sys.executable, str(ROOT / "release_desk.py"), "--store", str(nested),
             "add", "1.0.0", str(candidate)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, json.dumps({"error": "duplicate JSON object key"}) + "\n")
        self.assertEqual(failed.stderr, "")
        self.assertFalse(nested.exists())
        self.assertFalse(nested.parent.exists())

    def test_cli_add_rejects_duplicate_keys_via_unicode_escape(self):
        candidate = Path(self.temp.name) / "changes.json"
        candidate.write_text(
            '[{"category":"Added","text":"One","te\\u0078t":"Two"}]', encoding="utf-8")
        failed = subprocess.run(
            [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path),
             "add", "1.0.0", str(candidate)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout), {"error": "duplicate JSON object key"})
        self.assertFalse(self.path.exists())

    def test_cli_import_rejects_duplicate_batch_keys(self):
        self.desk.add("1.0.0", [{"category": "Added", "text": "One"}])
        before = self.path.read_bytes()
        batch = Path(self.temp.name) / "batch.json"
        batch.write_text(
            '{"2.0.0":[{"category":"Added","text":"New"}],'
            '"3.0.0":[{"category":"Fixed","text":"Patch","note":"a","note":"b"}]}',
            encoding="utf-8")
        failed = subprocess.run(
            [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path),
             "import", str(batch)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout.count("\n"), 1)
        self.assertEqual(json.loads(failed.stdout), {"error": "duplicate JSON object key"})
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_import_duplicate_keys_creates_nothing(self):
        missing = Path(self.temp.name) / "nested" / "store.json"
        batch = Path(self.temp.name) / "batch.json"
        batch.write_text(
            '{"1.0.0":[{"category":"Added","text":"One"}],"1.0.0":[{"category":"Fixed","text":"Two"}]}',
            encoding="utf-8")
        failed = subprocess.run(
            [sys.executable, str(ROOT / "release_desk.py"), "--store", str(missing),
             "import", str(batch)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_cli_read_entrypoints_reject_duplicate_store_keys(self):
        self.path.write_bytes(
            b'{"1.0.0":[{"category":"Added","text":"One"}],'
            b'"9.9.9":[{"category":"Added","text":"Ignored","x":1,"x":2}]}')
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        for args in (["versions"], ["notes", "1.0.0"], ["diff", "1.0.0", "1.0.0"]):
            failed = subprocess.run(prefix + args, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2, args)
            self.assertEqual(json.loads(failed.stdout), {"error": "duplicate JSON object key"})


if __name__ == "__main__":
    unittest.main()
