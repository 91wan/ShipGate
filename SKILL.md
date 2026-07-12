---
name: shipgate
description: Use before public GitHub push, tag, or release for a macOS app or Codex skill; inventories Git publication surfaces and history, scans redaction risks, requires split README.md and README_ZH.md pages, validates project evidence, and verifies release assets without performing publication writes.
---

# ShipGate

Use ShipGate as a read-only, fail-closed release gate. Resolve this loaded
skill's directory and run its bundled `scripts/shipgate.py`; do not assume the
target project's current directory contains ShipGate.

## Choose the Operation

- Development preflight: `--operation local`.
- Before public push: `--operation public-push`.
- Before creating a tag: `--operation tag`.
- Before a release with files: `--operation release --asset <path>` for every
  asset.
- Intentional source-only release: `--operation release --source-only`.

Specify `--project-type codex-skill` or `macos-app` when known. Explicit type
selection still requires structural evidence.

## Run the Gate

```bash
python3 <shipgate-skill-dir>/scripts/shipgate.py check <project> \
  --operation public-push \
  --project-type codex-skill \
  --report-md <project>/build/shipgate/report.md \
  --report-json <project>/build/shipgate/report.json
```

Require exact split language pages before any public operation:

- `README.md` links to `README_ZH.md` near the top.
- `README_ZH.md` links back to `README.md` near the top.

Do not accept a single mixed bilingual README.

## Interpret the Result

Treat every nonzero exit code as a hard stop. Do not look only at overall
status: review `operation`, `source`, commit, dirty/shallow/submodule state,
inventory counts/errors/exclusions, findings, and assets.

Fix inventory and redaction errors first. Unknown, unreadable, unscanned, or
unverified publication content is not a pass. Do not claim absolute
secret-freedom; ShipGate reports whether its configured high-risk indicators
were found in the declared inventory.

## Publication Boundary

Never use this skill to push, tag, create a GitHub release, upload assets, alter
remotes, or modify authentication. After a clean ShipGate report, hand those
separate write steps back to the user or an explicitly authorized workflow.
