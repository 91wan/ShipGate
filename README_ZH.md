# ShipGate

[English](README.md) | 中文

ShipGate 是面向 macOS app 和 Codex skill 的只读、fail-closed 公开发布门禁。它会对真实的本地或 Git 发布面建立清单，扫描已配置的高风险指标，验证分页 README 导航和项目证据，校验 release 资产，并输出路径安全的 Markdown/JSON 报告。ShipGate 不会执行 push、tag、创建 release、上传资产或修改认证配置。

运行时依赖：仅 Python 3.11+ 标准库。

## 公开 README 标准

任何使用 ShipGate 进行公开 push、tag 或 release 的项目，都必须在仓库根目录使用以下精确文件名：

- `README.md`：英文默认页，前 10 个正文行内必须有指向 `README_ZH.md` 的真实 Markdown 链接。
- `README_ZH.md`：中文页，前 10 个正文行内必须有回到 `README.md` 的真实 Markdown 链接。

别名文件、仅文本提及文件名、外链、锚点、路径穿越或单文件中英文混排均不能通过。ShipGate 只证明导航结构，不声称能够判断翻译质量。

## 安装

克隆或下载本仓库后，显式选择安装 scope。

仓库级 scope，推荐团队复现使用：

```bash
python3 scripts/install_skill.py --scope repo --repo <目标仓库>
```

用户级 scope，可跨仓库使用：

```bash
python3 scripts/install_skill.py --scope user
```

旧 `CODEX_HOME` 兼容 scope：

```bash
python3 scripts/install_skill.py --scope codex-home
```

Claude Code 用户级 scope，可跨仓库使用：

```bash
python3 scripts/install_skill.py --scope claude-user
```

Claude Code 仓库级 scope，推荐受团队版本控制的项目使用：

```bash
python3 scripts/install_skill.py --scope claude-repo --repo <目标仓库>
```

只预览、不写入：

```bash
python3 scripts/install_skill.py --scope user --dry-run
```

安装器先构建有边界的 runtime staging，再原子替换经过验证的 `shipgate` 目标。已有目标来源不明时默认拒绝；只有显式 `--force` 才允许替换，同时仍拒绝范围过大或包含 symlink 的目标。

Codex skill 路径和 `AGENTS.md` 行为已于 2026-07-12 对照
[OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills) 和
[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md) 官方文档核验。

## 多端使用

### Codex

安装到 repo 或 user scope；如果 skill 列表未刷新，重启 Codex。之后调用 `$shipgate`。Codex 会加载 `SKILL.md`，并从已安装 skill 目录运行 bundled checker。

### OpenClaw

使用仓库级 `AGENTS.md` 作为操作适配器。它要求使用同一 CLI、检查报告，并把所有非零退出码作为停止条件。

### Claude Code

个人使用时安装到 `--scope claude-user`，单仓库使用时安装到
`--scope claude-repo`。如果目录是在 Claude Code session 启动后才创建，需重启
Claude Code，然后调用 `/shipgate` 或让 Claude 自动加载。`CLAUDE.md` 仍作为仓库级
发布适配器；Claude Code 与 Codex 安装使用的是同一个 checker。

