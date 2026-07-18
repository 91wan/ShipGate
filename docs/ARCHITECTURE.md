# Architecture

## Goals

ShipGate is a read-only publication gate. It establishes a declared publication
surface, runs deterministic checks over one inventory, and emits auditable,
path-safe reports. Unknown or incomplete required checks block public work.

Runtime code supports Python 3.11+ and uses only the standard library.

## Module Boundaries

- `model.py`: enums, dataclasses, overall policy and schema serialization.
- `inventory.py`: non-Git filesystem inventory and stable file streaming.
- `git_surface.py`: read-only Git metadata, working/index/history inventory and
  blob streaming. All subprocess calls use argument arrays and bounded timeouts.
- `checks/redaction.py`: configured high-risk indicator rules over inventory
  streams. It never traverses the project independently.
- `checks/readme.py`: exact split-page and reciprocal top-link structure.
- `checks/project.py`: strict skill metadata and macOS platform evidence.
- `checks/assets.py`: operation-aware regular-file checks and stable SHA-256.
- `engine.py`: fixed gate ordering and overall policy.
- `reporting.py`: deterministic JSON/Markdown rendering and atomic writes.
- `cli.py`: argument parsing, cross-argument validation and exit codes.
- `scripts/shipgate.py`: compatibility import/CLI adapter only.

## Operation and Source

`local` defaults to `working-tree`. In a Git repository, that means tracked plus
untracked non-ignored files. Outside Git, it means the filesystem tree excluding
only `.git` internals and exact report output paths.

`public-push` uses the Git working candidate plus all reachable history. `tag`
and `release` bind to clean `HEAD` or an explicit `git-ref`. Blob content is
deduplicated by object ID. Publication metadata is inventoried separately:
working/index paths, every changed path in the commit walk, paths under
non-commit tree refs, ref names, commit/tag messages, tag names, and typed Git
identity fields. `index`, `head`, `git-ref` and `history-all` are explicit source
models; invalid operation/source combinations are CLI usage errors.

Raw commit and annotated-tag header blocks are scanned in addition to typed
identity fields. Unknown headers and continuation lines therefore remain in the
redaction surface instead of being silently ignored. Filename policies run on
every inventoried path, including historical names that no longer label a
deduplicated blob.

History blobs and commit/tag objects are read through `git cat-file --batch`;
historical paths use one `git diff-tree --stdin --root -r -m --no-renames`
invocation so renamed-away and merge-introduced paths remain visible. No
checkout or remote write is performed.

## Status Policy

Gate statuses are `pass`, `fail`, `warning`, `not-applicable`, and `error`.
Any `fail` or `error` makes overall status `fail`. `not-applicable` means the
selected operation genuinely does not require that gate; it never means the
checker could not run.

Exit code `1` represents policy failure. Exit code `3` represents an `error`
that prevented trustworthy completion. Both stop publication.

## Trade-offs

ShipGate intentionally validates strict subsets instead of adding runtime
parsers. README validation proves navigation structure, not prose quality.
Skill frontmatter accepts a documented single-line subset, not arbitrary YAML.
SwiftPM and Xcode checks require positive macOS evidence and may reject unusual
projects until their evidence model is extended with tests.

The scanner detects configured high-risk indicators. It cannot prove that no
secret exists. Its trust claim is limited to the declared inventory, configured
rules and reported errors/exclusions.

Streaming scan windows carry the number of newlines preceding the window. The
counter excludes repeated overlap bytes or decoded characters, so findings in
large UTF-8, binary, and UTF-16 files retain absolute one-based line numbers.

## Official Baseline

Codex skill scope, `SKILL.md`, optional `agents/openai.yaml`, and `AGENTS.md`
behavior were checked against OpenAI's Build skills and AGENTS.md documentation
on 2026-07-12. Repository and user `.agents/skills` locations are preferred;
`CODEX_HOME/skills` is an explicit compatibility installation mode.
