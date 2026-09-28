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

    def test_empty_json_config_files_are_treated_as_unconfigured(self):
        self.factory.mkdir(parents=True)
        (self.factory / "settings.json").write_text("")
        (self.factory / "config.json").write_text(" \n")
        rows = byok.list_providers(self.factory, self.oroio)
        self.assertTrue(all(not row["configured"] for row in rows))

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
        payload = {"data": [{"id": "gpt-5.6"}, {"id": "grok-4.6"}]}
        byok.apply_openai_compatible(
            "https://api.example.test/v1",
            "first-key",
            ["gpt-5.6", "grok-4.6"],
            self.factory,
            self.oroio,
            opener=opener_for(payload),
        )
        settings = self.read(self.factory / "settings.json")
        self.assertEqual([row["model"] for row in settings["customModels"]], ["gpt-5.6", "grok-4.6"])
        self.assertTrue(all(row["baseUrl"] == "https://api.example.test/v1" for row in settings["customModels"]))
        self.assertTrue(all(row["provider"] == "generic-chat-completion-api" for row in settings["customModels"]))
        self.assertEqual(settings["customModels"][0]["baseModelId"], "gpt-5.6-sol")
        self.assertNotIn("reasoningEffort", settings["customModels"][1])
        self.assertEqual(settings["customModels"][1]["baseModelId"], "grok-4.6")

        discovered = byok.discover_openai_compatible(
            "https://api.example.test/v1",
            "first-key",
            self.factory,
            self.oroio,
            opener=opener_for(payload),
        )
        self.assertEqual(discovered["models"][0]["reasoningEfforts"], ["none", "low", "medium", "high", "xhigh", "max"])

        byok.apply_openai_compatible(
            "https://api.example.test/v1/",
            "replacement-key",
            ["grok-4.6"],
            self.factory,
            self.oroio,
            opener=opener_for(payload),
        )
        settings = self.read(self.factory / "settings.json")
        self.assertEqual([row["model"] for row in settings["customModels"]], ["grok-4.6"])
        self.assertEqual(settings["customModels"][0]["apiKey"], "replacement-key")
        self.assertNotIn("replacement-key", (self.oroio / "byok.json").read_text())

    def test_official_providers_write_droid_base_model_for_reasoning_selector(self):
        cases = [
            ("glm", "glm-5.3", "max"),
            ("deepseek", "deepseek-v4-pro", "high"),
            ("kimi", "kimi-k3", "low"),
        ]
        for provider, model_id, effort in cases:
            with self.subTest(provider=provider):
                factory = self.factory / provider
                oroio = self.oroio / provider
                discovery = byok.discover(
                    provider,
                    "test-key",
                    factory,
                    oroio,
                    opener=self.models({"id": model_id}),
                )
                self.assertIn(effort, discovery["models"][0]["reasoningEfforts"])
                byok.apply(
                    provider,
                    "test-key",
                    [model_id],
                    factory,
                    oroio,
                    opener=self.models({"id": model_id}),
                )
                entry = self.read(factory / "settings.json")["customModels"][0]
                self.assertEqual(entry["baseModelId"], model_id)

    def test_reasoning_profiles_cover_gpt_aliases_and_only_recognized_models(self):
        payload = {
            "data": [
                {"id": "openai/gpt-5.5"},
                {"id": "gpt-5.6-latest"},
                {"id": "xai/grok-4.6-fast"},
                {"id": "glm-future-model"},
            ]
        }
        discovered = byok.discover_openai_compatible(
            "https://api.example.test/v1",
            "test-key",
            self.factory,
            self.oroio,
            opener=opener_for(payload),
        )
        models = {model["id"]: model for model in discovered["models"]}
        self.assertEqual(models["openai/gpt-5.5"]["reasoningEfforts"], ["low", "medium", "high", "xhigh"])
        self.assertEqual(models["gpt-5.6-latest"]["reasoningEfforts"], ["none", "low", "medium", "high", "xhigh", "max"])
        self.assertEqual(models["xai/grok-4.6-fast"]["reasoningEfforts"], ["low", "medium", "high", "xhigh"])
        self.assertNotIn("reasoningEfforts", models["glm-future-model"])

        byok.apply_openai_compatible(
            "https://api.example.test/v1",
            "test-key",
            list(models),
            self.factory,
            self.oroio,
            opener=opener_for(payload),
        )
        entries = {
            entry["model"]: entry
            for entry in self.read(self.factory / "settings.json")["customModels"]
        }
        self.assertEqual(entries["openai/gpt-5.5"]["baseModelId"], "gpt-5.5")
        self.assertEqual(entries["gpt-5.6-latest"]["baseModelId"], "gpt-5.6-sol")
        self.assertEqual(entries["xai/grok-4.6-fast"]["baseModelId"], "grok-4.6")
        self.assertNotIn("baseModelId", entries["glm-future-model"])

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

    def test_export_round_trips_official_and_openai_compatible_providers(self):
        byok.apply(
            "glm", "glm-secret", ["glm-5.3", "glm-5.3-flash"], self.factory, self.oroio,
            opener=self.models({"id": "glm-5.3", "display_name": "GLM 5.3"}, {"id": "glm-5.3-flash"}),
        )
        byok.apply_openai_compatible(
            "https://api.example.test/v1", "compat-secret", ["gpt-5.6"], self.factory, self.oroio,
            opener=opener_for({"data": [{"id": "gpt-5.6"}]}),
        )
        export_path = self.factory.parent / "byok-export.json"

        empty_root = self.factory.parent / "empty"
        with self.assertRaises(byok.ByokError) as caught:
            byok.export_config(empty_root / "out.json", empty_root / ".factory", empty_root / ".oroio")
        self.assertEqual(caught.exception.code, "not_configured")
        self.assertFalse((empty_root / "out.json").exists())

        result = byok.export_config(export_path, self.factory, self.oroio)
        endpoint_id, _ = byok._openai_compatible_provider("https://api.example.test/v1")
        self.assertEqual(set(result["providers"]), {"glm", endpoint_id})
        payload = self.read(export_path)
        self.assertEqual(payload["version"], 1)
        glm_entry = next(
            item for item in payload["providers"]["glm"]["models"] if item["model"] == "glm-5.3"
        )
        self.assertEqual(glm_entry["apiKey"], "glm-secret")
        self.assertEqual(glm_entry["baseUrl"], byok.PROVIDERS["glm"]["baseUrl"])
        compat_entry = payload["providers"][endpoint_id]["models"][0]
        self.assertEqual(compat_entry["apiKey"], "compat-secret")

        with self.assertRaises(byok.ByokError) as caught:
            byok.export_config(export_path, self.factory, self.oroio)
        self.assertEqual(caught.exception.code, "path_exists")
        byok.export_config(export_path, self.factory, self.oroio, force=True)
        if os.name != "nt":
            self.assertEqual(export_path.stat().st_mode & 0o777, 0o600)

        fresh_factory = self.factory.parent / "home2" / ".factory"
        fresh_oroio = self.factory.parent / "home2" / ".oroio"
        self.write(fresh_factory / "settings.json", {
            "theme": "dark",
            "customModels": [{"model": "manual", "baseUrl": "https://example.test", "apiKey": "keep", "provider": "openai"}],
        })
        imported = byok.import_config(export_path, fresh_factory, fresh_oroio)
        self.assertEqual(imported["importedProviders"], ["glm", endpoint_id])
        self.assertEqual(imported["skippedProviders"], [])
        settings = self.read(fresh_factory / "settings.json")
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(
            [row["model"] for row in settings["customModels"]],
            ["manual", "glm-5.3", "glm-5.3-flash", "gpt-5.6"],
        )
        glm_row = next(row for row in settings["customModels"] if row["model"] == "glm-5.3")
        self.assertEqual(glm_row["apiKey"], "glm-secret")
        self.assertEqual(glm_row["provider"], "anthropic")
        self.assertEqual(glm_row["authMode"], "bearer")
        self.assertEqual(glm_row["baseModelId"], "glm-5.3")
        self.assertEqual(byok._saved_key("glm", fresh_factory, fresh_oroio), "glm-secret")
        rows = {row["id"]: row for row in byok.list_providers(fresh_factory, fresh_oroio)}
        self.assertTrue(rows["glm"]["configured"])
        self.assertNotIn("glm-secret", (fresh_oroio / "byok.json").read_text())
        self.assertNotIn("compat-secret", (fresh_oroio / "byok.json").read_text())

        again = byok.import_config(export_path, fresh_factory, fresh_oroio)
        self.assertEqual(again["importedProviders"], [])
        self.assertEqual(set(again["skippedProviders"]), {"glm", endpoint_id})
        forced = byok.import_config(export_path, fresh_factory, fresh_oroio, force=True)
        self.assertEqual(set(forced["importedProviders"]), {"glm", endpoint_id})

    def test_import_skips_or_replaces_configured_provider(self):
        byok.apply("kimi", "key-a", ["kimi-code"], self.factory, self.oroio, opener=self.models({"id": "kimi-code"}))
        export_path = self.factory.parent / "kimi-export.json"
        self.write(export_path, {
            "version": 1,
            "exportedAt": "2026-09-28T00:00:00Z",
            "providers": {"kimi": {
                "name": "Kimi Code Plan",
                "baseUrl": byok.PROVIDERS["kimi"]["baseUrl"],
                "managedModelIds": ["kimi-code"],
                "models": [{
                    "model": "kimi-code",
                    "displayName": "Kimi Code",
                    "baseUrl": byok.PROVIDERS["kimi"]["baseUrl"],
                    "apiKey": "key-b",
                    "provider": "generic-chat-completion-api",
                    "suspicious": "drop-me",
                }],
            }},
        })

        skipped = byok.import_config(export_path, self.factory, self.oroio)
        self.assertEqual(skipped["importedProviders"], [])
        self.assertEqual(skipped["skippedProviders"], ["kimi"])
        entry = self.read(self.factory / "settings.json")["customModels"][0]
        self.assertEqual(entry["apiKey"], "key-a")

        replaced = byok.import_config(export_path, self.factory, self.oroio, force=True)
        self.assertEqual(replaced["importedProviders"], ["kimi"])
        settings = self.read(self.factory / "settings.json")
        self.assertEqual(len(settings["customModels"]), 1)
        entry = settings["customModels"][0]
        self.assertEqual(entry["apiKey"], "key-b")
        self.assertNotIn("suspicious", entry)
        self.assertEqual(byok._saved_key("kimi", self.factory, self.oroio), "key-b")

    def test_import_rejects_invalid_files_without_writing(self):
        good_model = {
            "model": "kimi-code",
            "baseUrl": byok.PROVIDERS["kimi"]["baseUrl"],
            "apiKey": "key",
            "provider": "generic-chat-completion-api",
        }

        def file_with(providers, version=1):
            path = self.factory.parent / "import.json"
            path.write_text(json.dumps({"version": version, "exportedAt": "now", "providers": providers}))
            return path

        endpoint_id, _ = byok._openai_compatible_provider("https://api.example.test/v1")
        cases = [
            ({"openrouter": {"baseUrl": "https://openrouter.test/v1", "models": [dict(good_model)]}}, 1),
            ({"glm": {"baseUrl": "https://evil.test", "models": [dict(good_model)]}}, 1),
            ({"openai-compatible:0000000000000000": {"baseUrl": "https://api.example.test/v1", "models": [dict(good_model)]}}, 1),
            ({"kimi": {"baseUrl": byok.PROVIDERS["kimi"]["baseUrl"], "models": []}}, 1),
            ({"kimi": {"baseUrl": byok.PROVIDERS["kimi"]["baseUrl"], "models": [{"model": "kimi-code"}]}}, 1),
            ({"kimi": {"baseUrl": byok.PROVIDERS["kimi"]["baseUrl"], "models": [good_model]}}, 2),
        ]
        for providers, version in cases:
            with self.subTest(providers=providers), self.assertRaises(byok.ByokError) as caught:
                byok.import_config(file_with(providers, version), self.factory, self.oroio)
            self.assertEqual(caught.exception.code, "invalid_import")
            self.assertFalse((self.factory / "settings.json").exists())
            self.assertFalse((self.oroio / "byok.json").exists())

        bad_json = self.factory.parent / "bad.json"
        bad_json.write_text("not-json")
        with self.assertRaises(byok.ByokError) as caught:
            byok.import_config(bad_json, self.factory, self.oroio)
        self.assertEqual(caught.exception.code, "invalid_import")
        with self.assertRaises(byok.ByokError) as caught:
            byok.import_config(self.factory.parent / "missing.json", self.factory, self.oroio)
        self.assertEqual(caught.exception.code, "not_found")

    def test_export_converts_legacy_entries_and_derives_endpoint_base_url(self):
        endpoint_id, _ = byok._openai_compatible_provider("https://relay.example.test/v1")
        self.write(self.factory / "settings.json", {"customModels": [{
            "model": "oc-model",
            "baseUrl": "https://relay.example.test/v1",
            "apiKey": "relay-key",
            "provider": "generic-chat-completion-api",
        }]})
        self.write(self.factory / "config.json", {"custom_models": [{
            "model": "kimi-old",
            "model_display_name": "Old Kimi",
            "base_url": byok.PROVIDERS["kimi"]["baseUrl"],
            "api_key": "legacy-key",
            "provider": "generic-chat-completion-api",
        }]})
        self.write(self.oroio / "byok.json", {"version": 1, "providers": {
            "kimi": {
                "managedModels": ["kimi-old"],
                "locations": {"kimi-old": "legacy"},
                "knownModels": [{"id": "kimi-old", "displayName": "Old Kimi"}],
                "unavailable": [],
            },
            endpoint_id: {
                "managedModels": ["oc-model"],
                "locations": {"oc-model": "settings"},
                "knownModels": [{"id": "oc-model"}],
                "unavailable": [],
            },
        }})
        export_path = self.factory.parent / "legacy-export.json"
        result = byok.export_config(export_path, self.factory, self.oroio)
        self.assertEqual(set(result["providers"]), {"kimi", endpoint_id})
        kimi_model = result["providers"]["kimi"]["models"][0]
        self.assertEqual(kimi_model["model"], "kimi-old")
        self.assertEqual(kimi_model["displayName"], "Old Kimi")
        self.assertEqual(kimi_model["apiKey"], "legacy-key")
        self.assertEqual(kimi_model["baseUrl"], byok.PROVIDERS["kimi"]["baseUrl"])
        self.assertEqual(result["providers"][endpoint_id]["baseUrl"], "https://relay.example.test/v1")

        fresh_factory = self.factory.parent / "home3" / ".factory"
        fresh_oroio = self.factory.parent / "home3" / ".oroio"
        imported = byok.import_config(export_path, fresh_factory, fresh_oroio)
        self.assertEqual(set(imported["importedProviders"]), {"kimi", endpoint_id})
        rows = {row["model"]: row for row in self.read(fresh_factory / "settings.json")["customModels"]}
        self.assertEqual(rows["kimi-old"]["apiKey"], "legacy-key")
        self.assertEqual(rows["kimi-old"]["displayName"], "Old Kimi")
        self.assertEqual(rows["oc-model"]["baseUrl"], "https://relay.example.test/v1")

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

    def test_cli_kimi_setup_prefers_environment_key_without_printing_it(self):
        secret = "kimi-environment-secret"
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        discovered = {
            "success": True,
            "provider": "kimi",
            "configured": False,
            "models": [{"id": "kimi-code", "displayName": "Kimi Code", "selected": True}],
        }
        output = io.StringIO()
        with mock.patch.object(sys, "stdin", fake_stdin), mock.patch.object(sys, "stdout", output), \
             mock.patch.dict(os.environ, {"KIMI_CODING_API_KEY": secret}), \
             mock.patch("byok.getpass.getpass") as getpass_mock, \
             mock.patch("byok.list_providers", return_value=[{"id": "kimi", "configured": False}]), \
             mock.patch("byok.discover", return_value=discovered) as discover_mock, \
             mock.patch("byok.apply") as apply_mock, mock.patch("builtins.input", return_value=""):
            result = byok._cli_setup("kimi")
        self.assertEqual(result, 0)
        getpass_mock.assert_not_called()
        discover_mock.assert_called_once_with("kimi", secret)
        apply_mock.assert_called_once_with("kimi", secret, ["kimi-code"])
        self.assertIn("KIMI_CODING_API_KEY", output.getvalue())
        self.assertNotIn(secret, output.getvalue())

    def test_cli_kimi_setup_prompts_when_environment_key_is_blank(self):
        secret = "prompted-kimi-secret"
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        discovered = {
            "success": True,
            "provider": "kimi",
            "configured": False,
            "models": [{"id": "kimi-code", "displayName": "Kimi Code", "selected": True}],
        }
        with mock.patch.object(sys, "stdin", fake_stdin), mock.patch.object(sys, "stdout", io.StringIO()), \
             mock.patch.dict(os.environ, {"KIMI_CODING_API_KEY": "   "}), \
             mock.patch("byok.getpass.getpass", return_value=secret) as getpass_mock, \
             mock.patch("byok.list_providers", return_value=[{"id": "kimi", "configured": False}]), \
             mock.patch("byok.discover", return_value=discovered), mock.patch("byok.apply") as apply_mock, \
             mock.patch("builtins.input", return_value=""):
            result = byok._cli_setup("kimi")
        self.assertEqual(result, 0)
        getpass_mock.assert_called_once()
        apply_mock.assert_called_once_with("kimi", secret, ["kimi-code"])

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

    def test_cli_export_and_import(self):
        export_result = {
            "success": True,
            "path": "x.json",
            "providers": {
                "glm": {"models": [{}, {}], "unavailableModelIds": []},
                "kimi": {"models": [{}], "unavailableModelIds": ["kimi-gone"]},
            },
        }
        output = io.StringIO()
        with mock.patch.object(sys, "stdout", output), mock.patch("byok.export_config", return_value=export_result) as export_mock:
            self.assertEqual(byok._cli_export("x.json", False), 0)
        export_mock.assert_called_once_with("x.json", force=False)
        self.assertIn("已导出 2 个平台配置", output.getvalue())
        self.assertIn("glm: 2 个模型", output.getvalue())
        self.assertIn("kimi: 1 个模型；不可用: 1", output.getvalue())
        self.assertIn("明文 API Key", output.getvalue())

        import_result = {"success": True, "importedProviders": ["glm"], "skippedProviders": ["kimi"]}
        output = io.StringIO()
        with mock.patch.object(sys, "stdout", output), mock.patch("byok.import_config", return_value=import_result) as import_mock:
            self.assertEqual(byok._cli_import("x.json", True), 0)
        import_mock.assert_called_once_with("x.json", force=True)
        self.assertIn("已导入 glm", output.getvalue())
        self.assertIn("跳过 kimi", output.getvalue())

        with self.assertRaises(byok.ByokError):
            byok._cli_export(None, False)
        with self.assertRaises(byok.ByokError):
            byok._cli_import(None, False)
        with self.assertRaises(byok.ByokError) as caught:
            byok.cli_main(["import", str(Path(self.temp.name) / "nope.json")])
        self.assertEqual(caught.exception.code, "not_found")


if __name__ == "__main__":
    unittest.main()
