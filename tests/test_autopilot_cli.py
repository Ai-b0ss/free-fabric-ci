import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autopilot.cli import validate_snapshot


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "scheduler_snapshot.json"


class SchedulerCliTests(unittest.TestCase):
    def test_valid_fixture(self):
        self.assertEqual(validate_snapshot(FIXTURE), (7, 2))

    def test_module_cli_accepts_valid_fixture(self):
        result = subprocess.run(
            [sys.executable, "-m", "autopilot", "validate", str(FIXTURE)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "STATE_VALID generation=7 packets=2")
        self.assertEqual(result.stderr, "")

    def test_module_cli_rejects_invalid_json_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not-json", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "autopilot", "validate", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("STATE_INVALID: invalid JSON", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_module_cli_rejects_invalid_snapshot_without_traceback(self):
        state = json.loads(FIXTURE.read_text(encoding="utf-8"))
        state["packets"]["VERIFY"]["state"] = "IMPOSSIBLE"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid-state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "autopilot", "validate", str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("STATE_INVALID: invalid packet state: VERIFY", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_snapshot_root_must_be_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "array.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "root must be an object"):
                validate_snapshot(path)


if __name__ == "__main__":
    unittest.main()