Claude Code skill 路径已于 2026-07-18 对照 Anthropic
[官方 skills 文档](https://code.claude.com/docs/en/slash-commands)核验。

### 直接 CLI

兼容入口继续可用：

```bash
python3 scripts/shipgate.py check <项目> --project-type codex-skill
```

安装 package 后也可使用：

```bash
python3 -m shipgate --version
python3 -m shipgate check <项目> --operation local
```

## Operation 语义

| Operation | 发布面 | 资产策略 |
| --- | --- | --- |
| `local` | Git tracked + untracked non-ignored 工作文件；非 Git 项目扫描 filesystem tree | 未传资产时为 `not-applicable` |
| `public-push` | Git 工作候选 + 全部可达历史 | 通常为 `not-applicable` |
| `tag` | clean `HEAD` 或显式 `git-ref`，包含可达历史 | 资产可选 |
| `release` | clean `HEAD` 或显式 `git-ref`，包含可达历史 | 默认至少一个资产；只有显式 `--source-only` 才允许无资产 |

公开 operation 必须在 Git 仓库中运行。浅克隆、未验证 submodule、ref 缺失或 Git 读取失败都会阻断；`tag` 和 `release` 还要求工作树 clean。

示例：

```bash
python3 scripts/shipgate.py check . \
  --operation public-push \
  --project-type codex-skill \
  --report-md build/shipgate/public-push.md \
  --report-json build/shipgate/public-push.json

python3 scripts/shipgate.py check . \
  --operation release \
  --project-type macos-app \
  --asset dist/App.dmg \
  --asset dist/App.zip

python3 scripts/shipgate.py check . \
  --operation release \
  --project-type codex-skill \
  --source-only
```

## 项目证据

- `codex-skill`：`SKILL.md` 必须满足 ShipGate 文档化的严格 frontmatter 子集；如果存在 `agents/openai.yaml`，其必要 interface metadata 必须可读且完整。
- `macos-app`：Xcode project data 必须包含 macOS 平台证据，或 `Package.swift` 必须在真实 `platforms` 参数中声明 macOS。

自动检测会返回 candidates 和 evidence。候选为 0 或超过 1 个时失败；显式传入项目类型不能绕过证据不足。

## 门禁与报告

所有检查共用一份 inventory。ShipGate 不会静默跳过 `.github`、大文件、UTF-16、binary 中的 ASCII 指标、broken link、特殊文件或不可读的发布条目。Finding 只输出稳定 code、相对路径、可用时的行号和安全 fingerprint，不输出完整凭据匹配值。

Unix home path 检测仅有一条有界 fixture 例外：只有位于 `tests` 或 `*Tests`
目录中的 `.py` 或 `.swift` 文件，才允许使用 synthetic 用户名 `alice` 和
`example`。这些名称出现在 test source 之外，或任何其他用户名出现在 test
source 内，仍然会阻断。

环境文件名采用 fail-closed 策略。发布面中名为 `.env` 或以 `.env.` 开头的文件
都会被阻断，包括 `.env.example`，且不设 filename allowlist。经过完整脱敏、需要
公开的配置模板应改名为 `env.example`。被 Git ignore 且从未 tracked 的本地
`.env` 不属于 working surface；一旦 `.env` 类路径已出现在所选 publication
inventory 中，Git ignore 不会为其提供豁免。

报告包含 schema/tool 版本、operation、项目 evidence、source commit 和 Git 状态、inventory 数量/错误/排除项、gates、assets 与 recommendations。项目根固定表示为 `.`；报告原子写入且结果稳定。

退出码：

- `0`：全部适用 gate 通过；可能仍有非阻断 warning。
- `1`：policy 或 gate 失败。
- `2`：CLI 用法或参数组合错误。
- `3`：因 I/O 或执行环境错误，无法完成可信检查。

公开发布流程必须把任何非零退出码视为硬停止。

## 开发

在隔离环境安装可选开发工具：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
make validate PYTHON=.venv/bin/python
```

`make validate` 会运行 compile、Ruff lint/format、mypy、unit/integration tests、line/branch coverage threshold 和 ShipGate local self-check。外部官方 skill validator 明确作为独立检查：

```bash
make official-skill-validate \
  QUICK_VALIDATE=<外部-quick_validate.py-路径> \
  PYTHON=.venv/bin/python
```

参阅 [Architecture](docs/ARCHITECTURE.md)、
[Threat model](docs/THREAT_MODEL.md)、
[Report schema](docs/REPORT_SCHEMA.md) 和
[Code review](docs/CODE_REVIEW.md)。

## 发布边界

ShipGate 通过后，人员或宿主 agent 可以在门禁之外单独核验 GitHub auth/remote，再执行 push、创建 annotated tag、创建 release、上传已检查资产，并比较下载资产的 SHA-256。ShipGate 故意不包含这些写操作。
