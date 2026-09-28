# oroio

**轻量级 CLI，用于管理 Factory Droid API 密钥，支持自动轮换。**

[English](README.md)

## dk 是什么？

dk 集中管理多个 Factory Droid API 密钥，实时追踪用量和到期时间。当一个密钥额度耗尽时自动切换到下一个可用密钥，让你的 AI 编程会话不中断。

### 适用场景

- **重度 Droid 用户** — 管理多个 API 密钥，无需手动切换
- **团队环境** — 跨设备共享密钥池
- **不间断工作流** — 自动轮换保持会话持续运行

## 快速开始

### 安装

**macOS / Linux：**

```bash
curl -fsSL https://raw.githubusercontent.com/gaoxiang89/oroio/main/install.sh | bash
```

**Windows (PowerShell)：**

```powershell
irm https://raw.githubusercontent.com/gaoxiang89/oroio/main/install.ps1 | iex
```

安装程序会在 shell 中添加 `droid` 函数。重启终端后直接运行 `droid` 即可。

### 基本用法

```bash
# 1. 添加 API 密钥
dk add fk-xxxx fk-yyyy fk-zzzz

# 或从明文文件导入（每行一个密钥）
dk import keys.txt

# 将全部密钥导出到明文文件
dk export keys-backup.txt

# 2. 查看用量和到期时间
dk list

# 3. 运行 droid（自动注入密钥，额度耗尽时自动轮换）
droid
```

`dk list` 效果如下：

![CLI](assets/imgs/cli.png)

## 命令

| 命令                   | 说明                             |
| ---------------------- | -------------------------------- |
| `dk add <key...>`      | 添加一个或多个 API 密钥          |
| `dk add --file <路径>` | 从文件导入密钥                   |
| `dk import <路径>`     | 从明文文件导入密钥（自动去重）   |
| `dk export <路径>`     | 将全部密钥导出到明文文件         |
| `dk export --force <路径>` | 覆盖已有文件并导出全部密钥   |
| `dk list`              | 显示所有密钥的用量和到期时间     |
| `dk current`           | 显示当前密钥并复制 export 命令   |
| `dk use <序号>`        | 按序号切换密钥                   |
| `dk rm <序号...>`      | 按序号删除密钥                   |
| `dk run <命令>`        | 使用当前密钥运行命令（自动轮换） |
| `dk serve`             | 启动 Web 控制台（端口 7758）     |
| `dk byok setup [平台]` | 交互配置 GLM、DeepSeek 或 Kimi   |
| `dk byok list`         | 查看官方 BYOK 平台配置           |
| `dk byok refresh [平台]` | 使用已保存 Key 刷新模型        |
| `dk byok remove <平台>` | 删除指定平台管理的模型          |
| `dk config`            | 配置 CLI 选项（边框样式等）      |
| `dk reinstall`         | 更新到最新版本                   |
| `dk uninstall`         | 卸载 dk                          |

> **安全提示**：导出的文件包含明文 API Key。请存放在安全位置，不要提交到 Git，并在导入完成后及时删除。macOS/Linux 下导出文件的权限会自动设为 `0600`。

## 官方平台一键 BYOK

oroio 首批提供三个官方接口的交互式配置：

| 快捷入口 | 模型发现接口 | Droid 运行接口 |
| --- | --- | --- |
| GLM Coding Plan | `open.bigmodel.cn/api/coding/paas/v4/models` | GLM 官方 Anthropic 兼容接口 |
| DeepSeek 官方 API | `api.deepseek.com/models` | DeepSeek 官方 Anthropic 兼容接口 |
| Kimi Code Plan | `api.kimi.com/coding/v1/models` | OpenAI Chat Completions 兼容接口 |

CLI 会隐藏 API Key 输入，并支持用序号和范围多选模型：

```bash
dk byok setup glm
dk byok setup deepseek
dk byok setup kimi
dk byok list
dk byok refresh glm
dk byok remove glm
```

