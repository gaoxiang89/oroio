import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import byok  # noqa: E402


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        return self.body[:limit]


class RawResponse(Response):
    def __init__(self, body):
        self.body = body


def opener_for(payload, requests=None):
    def open_url(request, timeout):
        if requests is not None:
            requests.append((request, timeout))
        return Response(payload)

    return open_url


class ByokTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.factory = root / ".factory"
        self.oroio = root / ".oroio"

    def tearDown(self):
        self.temp.cleanup()

    def read(self, path):
        return json.loads(Path(path).read_text())

    def write(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def models(self, *rows):
        return opener_for({"object": "list", "data": list(rows)})

    def test_normalizes_three_common_shapes_and_selects_newest(self):
        result = byok.normalize_models({
            "data": [
                {"id": "old", "created": 10},
                {
                    "id": "new",
                    "name": "New Model",
                    "created": 20,
                    "context_length": 128000,
                    "max_output_tokens": 8192,
                    "input_modalities": ["text", "image"],
                    "capabilities": {"reasoning": True},
                },
            ]
        })
        self.assertFalse(result[0].get("recommended", False))
        self.assertTrue(result[1]["recommended"])
        self.assertEqual(result[1]["contextLength"], 128000)
        self.assertEqual(result[1]["maxOutputTokens"], 8192)
        self.assertTrue(result[1]["supportsImages"])
        self.assertTrue(result[1]["supportsReasoning"])

        nested = byok.normalize_models({"models": [{"model": "kimi-code", "displayName": "Kimi"}]})
        self.assertEqual(nested[0]["id"], "kimi-code")
        self.assertTrue(nested[0]["recommended"])

    def test_discovery_uses_fixed_endpoint_bearer_and_never_returns_key(self):
        requests = []
        secret = "test-secret-value"
        result = byok.discover(
            "glm",
            secret,
            self.factory,
            self.oroio,
            opener=opener_for({"data": [{"id": "glm-code"}]}, requests),
        )
        request, timeout = requests[0]
        self.assertEqual(request.full_url, byok.PROVIDERS["glm"]["modelsUrl"])
        self.assertEqual(request.get_header("Authorization"), f"Bearer {secret}")
        self.assertEqual(timeout, byok.REQUEST_TIMEOUT)
        self.assertNotIn(secret, json.dumps(result))

    def test_openai_compatible_discovers_models_from_user_base_url(self):
        requests = []
        result = byok.discover_openai_compatible(
            "https://api.example.test/v1/",
            "custom-secret",
            self.factory,
            self.oroio,
            opener=opener_for({"data": [{"id": "gpt-custom"}, {"id": "claude-custom"}]}, requests),
        )
        request, timeout = requests[0]
        self.assertEqual(request.full_url, "https://api.example.test/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer custom-secret")
        self.assertEqual(timeout, byok.REQUEST_TIMEOUT)
        self.assertEqual(result["baseUrl"], "https://api.example.test/v1")
        self.assertEqual([model["id"] for model in result["models"]], ["gpt-custom", "claude-custom"])
        self.assertNotIn("custom-secret", json.dumps(result))

    def test_openai_compatible_apply_syncs_selected_models(self):
        payload = {"data": [{"id": "model-a"}, {"id": "model-b"}]}
        byok.apply_openai_compatible(
            "https://api.example.test/v1",
            "first-key",
            ["model-a", "model-b"],
            self.factory,
            self.oroio,
            opener=opener_for(payload),
        )
        settings = self.read(self.factory / "settings.json")
        self.assertEqual([row["model"] for row in settings["customModels"]], ["model-a", "model-b"])
        self.assertTrue(all(row["baseUrl"] == "https://api.example.test/v1" for row in settings["customModels"]))
        self.assertTrue(all(row["provider"] == "generic-chat-completion-api" for row in settings["customModels"]))

        byok.apply_openai_compatible(
            "https://api.example.test/v1/",
            "replacement-key",
            ["model-b"],
            self.factory,
            self.oroio,
            opener=opener_for(payload),
        )
        settings = self.read(self.factory / "settings.json")
        self.assertEqual([row["model"] for row in settings["customModels"]], ["model-b"])
        self.assertEqual(settings["customModels"][0]["apiKey"], "replacement-key")
        self.assertNotIn("replacement-key", (self.oroio / "byok.json").read_text())

    def test_openai_compatible_rejects_unsafe_base_urls(self):
        invalid = [
            "",
            "ftp://api.example.test/v1",
            "https://user:password@api.example.test/v1",
            "https://api.example.test/v1?token=secret",
            "https://api.example.test/v1#models",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(byok.ByokError) as caught:
                byok.normalize_openai_base_url(value)
            self.assertEqual(caught.exception.code, "invalid_base_url")
        self.assertEqual(byok.normalize_openai_base_url("http://127.0.0.1:8317/v1/models"), "http://127.0.0.1:8317/v1")

    def test_http_errors_have_stable_safe_messages(self):
        for status, code in ((401, "invalid_key"), (403, "forbidden"), (429, "rate_limited"), (500, "provider_error")):
            def fail(request, timeout, status=status):
                raise urllib.error.HTTPError(request.full_url, status, "secret must not appear", {}, None)

            with self.subTest(status=status), self.assertRaises(byok.ByokError) as caught:
                byok.discover("deepseek", "never-print-me", self.factory, self.oroio, opener=fail)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("never-print-me", caught.exception.message)
            self.assertNotIn("secret must not appear", caught.exception.message)

    def test_timeout_empty_malformed_and_oversized_responses(self):
        def timeout(_request, **_kwargs):
            raise socket.timeout()

        cases = [
            (timeout, "timeout"),
            (lambda *_args, **_kwargs: Response({"data": []}), "no_models"),
            (lambda *_args, **_kwargs: RawResponse(b"not-json"), "invalid_response"),
            (lambda *_args, **_kwargs: RawResponse(b"x" * (byok.MAX_RESPONSE_BYTES + 1)), "invalid_response"),
        ]
        for opener, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(byok.ByokError) as caught:
                byok.discover("kimi", "safe-test-key", self.factory, self.oroio, opener=opener)
            self.assertEqual(caught.exception.code, expected)

    def test_failed_apply_does_not_modify_existing_settings(self):
        original = {"customModels": [{"model": "manual", "baseUrl": "https://example.test", "apiKey": "keep", "provider": "openai"}]}
        self.write(self.factory / "settings.json", original)

        def reject(request, **_kwargs):
            raise urllib.error.HTTPError(request.full_url, 401, "rejected", {}, None)

        with self.assertRaises(byok.ByokError):
            byok.apply("glm", "bad-key", ["anything"], self.factory, self.oroio, opener=reject)
        self.assertEqual(self.read(self.factory / "settings.json"), original)
        self.assertFalse((self.oroio / "byok.json").exists())

    def test_apply_writes_current_settings_preserves_unknown_fields_and_is_idempotent(self):
        self.write(self.factory / "settings.json", {"theme": "dark", "unknown": {"keep": True}})
        payload = {"data": [{
            "id": "deepseek-chat",
            "display_name": "DeepSeek Chat",
            "max_output_tokens": 8192,
            "supports_images": False,
        }]}
        first = byok.apply(
            "deepseek", "key-one", ["deepseek-chat"], self.factory, self.oroio, opener=opener_for(payload)
        )
        settings = self.read(self.factory / "settings.json")
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["unknown"], {"keep": True})
        entry = settings["customModels"][0]
        self.assertEqual(entry["baseUrl"], "https://api.deepseek.com/anthropic")
        self.assertEqual(entry["provider"], "anthropic")
        self.assertEqual(entry["authMode"], "bearer")
        self.assertEqual(entry["maxOutputTokens"], 8192)
        self.assertTrue(entry["noImageSupport"])
        self.assertEqual(first["managedModelIds"], ["deepseek-chat"])
        before = json.dumps(settings, sort_keys=True)

        byok.apply("deepseek", "key-one", ["deepseek-chat"], self.factory, self.oroio, opener=opener_for(payload))
        after = json.dumps(self.read(self.factory / "settings.json"), sort_keys=True)
        self.assertEqual(before, after)
        self.assertNotIn("key-one", (self.oroio / "byok.json").read_text())
        if os.name != "nt":
            self.assertEqual((self.factory / "settings.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.oroio / "byok.json").stat().st_mode & 0o777, 0o600)

    def test_key_replacement_and_unavailable_model_retention_then_explicit_delete(self):
        initial = {"data": [{"id": "model-a"}, {"id": "model-b"}]}
        byok.apply("glm", "old-key", ["model-a", "model-b"], self.factory, self.oroio, opener=opener_for(initial))

        refresh_result = byok.refresh(
            "glm", self.factory, self.oroio, opener=opener_for({"data": [{"id": "model-a"}, {"id": "model-c"}]})
        )
        self.assertEqual(refresh_result["unavailableModelIds"], ["model-b"])
        settings = self.read(self.factory / "settings.json")
        self.assertEqual([row["model"] for row in settings["customModels"]], ["model-a", "model-b"])

        discovered = byok.discover(
            "glm", "new-key", self.factory, self.oroio,
            opener=opener_for({"data": [{"id": "model-a"}, {"id": "model-c"}]})
        )
        flags = {row["id"]: row for row in discovered["models"]}
        self.assertTrue(flags["model-b"]["selected"])
        self.assertTrue(flags["model-b"]["unavailable"])
        self.assertFalse(flags["model-c"]["selected"])
        self.assertTrue(flags["model-c"]["isNew"])

        byok.apply(
            "glm", "new-key", ["model-a"], self.factory, self.oroio,
            opener=opener_for({"data": [{"id": "model-a"}, {"id": "model-c"}]})
        )
        settings = self.read(self.factory / "settings.json")
        self.assertEqual([row["model"] for row in settings["customModels"]], ["model-a"])
        self.assertEqual(settings["customModels"][0]["apiKey"], "new-key")

    def test_apply_can_reuse_saved_key_after_refresh(self):
        byok.apply(
            "kimi", "saved-key", ["model-a"], self.factory, self.oroio,
            opener=self.models({"id": "model-a"}, {"id": "model-b"}),
        )
        byok.apply(
            "kimi", "", ["model-a", "model-b"], self.factory, self.oroio,
            opener=self.models({"id": "model-a"}, {"id": "model-b"}),
        )
        settings = self.read(self.factory / "settings.json")
        self.assertEqual([row["model"] for row in settings["customModels"]], ["model-a", "model-b"])
        self.assertTrue(all(row["apiKey"] == "saved-key" for row in settings["customModels"]))

    def test_managed_legacy_entry_stays_in_legacy_and_other_fields_survive(self):
        self.write(self.factory / "settings.json", {"customModels": [], "keep": 1})
        self.write(self.factory / "config.json", {
            "other": "keep",
            "custom_models": [{
                "model": "kimi-old",
                "model_display_name": "Old",
                "base_url": byok.PROVIDERS["kimi"]["baseUrl"],
                "api_key": "old",
                "provider": "generic-chat-completion-api",
                "custom_field": "keep",
            }],
        })
        self.write(self.oroio / "byok.json", {
            "version": 1,
            "providers": {"kimi": {
                "managedModels": ["kimi-old"],
                "locations": {"kimi-old": "legacy"},
                "knownModels": [{"id": "kimi-old", "displayName": "Old"}],
                "unavailable": [],
            }},
        })
        byok.apply(
            "kimi", "replacement", ["kimi-old"], self.factory, self.oroio,
            opener=self.models({"id": "kimi-old", "display_name": "Updated"}),
        )
        self.assertEqual(self.read(self.factory / "settings.json")["customModels"], [])
        legacy = self.read(self.factory / "config.json")
        self.assertEqual(legacy["other"], "keep")
        self.assertEqual(legacy["custom_models"][0]["api_key"], "replacement")
        self.assertEqual(legacy["custom_models"][0]["custom_field"], "keep")

    def test_remove_provider_only_deletes_managed_entries(self):
        self.write(self.factory / "settings.json", {
            "customModels": [{"model": "manual", "baseUrl": "https://example.test", "apiKey": "x", "provider": "openai"}]
        })
        byok.apply("kimi", "key", ["kimi-code"], self.factory, self.oroio, opener=self.models({"id": "kimi-code"}))
        result = byok.remove_provider("kimi", self.factory, self.oroio)
        self.assertEqual(result["removedModelIds"], ["kimi-code"])
        settings = self.read(self.factory / "settings.json")
        self.assertEqual([row["model"] for row in settings["customModels"]], ["manual"])

    def test_manual_list_update_and_remove_merge_current_and_legacy(self):
        self.write(self.factory / "settings.json", {
            "topLevel": True,
            "customModels": [{"model": "same", "displayName": "Current", "baseUrl": "a", "apiKey": "k", "provider": "openai", "keep": 1}],
        })
        self.write(self.factory / "config.json", {
            "custom_models": [
                {"model": "same", "base_url": "old", "api_key": "old", "provider": "openai"},
                {"model": "legacy", "base_url": "b", "api_key": "l", "provider": "anthropic"},
            ]
        })
        rows = byok.list_custom_models(self.factory)
        self.assertEqual([row["model"] for row in rows], ["same", "legacy"])
        byok.update_custom_model(0, {**rows[0], "model_display_name": "Changed"}, self.factory)
        self.assertEqual(self.read(self.factory / "settings.json")["customModels"][0]["displayName"], "Changed")
        self.assertEqual(self.read(self.factory / "settings.json")["customModels"][0]["keep"], 1)
        byok.remove_custom_model(1, self.factory)
        legacy = self.read(self.factory / "config.json")
        self.assertEqual([row["model"] for row in legacy["custom_models"]], ["same"])

    def test_selection_parser_supports_ranges(self):
        self.assertEqual(byok.parse_selection("1,3-5", 5), [1, 3, 4, 5])
        self.assertEqual(byok.parse_selection("", 5, [2, 4]), [2, 4])
        with self.assertRaises(byok.ByokError):
            byok.parse_selection("0,9", 5)

    def test_cli_setup_hides_key_from_output(self):
        secret = "cli-secret"
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        fake_stdin.readline.side_effect = ["\n"]
        discovered = {
            "success": True,
            "provider": "glm",
            "configured": False,
            "models": [{"id": "glm-code", "displayName": "GLM Code", "selected": True, "recommended": True}],
        }
        output = io.StringIO()
        with mock.patch.object(sys, "stdin", fake_stdin), mock.patch.object(sys, "stdout", output), \
             mock.patch("byok.getpass.getpass", return_value=secret), \
             mock.patch("byok.list_providers", return_value=[{"id": "glm", "configured": False}]), \
             mock.patch("byok.discover", return_value=discovered), mock.patch("byok.apply") as apply_mock, \
             mock.patch("builtins.input", return_value=""):
            result = byok._cli_setup("glm")
        self.assertEqual(result, 0)
        self.assertNotIn(secret, output.getvalue())
        apply_mock.assert_called_once_with("glm", secret, ["glm-code"])

    def test_cli_refresh_and_remove(self):
        output = io.StringIO()
        refresh_result = {
            "managedModelIds": ["model-a"],
            "unavailableModelIds": [],
        }
        with mock.patch.object(sys, "stdout", output), mock.patch("byok.refresh", return_value=refresh_result) as refresh_mock:
            self.assertEqual(byok._cli_refresh("kimi"), 0)
        refresh_mock.assert_called_once_with("kimi")
        self.assertIn("model-a", output.getvalue())

        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = False
        with mock.patch.object(sys, "stdin", fake_stdin), mock.patch.object(sys, "stdout", io.StringIO()), mock.patch("byok.remove_provider", return_value={"removedModelIds": ["model-a"]}) as remove_mock:
            self.assertEqual(byok._cli_remove("kimi"), 0)
        remove_mock.assert_called_once_with("kimi")


if __name__ == "__main__":
    unittest.main()
