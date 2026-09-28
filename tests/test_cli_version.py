import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = json.loads(
    (ROOT / "electron" / "package.json").read_text(encoding="utf-8")
)["version"]


class CliVersionTests(unittest.TestCase):
    def run_cli(self, command):
        with tempfile.TemporaryDirectory(prefix="oroio version test ") as profile:
            env = os.environ.copy()
            env["HOME"] = profile
            env["USERPROFILE"] = profile
            return subprocess.run(
                command,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            ).stdout.strip()

    @unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "Unix Bash required")
    def test_bash_version_matches_desktop_version(self):
        for argument in ("version", "-v", "--version"):
            with self.subTest(argument=argument):
                output = self.run_cli(["bash", str(ROOT / "bin" / "dk"), argument])
                self.assertEqual(output, f"dk {EXPECTED_VERSION}")

    @unittest.skipUnless(os.name == "nt", "Windows required")
    def test_powershell_version_matches_desktop_version(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            self.skipTest("PowerShell required")
        for argument in ("version", "-v", "--version"):
            with self.subTest(argument=argument):
                output = self.run_cli(
                    [shell, "-NoProfile", "-NonInteractive", "-File", str(ROOT / "bin" / "dk.ps1"), argument]
                )
                self.assertEqual(output, f"dk {EXPECTED_VERSION}")


if __name__ == "__main__":
    unittest.main()
