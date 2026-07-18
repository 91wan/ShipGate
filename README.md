# ShipGate

English | [中文](README_ZH.md)

ShipGate is a read-only, fail-closed publication gate for macOS apps and Codex
skills. It inventories the actual local or Git publication surface, scans
configured high-risk indicators, validates split language README navigation and
project evidence, verifies release assets, and emits path-safe Markdown and JSON
reports. It never pushes, tags, creates releases, uploads assets, or changes
authentication.

Runtime dependencies: Python 3.11+ standard library only.

## Public README Standard

Every project using ShipGate for a public push, tag, or release must have these
exact root files:

- `README.md`: English default page with a real Markdown link to
  `README_ZH.md` in its first 10 content lines.
- `README_ZH.md`: Chinese page with a real Markdown link back to `README.md` in
  its first 10 content lines.

Aliases, plain-text filename mentions, external links, anchors, path traversal,
and one mixed bilingual page do not satisfy the gate. ShipGate proves this
navigation structure; it does not claim to judge translation quality.

## Install

Clone or download this repository, then choose one explicit scope.

Repository scope, recommended for team reproducibility:

```bash
python3 scripts/install_skill.py --scope repo --repo <target-repository>
```

User scope, available across repositories:

```bash
python3 scripts/install_skill.py --scope user
```

Legacy `CODEX_HOME` compatibility scope:

```bash
python3 scripts/install_skill.py --scope codex-home
```

Claude Code user scope, available across repositories:

```bash
python3 scripts/install_skill.py --scope claude-user
```

Claude Code repository scope, recommended for team-controlled projects:

```bash
python3 scripts/install_skill.py --scope claude-repo --repo <target-repository>
```

Preview any installation without writing:

```bash
python3 scripts/install_skill.py --scope user --dry-run
```

The installer stages a bounded runtime copy and atomically replaces only a
validated `shipgate` target. It refuses unknown existing targets unless
`--force` is explicitly supplied, and still rejects broad or symlinked targets.

Current Codex skill locations and `AGENTS.md` behavior were checked against the
[OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills) and
[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
documentation on 2026-07-12.

## Platform Use

### Codex

Install to repository or user scope, restart Codex if the skill list is stale,
then invoke `$shipgate`. Codex loads `SKILL.md` and runs the bundled checker from
the installed skill directory.

### OpenClaw

Use the repository-level `AGENTS.md` as the operational adapter. It requires the
same CLI, report review, and nonzero-exit stop rules.

### Claude Code

Install with `--scope claude-user` for personal use or `--scope claude-repo`
for one repository. Restart Claude Code if the skill directory was created
while a session was already running, then invoke `/shipgate` or let Claude load
it automatically. `CLAUDE.md` remains the repository-level release adapter; the
installed skill and Codex both run the same checker.

Claude Code skill locations were checked against Anthropic's
[official skills documentation](https://code.claude.com/docs/en/slash-commands)
on 2026-07-18.

### Direct CLI

The compatibility entry point remains available:

```bash
python3 scripts/shipgate.py check <project> --project-type codex-skill
```

Installed package entry points are also available:

```bash
python3 -m shipgate --version
python3 -m shipgate check <project> --operation local
```

## Operations

| Operation | Publication source | Asset policy |
| --- | --- | --- |
| `local` | Git tracked + untracked non-ignored working files, or a non-Git filesystem tree | `not-applicable` when none are supplied |
| `public-push` | Git working candidate plus all reachable history | Assets normally `not-applicable` |
| `tag` | Clean `HEAD` or explicit `git-ref`, including reachable history | Assets optional |
| `release` | Clean `HEAD` or explicit `git-ref`, including reachable history | At least one asset, unless `--source-only` is explicit |

Public operations require a Git repository. Shallow history, unverified
submodules, missing refs, or Git read failures block the operation. `tag` and
`release` also require a clean working tree.

Examples:

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

## Project Evidence

- `codex-skill`: `SKILL.md` must satisfy ShipGate's documented strict
  frontmatter subset. If `agents/openai.yaml` exists, its required interface
  metadata must be readable and complete.
- `macos-app`: Xcode project data must contain macOS platform evidence, or
  `Package.swift` must declare macOS in its actual `platforms` argument.

Auto detection returns candidates and evidence. Zero candidates or multiple
candidates fail; explicitly selecting a type does not bypass missing evidence.

## Gates and Reports

ShipGate uses one immutable inventory for all checks. It does not silently skip
`.github`, large files, UTF-16 text, binary ASCII indicators, broken links,
special files, or unreadable publication entries. Findings expose stable codes,
relative paths, optional line numbers, and safe fingerprints, never full matched
credentials.

Unix home-path detection has one bounded fixture exception: only `.py` or
`.swift` files under a `tests` or `*Tests` directory may use the synthetic
usernames `alice` and `example`. The same names outside test source, and every
other username inside test source, remain blocking.

Environment filenames are intentionally fail-closed. Any publication-surface
file named `.env` or beginning with `.env.` is blocked, including
`.env.example`; there is no filename allowlist. Publish a fully sanitized
template as `env.example` instead. An ignored, untracked local `.env` stays
outside the Git working surface, but any tracked or historically reachable copy
remains blocking.

Reports include schema/tool versions, operation, project evidence, source
commit and Git state, inventory counts/errors/exclusions, gates, assets and
recommendations. Project root is always represented as `.`; report writes are
atomic and deterministic.

Exit codes:

- `0`: all applicable gates passed; warnings may remain.
- `1`: policy or gate failure.
- `2`: invalid CLI usage or parameter combination.
- `3`: trusted checking could not complete because of I/O or execution errors.

Any nonzero code is a hard stop for public release work.

## Development

Install the optional development tools into an isolated environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
make validate PYTHON=.venv/bin/python
```

`make validate` runs compile checks, Ruff lint and format checks, mypy, unit and
integration tests, line/branch coverage thresholds, and ShipGate local
self-check. External official skill validation is deliberately separate:

```bash
make official-skill-validate \
  QUICK_VALIDATE=<path-to-external-quick_validate.py> \
  PYTHON=.venv/bin/python
```

See [Architecture](docs/ARCHITECTURE.md),
[Threat model](docs/THREAT_MODEL.md),
[Report schema](docs/REPORT_SCHEMA.md), and
[Code review](docs/CODE_REVIEW.md).

## Publication Boundary

After ShipGate passes, a person or host agent may separately verify GitHub auth
and remote state, push, create an annotated tag, create a release, upload the
verified assets, and compare downloaded SHA-256 values. Those writes are
intentionally outside ShipGate.
