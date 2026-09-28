import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ServeCliTests:
    shell = None
    windows = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oroio serve test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Stage exactly the source scripts with installer-compatible encodings.
        for name in ("dk", "dk.ps1", "serve.py", "byok.py"):
            text = (ROOT / "bin" / name).read_text(encoding="utf-8")
            encoding = "utf-8-sig" if name == "dk.ps1" else "utf-8"
            (self.bin / name).write_bytes(text.encode(encoding))
        self.runner = self.bin / "run.ps1"
        self.runner.write_text("""
            [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
            function Read-Host {
                param([string]$Prompt, [switch]$AsSecureString)
                $pin = [Security.SecureString]::new()
                foreach ($digit in '1234'.ToCharArray()) { $pin.AppendChar($digit) }
                return $pin
            }
            & (Join-Path $PSScriptRoot 'dk.ps1') serve @args
            if ($LASTEXITCODE) { exit $LASTEXITCODE }
        """, encoding="utf-8-sig")
        self.profiles = []
        self.addCleanup(self.stop_services)
        self.profile = self.new_profile("first")
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def new_profile(self, name):
        profile = self.root / name
        web = profile / ".oroio" / "web"
        web.mkdir(parents=True)
        (web / "index.html").write_text("oroio-port-test", encoding="utf-8")
        self.profiles.append(profile)
        return profile

    def cli(self, action="start", *, port=None, profile=None):
        profile = profile or self.profile
        env = os.environ.copy()
        env.pop("PSModulePath", None)
        env["USERPROFILE" if self.windows else "HOME"] = str(profile)
        env.pop("DKM_SERVE_PORT", None)
        if port is not None:
            env["DKM_SERVE_PORT"] = str(port)
        if self.windows:
            command = [self.shell, "-NoProfile", "-NonInteractive", "-File", str(self.runner), action]
        else:
            command = [self.shell, str(self.bin / "dk"), "serve", action]
        # Detached Windows children can inherit pipe handles and prevent EOF.
        # Capture CLI output in a file so the test waits only for the launcher.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=output, timeout=30)
            output.seek(0)
            result.stdout = output.read().decode("utf-8", errors="replace")
            result.stderr = ""
            return result

    def stop_services(self):
        for profile in self.profiles:
            if (profile / ".oroio" / "serve.pid").exists():
                result = self.cli("stop", profile=profile)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_started(self, result, profile=None):
        profile = profile or self.profile
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        port = int((profile / ".oroio" / "serve.port").read_text())
        self.assertGreater(port, 0)
        self.assertLessEqual(port, 65535)
        self.assertIn(f"http://localhost:{port}", result.stdout)
        with self.http.open(f"http://127.0.0.1:{port}/", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"oroio-port-test", response.read())
        return port

    def test_auto_ports_and_status_are_independent_of_terminal_environment(self):
        # A stale port must never count as readiness for the new process.
        (self.profile / ".oroio" / "serve.port").write_text("1")
        first = self.assert_started(self.cli())
        second_profile = self.new_profile("second")
        second = self.assert_started(self.cli(port=0, profile=second_profile), second_profile)
        self.assertNotEqual(first, second)
        for action in ("status", "start"):
            result = self.cli(action, port="invalid-setting-in-new-terminal")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f"http://localhost:{first}", result.stdout)
        result = self.cli("stop")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.profile / ".oroio" / "serve.port").exists())
        self.assertFalse((self.profile / ".oroio" / "serve.pid").exists())

    def test_fixed_port_is_honored_and_recorded(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        actual = self.assert_started(self.cli(port=port))
        self.assertEqual(actual, port)
        result = self.cli("status")
        self.assertIn(f"http://localhost:{port}", result.stdout)

    def test_busy_fixed_port_fails_without_switching_ports(self):
        with socket.socket() as listener:
            listener.bind(("0.0.0.0", 0))
            listener.listen()
            result = self.cli(port=listener.getsockname()[1])
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.profile / ".oroio" / "serve.port").exists())
        self.assertFalse((self.profile / ".oroio" / "serve.pid").exists())

    def test_invalid_ports_fail_clearly(self):
        for port in ("-1", "65536", "abc"):
            with self.subTest(port=port):
                result = self.cli(port=port)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DKM_SERVE_PORT", result.stdout + result.stderr)
        self.assertFalse((self.profile / ".oroio" / "serve.pid").exists())

    def test_startup_failure_does_not_report_a_stale_port(self):
        (self.profile / ".oroio" / "serve.port").write_text("12345")
        (self.bin / "serve.py").write_text("raise RuntimeError('startup-test-failure')\n")
        result = self.cli()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("http://localhost:", result.stdout)
        self.assertFalse((self.profile / ".oroio" / "serve.port").exists())
        self.assertFalse((self.profile / ".oroio" / "serve.pid").exists())

    def test_authentication_on_allocated_port(self):
        port = self.assert_started(self.cli())

        def post(path, data):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.http.open(request, timeout=5) as response:
                return json.load(response)

        state = post("/api/auth/check", {})
        # PowerShell enters the test PIN; noninteractive Bash skips the prompt.
        self.assertEqual(state["required"], self.windows)
        if self.windows:
            self.assertFalse(state["authenticated"])
            self.assertFalse(post("/api/auth", {"pin": "0000"})["success"])
        self.assertTrue(post("/api/auth", {"pin": "1234"})["success"])


@unittest.skipUnless(os.name != "nt" and shutil.which("bash"), "POSIX Bash required")
class BashServeTests(ServeCliTests, unittest.TestCase):
    shell = shutil.which("bash")


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell required")
class WindowsPowerShellServeTests(ServeCliTests, unittest.TestCase):
    shell = shutil.which("powershell")
    windows = True


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "PowerShell 7 on Windows required")
class PowerShell7ServeTests(ServeCliTests, unittest.TestCase):
    shell = shutil.which("pwsh")
    windows = True


if __name__ == "__main__":
    unittest.main()
