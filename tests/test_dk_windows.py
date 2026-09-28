import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DK_SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "dk.ps1"
POWERSHELLS = [path for name in ("powershell", "pwsh") if (path := shutil.which(name))]


@unittest.skipUnless(os.name == "nt" and POWERSHELLS, "Windows PowerShell required")
class PythonDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oroio python test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def command(self, name, body, directory="first"):
        folder = self.root / directory
        folder.mkdir(exist_ok=True)
        (folder / f"{name}.cmd").write_text("@echo off\n" + body + "\n")
        return folder

    def python_command(self, name="python", directory="first"):
        return self.command(name, f'"{sys.executable}" %*', directory)

    def discover(self, folders, expected=None):
        # Load only the selector, avoiding the CLI and the user's key store.
        script = """
            $ErrorActionPreference = 'Stop'
            $source = Get-Content -LiteralPath $env:DK_TEST_SCRIPT -Raw -Encoding UTF8
            $tokens = $null
            $parseErrors = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseInput(
                $source, [ref]$tokens, [ref]$parseErrors
            )
            if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
            $selector = $ast.Find({
                param($node)
                $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                    $node.Name -eq 'Get-Python'
            }, $true)
            Invoke-Expression $selector.Extent.Text
            function Write-ErrorExit { param([string]$Message) throw $Message }
            try {
                $python = Get-Python
                if ($python -ne $env:DK_TEST_EXPECTED) { throw 'Unexpected interpreter' }
                & $python -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)'
                exit $LASTEXITCODE
            } catch {
                if (-not $env:DK_TEST_EXPECTED -and $_.Exception.Message -like '*Python 3*') {
                    exit 0
                }
                Write-Error $_ -ErrorAction Continue
                exit 1
            }
        """
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join(map(str, folders))
        env["DK_TEST_SCRIPT"] = str(DK_SCRIPT)
        env["DK_TEST_EXPECTED"] = str(expected or "")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        for shell in POWERSHELLS:
            with self.subTest(shell=shell):
                result = subprocess.run(
                    [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                    env=env,
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, repr(result.stdout + result.stderr))

    def test_python3_store_alias_does_not_hide_working_python(self):
        folder = self.command("python3", "exit /b 9009")
        self.python_command()
        self.discover([folder], sys.executable)

    def test_broken_python_falls_back_to_python3(self):
        folder = self.command("python", "exit /b 9009")
        self.python_command("python3")
        self.discover([folder], sys.executable)

    def test_searches_later_path_entries(self):
        first = self.command("python", "exit /b 9009")
        second = self.python_command(directory="second")
        self.discover([first, second], sys.executable)

    def test_launcher_resolves_actual_interpreter(self):
        folder = self.command(
            "py",
            'if not "%~1"=="-3" exit /b 2\n'
            f'"{sys.executable}" %2 %3',
        )
        self.discover([folder], sys.executable)

    def test_zero_exit_without_interpreter_output_is_rejected(self):
        folder = self.command("python", "exit /b 0")
        self.discover([folder])

    def test_only_broken_aliases_reports_missing_python(self):
        folder = self.command("python", "exit /b 9009")
        self.command("python3", "exit /b 9009")
        self.discover([folder])


if __name__ == "__main__":
    unittest.main()
