from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from desktop.build_release import _safe_archive_name, selftest, verify


class ReleaseVerifierTests(unittest.TestCase):
    def test_selftest_is_reproducible_and_rejects_unsafe_paths(self):
        result = selftest()
        self.assertTrue(result["selftest"])
        self.assertEqual(result["unsafe_paths_rejected"], 4)
        self.assertEqual(len(result["sha256"]), 64)

    def test_path_policy_rejects_traversal_and_windows_spellings(self):
        for value in ("../x", "/x", "C:/x", "nested\\x", "a/../b"):
            with self.assertRaises(ValueError, msg=value):
                _safe_archive_name(value)

    def test_missing_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("payload/server.py", "pass\n")
            with self.assertRaisesRegex(ValueError, "manifest missing"):
                verify(archive)


if __name__ == "__main__":
    unittest.main()
