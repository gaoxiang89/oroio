import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


DK_SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "dk.ps1"
PWSH = shutil.which("pwsh")

# CommandLineToArgvW vectors. Input, expected quoted argument.
QUOTE_VECTORS = [
    ["a", "a"],
    ["", '""'],
    ["a b", '"a b"'],
    ['a"b', '"a\\"b"'],
    ["a\\b", "a\\b"],
    ["a\\b c", '"a\\b c"'],
    ['a\\"', '"a\\\\\\""'],
    ['"', '"\\""'],
    ["foo\\", "foo\\"],
    ["foo bar\\", '"foo bar\\\\"'],
    ['foo\\"bar', '"foo\\\\\\"bar"'],
    ["a\tb", '"a\tb"'],
]


@unittest.skipUnless(PWSH, "pwsh required")
class InheritedProcessTests(unittest.TestCase):
    def powershell(self, body, env_extra=None, stdin=None):
        script = r"""
            $ErrorActionPreference = 'Stop'
            $source = Get-Content -LiteralPath $env:DK_TEST_SCRIPT -Raw -Encoding UTF8
            $tokens = $null
            $parseErrors = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseInput(
                $source, [ref]$tokens, [ref]$parseErrors
            )
            if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
            foreach ($name in @(
                'ConvertTo-CommandLineArgument',
                'ConvertTo-WindowsCommandLine',
                'Invoke-InheritedProcess'
            )) {
                $fn = $ast.Find({
                    param($node)
                    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                        $node.Name -eq $name
                }, $true)
                if (-not $fn) { throw "missing $name" }
                Invoke-Expression $fn.Extent.Text
            }
        """ + body
        env = os.environ.copy()
        env["DK_TEST_SCRIPT"] = str(DK_SCRIPT)
        if env_extra:
            env.update(env_extra)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "driver.ps1"
            path.write_text(script, encoding="utf-8")
            return subprocess.run(
                [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path)],
                env=env,
                input=stdin,
                capture_output=True,
                timeout=30,
            )

    def test_windows_quoting_matches_command_line_to_argv(self):
        result = self.powershell(
            r"""
                $vectors = $env:DK_QUOTE_VECTORS | ConvertFrom-Json
                $got = foreach ($pair in $vectors) {
                    ConvertTo-CommandLineArgument -Argument ([string]$pair[0])
                }
                $got | ConvertTo-Json -Compress | Write-Output
            """,
            {"DK_QUOTE_VECTORS": json.dumps(QUOTE_VECTORS)},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        quoted = json.loads(result.stdout.decode())
        self.assertEqual(quoted, [pair[1] for pair in QUOTE_VECTORS])

    def test_inherited_stdio_is_byte_transparent(self):
        payload = b'Content-Length: 2\r\n\r\n{"jsonrpc":"2.0","id":1}\r\n\xe4\xb8\xad'
        code = (
            "import sys\n"
            "data = sys.stdin.buffer.read()\n"
            "sys.stdout.buffer.write(data)\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.write(b'err-line\\n')\n"
            "sys.stderr.buffer.flush()\n"
            "raise SystemExit(7)\n"
        )
        result = self.powershell(
            r"""
                $code = Invoke-InheritedProcess -FilePath $env:DK_TEST_PYTHON -ArgumentList @(
                    '-c', $env:DK_TEST_PY
                )
                exit $code
            """,
            {
                "DK_TEST_PYTHON": sys.executable,
                "DK_TEST_PY": code,
            },
            stdin=payload,
        )
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, payload)
        self.assertIn(b"err-line\n", result.stderr)
        self.assertNotIn(b"\x00", result.stdout)
        self.assertFalse(result.stdout.startswith(b"\xff\xfe"))

    def test_child_inherits_factory_api_key(self):
        code = (
            "import os, sys\n"
            "sys.stdout.buffer.write(os.environ.get('FACTORY_API_KEY', '').encode('utf-8'))\n"
        )
        result = self.powershell(
            r"""
                $env:FACTORY_API_KEY = $env:DK_TEST_KEY
                $code = Invoke-InheritedProcess -FilePath $env:DK_TEST_PYTHON -ArgumentList @(
                    '-c', $env:DK_TEST_PY
                )
                exit $code
            """,
            {
                "DK_TEST_PYTHON": sys.executable,
                "DK_TEST_PY": code,
                "DK_TEST_KEY": "fk-test-key",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"fk-test-key")

    def test_argument_with_spaces_reaches_child(self):
        code = (
            "import sys\n"
            "sys.stdout.buffer.write(sys.argv[1].encode('utf-8'))\n"
        )
        argument = 'hello world "\u4e2d"'
        result = self.powershell(
            r"""
                $code = Invoke-InheritedProcess -FilePath $env:DK_TEST_PYTHON -ArgumentList @(
                    '-c', $env:DK_TEST_PY, $env:DK_TEST_ARG
                )
                exit $code
            """,
            {
                "DK_TEST_PYTHON": sys.executable,
                "DK_TEST_PY": code,
                "DK_TEST_ARG": argument,
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout.decode("utf-8"), argument)


if __name__ == "__main__":
    unittest.main()
