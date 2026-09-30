import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DK_SCRIPT = ROOT / "bin" / "dk"


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "Unix Bash required")
class BashListDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oroio list test ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.mock_bin = Path(self.temp.name) / "bin"
        self.home.mkdir()
        self.mock_bin.mkdir()

        curl = self.mock_bin / "curl"
        curl.write_text(
            """#!/usr/bin/env bash
case "$*" in
  *api/app/auth/me*)
    printf '%s\\n%s' '{"userProfile":{"email":"tester@example.com"}}' '200'
    ;;
  *api/billing/limits*)
    printf '%s\\n%s' '{"usesTokenRateLimitsBilling":true,"limits":{"standard":{"fiveHour":{"usedPercent":39,"windowEnd":"2026-10-01T05:00:00Z"},"weekly":{"usedPercent":23,"windowEnd":"2026-10-08T06:30:00Z"},"monthly":{"usedPercent":6,"windowEnd":"2026-10-31T07:45:00Z"}}}}' '200'
    ;;
  *)
    printf '%s\\n%s' '{}' '404'
    ;;
esac
""",
            encoding="utf-8",
        )
        curl.chmod(0o755)

        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.home),
                "PATH": str(self.mock_bin) + os.pathsep + self.env["PATH"],
                "DKM_CACHE_TTL": "0",
                "DKM_COLOR": "never",
                "COLUMNS": "200",
                "TZ": "UTC",
            }
        )
        self.run_dk("add", "fk-1234567890abcdef")

    def run_dk(self, *arguments):
        return subprocess.run(
            ["bash", str(DK_SCRIPT), *arguments],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout

    def test_list_shows_each_window_end_and_omits_expiry_column(self):
        output = self.run_dk("list")

        self.assertNotIn("Expiry", output)
        self.assertIn("End 10-01 05:00", output)
        self.assertIn("End 10-08 06:30", output)
        self.assertIn("End 10-31 07:45", output)

    def test_current_shows_window_ends_without_expiry_row(self):
        output = self.run_dk("current")

        self.assertNotIn("Expiry", output)
        self.assertIn("End 10-01 05:00", output)
        self.assertIn("End 10-08 06:30", output)
        self.assertIn("End 10-31 07:45", output)


if __name__ == "__main__":
    unittest.main()
