# oroio

**Lightweight CLI for managing Factory Droid API keys with auto-rotation.**

[中文](README.zh-CN.md)

## What is dk?

dk manages multiple Factory Droid API keys in one place. It tracks usage limits and expiration dates, and automatically rotates to the next available key when one runs out—so your AI coding sessions never get interrupted.

### Perfect For

- **Heavy Droid Users** — Manage multiple API keys without manual switching
- **Team Environments** — Share key pools across machines
- **Uninterrupted Workflows** — Auto-rotation keeps sessions running

## Quick Start

### Install

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/gaoxiang89/oroio/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/gaoxiang89/oroio/main/install.ps1 | iex
```

The installer adds a `droid` function to your shell. Restart your terminal, then just run `droid`.

### Basic Workflow

```bash
# 1. Add your API keys
dk add fk-xxxx fk-yyyy fk-zzzz

# Or import from a plaintext file (one key per line)
dk import keys.txt

# Export all keys to a plaintext file
dk export keys-backup.txt

# 2. Check usage and expiration
dk list

# 3. Run droid (auto-injects key, auto-rotates on exhaustion)
droid
```

Here's what `dk list` looks like:

![CLI](assets/imgs/cli.png)

## Commands

| Command                | Description                                 |
| ---------------------- | ------------------------------------------- |
| `dk add <key...>`      | Add one or more API keys                    |
| `dk add --file <path>` | Import keys from file                       |
| `dk import <path>`     | Import keys from plaintext (deduplicated)   |
| `dk export <path>`     | Export all keys to a plaintext file         |
| `dk export --force <path>` | Overwrite a file and export all keys    |
| `dk list`              | Show all keys with usage/expiration         |
| `dk current`           | Display current key and copy export command |
| `dk use <n>`           | Switch to key by index                      |
| `dk rm <n...>`         | Remove keys by index                        |
| `dk run <cmd>`         | Run command with current key (auto-rotates) |
| `dk serve`             | Start web dashboard on an available port    |
| `dk byok setup [provider]` | Configure GLM, DeepSeek, or Kimi interactively |
| `dk byok list`         | List official BYOK provider configurations  |
| `dk byok refresh [provider]` | Refresh models using the saved key      |
| `dk byok remove <provider>` | Remove models managed for one provider  |
| `dk config`            | Configure CLI options (border style, etc.)  |
| `dk reinstall`         | Update to latest version                    |
| `dk uninstall`         | Remove dk                                   |

> **Security warning**: Exported files contain plaintext API keys. Store them securely, never commit them to Git, and delete them after importing. On macOS/Linux, exported files are automatically set to mode `0600`.

## Official provider BYOK

oroio provides guided setup for three official endpoints:

| Shortcut | Model discovery | Droid runtime |
| --- | --- | --- |
| GLM Coding Plan | `open.bigmodel.cn/api/coding/paas/v4/models` | Anthropic-compatible GLM endpoint |
| DeepSeek API | `api.deepseek.com/models` | Official Anthropic-compatible endpoint |
| Kimi Code Plan | `api.kimi.com/coding/v1/models` | OpenAI Chat Completions-compatible endpoint |

Run the hidden-input setup wizard, then select models by number or range:

```bash
dk byok setup glm
dk byok setup deepseek
dk byok setup kimi
dk byok list
dk byok refresh glm
dk byok remove glm
```

The same guided cards are available on the Web dashboard and in the desktop app. Recognized GLM, DeepSeek, and Kimi models are linked to Droid's built-in model profiles, so `/model` offers their supported reasoning levels after setup. Setup validates the key against the provider's official model endpoint before changing any local config. A refresh keeps currently managed models selected, highlights newly discovered models without selecting them, and retains upstream models marked unavailable until you explicitly deselect them or remove the provider.

The BYOK page also includes an **OpenAI-compatible** guided setup. Enter a Base URL and API key; oroio requests `<Base URL>/models`, lets you select the returned models, and writes them with Droid's `generic-chat-completion-api` provider. Recognized OpenAI, Grok 4.6, GLM, DeepSeek, and Kimi model IDs are linked through Droid's `baseModelId`, enabling the same model-then-reasoning-level selection used by built-in models. Local HTTP endpoints are supported, while redirects and URLs containing embedded credentials, query parameters, or fragments are rejected to reduce credential leakage risk.

New entries are written to Droid's current `~/.factory/settings.json` `customModels` format. Existing legacy entries in `~/.factory/config.json` remain in that file when edited. oroio preserves unrelated settings and records only management metadata in `~/.oroio/byok.json`; it does not duplicate the API key there.

> **Local key warning:** Droid needs the provider key at runtime, so these BYOK keys are stored as plaintext in your local Factory settings. The files are set to `0600` on POSIX systems, but anyone or any process that can read your account files can read the keys. Never commit either Factory settings file.

## Web Dashboard

```bash
dk serve        # Start dashboard
dk serve stop   # Stop dashboard
dk serve status # Check if running
```

Open the address printed by `dk serve` to view and manage keys visually. By default,
the system allocates an available port; it may change after a restart. Use
`dk serve status` to retrieve the running service's address, even from a new terminal.

To use a fixed port, set `DKM_SERVE_PORT` before starting the service:

```bash
DKM_SERVE_PORT=7758 dk serve  # Linux / macOS / WSL
```

```powershell
$env:DKM_SERVE_PORT = '7758'  # Windows PowerShell
dk serve
```

Set it to `0` (or unset it) to restore automatic allocation. An occupied fixed port
causes startup to fail; it does not silently switch ports. Restart a running service
with `dk serve stop` followed by `dk serve` to apply a changed setting.

![Web Dashboard](assets/imgs/web-dashboard.png)

## Desktop App (Optional)

A standalone desktop app is available for macOS, Windows, and Linux. It provides the same dashboard experience with system tray integration and low-balance notifications.

Download from [Releases](https://github.com/gaoxiang89/oroio/releases/tag/electron-dist).

> **macOS**: After installing, run `xattr -cr /Applications/oroio.app` to bypass Gatekeeper (app is unsigned).
>
> **Note**: The desktop app works standalone for key management. To use `droid` in terminal, install the CLI separately.

![alt text](assets/imgs/desktop.png)

## Installation Details

### What Gets Installed

**macOS / Linux:**
- Binary: `~/.local/bin/dk`
- BYOK helper: `~/.local/bin/byok.py`
- Data: `~/.oroio/`
- Shell alias: `droid` → `dk run droid`

**Windows:**
- Script: `%LOCALAPPDATA%\oroio\bin\dk.ps1`
- BYOK helper: `%LOCALAPPDATA%\oroio\bin\byok.py`
- Data: `%USERPROFILE%\.oroio\`
- PowerShell function: `droid` → `dk run droid`

### Updating

```bash
dk reinstall
```

Or manually:
```bash
# macOS/Linux
curl -fsSL https://raw.githubusercontent.com/gaoxiang89/oroio/main/reinstall.sh | bash
```
```powershell
# Windows
irm https://raw.githubusercontent.com/gaoxiang89/oroio/main/reinstall.ps1 | iex
```

### Uninstalling

```bash
dk uninstall
```

Or manually:
```bash
# macOS/Linux
curl -fsSL https://raw.githubusercontent.com/gaoxiang89/oroio/main/uninstall.sh | bash
```
```powershell
# Windows
irm https://raw.githubusercontent.com/gaoxiang89/oroio/main/uninstall.ps1 | iex
```

---

**Stop juggling API keys. Start shipping code.**