Web 控制台和桌面端也提供相同的三张快捷配置卡片。识别到 GLM、DeepSeek 或 Kimi 模型后，会关联 Droid 内置模型能力，配置完成后可在 `/model` 中继续选择该模型支持的思考等级。配置前会直接调用平台官方模型接口验证 Key；验证或刷新失败时不会修改已有配置。刷新时，已有模型默认保持选中，新发现模型会突出显示但不自动选中；上游暂时消失的模型会标记为 unavailable 并继续保留，只有你明确取消勾选或删除平台后才会移除。

BYOK 页面还提供 **OpenAI-compatible** 引导配置。输入 Base URL 和 API Key 后，oroio 会请求 `<Base URL>/models`，让你勾选返回的模型。识别到 OpenAI、Grok 4.6、GLM、DeepSeek 或 Kimi 模型 ID 时，会通过 Droid 的 `baseModelId` 关联内置模型能力，从而获得与官方模型相同的“先选模型、再选思考等级”流程。支持本地 HTTP 端点；为降低 Key 泄露风险，会拒绝重定向以及包含内嵌凭据、查询参数或 fragment 的 URL。

新配置使用 Droid 当前的 `~/.factory/settings.json` / `customModels` 格式。已有的 `~/.factory/config.json` 旧格式条目仍在原文件中编辑，不会被自动迁移。oroio 会在 `~/.oroio/byok.json` 保存平台管理状态，但不会在其中重复保存 API Key，且会保留 Factory 配置里的其他未知字段。

> **本地明文 Key 风险**：Droid 运行时需要读取平台 Key，因此这些 BYOK Key 会以明文保存在本机 Factory 配置中。POSIX 系统会将写入文件设为 `0600`，但任何能读取当前用户文件的程序或人员仍可能取得 Key。请勿将 Factory 配置提交到 Git。

## Web 控制台

```bash
dk serve        # 启动控制台
dk serve stop   # 停止控制台
dk serve status # 检查运行状态
```

访问 `http://localhost:7758` 可视化查看和管理密钥。

![Web Dashboard](assets/imgs/web-dashboard.png)

## 桌面应用（可选）

提供适用于 macOS、Windows 和 Linux 的独立桌面应用。具备相同的控制台功能，并支持系统托盘和余额不足提醒。

从 [Releases](https://github.com/gaoxiang89/oroio/releases/tag/electron-dist) 下载。

> **macOS**：安装后运行 `xattr -cr /Applications/oroio.app` 以绕过 Gatekeeper（应用未签名）。
>
> **注意**：桌面应用可独立管理密钥。如需在终端使用 `droid`，请单独安装 CLI。

![alt text](assets/imgs/desktop.png)

## 安装详情

### 安装内容

**macOS / Linux：**
- 可执行文件：`~/.local/bin/dk`
- BYOK 辅助模块：`~/.local/bin/byok.py`
- 数据目录：`~/.oroio/`
- Shell 别名：`droid` → `dk run droid`

**Windows：**
- 脚本：`%LOCALAPPDATA%\oroio\bin\dk.ps1`
- BYOK 辅助模块：`%LOCALAPPDATA%\oroio\bin\byok.py`
- 数据目录：`%USERPROFILE%\.oroio\`
- PowerShell 函数：`droid` → `dk run droid`

### 更新

```bash
dk reinstall
```

或手动执行：
```bash
# macOS/Linux
curl -fsSL https://raw.githubusercontent.com/gaoxiang89/oroio/main/reinstall.sh | bash
```
```powershell
# Windows
irm https://raw.githubusercontent.com/gaoxiang89/oroio/main/reinstall.ps1 | iex
```

### 卸载

```bash
dk uninstall
```

或手动执行：
```bash
# macOS/Linux
curl -fsSL https://raw.githubusercontent.com/gaoxiang89/oroio/main/uninstall.sh | bash
```
```powershell
# Windows
irm https://raw.githubusercontent.com/gaoxiang89/oroio/main/uninstall.ps1 | iex
```

---

**告别密钥切换的烦恼，专注编写代码。**
