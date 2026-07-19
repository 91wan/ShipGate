# Report Schema

Schema version: `1.0`.

JSON and Markdown are rendered from the same typed report. JSON keys and lists
use stable ordering, no timestamp is added, and project root is represented as
`.`. Paths use project-relative POSIX labels; external assets are rejected for
public release checks.

## Top-Level Fields

- `schema_version`: report contract version.
- `shipgate_version`: implementation version.
- `status`: `pass` or `fail`.
- `operation`: `local`, `public-push`, `tag`, or `release`.
- `project`: requested/detected type and evidence.
- `source`: source kind, ref/commit and Git dirty/shallow/submodule state.
- `inventory`: considered/scanned file and metadata counts, bytes, exclusions
  and errors.
- `gates`: ordered gate outcomes and findings.
- `assets`: relative label, status, size, SHA-256 and detail.
- `recommendations`: next actions derived from failed gates.

## Gate and Finding

Every gate contains `id`, compatibility `name`, `status`, `detail` and
`findings`. Finding fields are:

```json
{
  "code": "secret.github-token",
  "severity": "error",
  "path": ".github/workflows/release.yml",
  "line": 12,
  "message": "Configured GitHub token indicator found.",
  "fingerprint": "sha256:0123456789abcdef"
}
```

The fingerprint is a truncated hash of rule code and matched value. The matched
value itself is never reported. If a path or ref contains a configured
high-risk indicator, reports use a deterministic scope-and-hash label instead
of the original text. Line numbers are best-effort for streaming windows and
may be null when unavailable.

## Compatibility

The Python `check_project()` compatibility wrapper also exposes top-level
`project_type`. New consumers should use `project.requested_type` and
`project.detected_type`.

Schema changes that remove or reinterpret fields require a schema-version
change and migration notes.
