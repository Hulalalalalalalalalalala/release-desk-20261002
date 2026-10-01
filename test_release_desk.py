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

    def test_import_new_versions_sorted(self):
        payload = {
            "1.10.0": [{"category": "Added", "text": "Bigger"}],
            "0.9.0": [{"category": "Fixed", "text": "Earlier", "note": "ignored"}],
            "1.2.0": [{"category": "Changed", "text": " Mid "}],
        }
        result = self.desk.import_releases(payload)
        self.assertEqual(result, {"imported": ["0.9.0", "1.2.0", "1.10.0"], "skipped": []})
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["1.2.0"], [{"category": "Changed", "text": "Mid"}])
        self.assertEqual(set(stored["0.9.0"][0]), {"category", "text"})
        self.assertEqual(self.desk.versions(), ["0.9.0", "1.2.0", "1.10.0"])

    def test_import_empty_payload_creates_nothing(self):
        self.assertEqual(self.desk.import_releases({}), {"imported": [], "skipped": []})
        self.assertFalse(self.path.exists())

    def test_import_skips_identical_and_preserves_bytes(self):
        self.path.write_bytes(b'{"1.0.0": [{"category": "Added", "text": "Keep whitespace", "extra": 1}]}\n')
        before = self.path.read_bytes()
        result = self.desk.import_releases({"1.0.0": [{"category": "Added", "text": "  Keep whitespace  "}]})
        self.assertEqual(result, {"imported": [], "skipped": ["1.0.0"]})
        self.assertEqual(self.path.read_bytes(), before)

    def test_import_mixed_skip_and_import(self):
        self.desk.add("2.0.0", [{"category": "Added", "text": "Two"}])
        result = self.desk.import_releases({
            "3.0.0": [{"category": "Fixed", "text": "Three"}],
            "2.0.0": [{"category": "Added", "text": "Two"}],
            "1.0.0": [{"category": "Changed", "text": "One"}, {"category": "Changed", "text": "One"}],
        })
        self.assertEqual(result, {"imported": ["1.0.0", "3.0.0"], "skipped": ["2.0.0"]})
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["2.0.0"], [{"category": "Added", "text": "Two"}])
        self.assertEqual(len(stored["1.0.0"]), 2)

    def test_import_conflicts_leave_no_partial_result(self):
        original = b'{"1.0.0": [{"category": "Added", "text": "Same"}, {"category": "Fixed", "text": "Other"}]}\n'
        self.path.write_bytes(original)
        payloads = (
            {"1.0.0": [{"category": "Added", "text": "Different"}], "2.0.0": [{"category": "Added", "text": "New"}]},
            {"1.0.0": [{"category": "Fixed", "text": "Other"}, {"category": "Added", "text": "Same"}]},
            {"1.0.0": [{"category": "Added", "text": "Same"}]},
            {"1.0.0": [{"category": "Added", "text": "same"}], "2.0.0": [{"category": "Added", "text": "New"}]},
        )
        for payload in payloads:
            with self.assertRaises(ValueError):
                self.desk.import_releases(payload)
            self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.desk.versions(), ["1.0.0"])

    def test_import_invalid_payloads_change_nothing(self):
        bad_payloads = (
            [],
            {"v1.0.0": [{"category": "Added", "text": "x"}]},
            {"1.0": [{"category": "Added", "text": "x"}]},
            {"1.0.0": []},
            {"1.0.0": "nope"},
            {"1.0.0": [{"category": "Other", "text": "x"}]},
            {"1.0.0": [{"category": "Added", "text": "  "}]},
            {"1.0.0": [{"category": "Added", "text": "a\nb"}]},
            {"1.0.0": [{"text": "x"}]},
            {"1.0.0": [42]},
        )
        for payload in bad_payloads:
            with self.assertRaises(ValueError):
                self.desk.import_releases(payload)
            self.assertFalse(self.path.exists())

    def test_import_invalid_target_store(self):
        bad_stores = (b"\xff\xfe", b"[1, 2]", b'{"v1": []}',
                      b'{"1.0.0": []}', b'{"1.0.0": [{"category": "Nope", "text": "x"}]}')
        payload = {"2.0.0": [{"category": "Added", "text": "New"}]}
        for content in bad_stores:
            self.path.write_bytes(content)
            with self.assertRaises(ValueError):
                self.desk.import_releases(payload)
            self.assertEqual(self.path.read_bytes(), content)

    def test_import_unicode_and_duplicates(self):
        result = self.desk.import_releases({"1.0.0": [
            {"category": "Added", "text": "café ☃"},
            {"category": "Fixed", "text": "Repeat"},
            {"category": "Fixed", "text": "Repeat"},
        ]})
        self.assertEqual(result["imported"], ["1.0.0"])
        self.assertEqual(self.desk.notes("1.0.0"), "# 1.0.0\n\n## Added\n- café ☃\n\n## Fixed\n- Repeat\n- Repeat\n")

    def test_cli_import(self):
        prefix = [sys.executable, str(ROOT / "release_desk.py"), "--store", str(self.path)]
        source = Path(self.temp.name) / "incoming.json"
        source.write_text(json.dumps({
            "1.10.0": [{"category": "Added", "text": "Ten"}],
            "1.2.0": [{"category": "Fixed", "text": "Two"}],
        }), encoding="utf-8")
        result = subprocess.run(prefix + ["import", str(source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"imported": ["1.2.0", "1.10.0"], "skipped": []})
        again = subprocess.run(prefix + ["import", str(source)], capture_output=True, text=True)
        self.assertEqual(json.loads(again.stdout), {"imported": [], "skipped": ["1.2.0", "1.10.0"]})
        source.write_bytes(b"\xff\xfe")
        bad_encoding = subprocess.run(prefix + ["import", str(source)], capture_output=True, text=True)
        self.assertEqual(bad_encoding.returncode, 2)
        self.assertIn("error", json.loads(bad_encoding.stdout))
        missing = subprocess.run(prefix + ["import", str(Path(self.temp.name) / "missing.json")], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("error", json.loads(missing.stdout))


if __name__ == "__main__":
    unittest.main()
