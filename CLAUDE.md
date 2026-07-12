# ShipGate Adapter for Claude Code

Use the same read-only ShipGate runtime as Codex. Do not recreate checks in this
file or infer release readiness from documentation alone.

Before public work, run the repository's bundled checker with the matching
operation and project type:

```bash
python3 scripts/shipgate.py check <project> \
  --operation public-push \
  --project-type codex-skill \
  --report-md <project>/build/shipgate/report.md \
  --report-json <project>/build/shipgate/report.json
```

For release assets, use `--operation release` and repeat `--asset`; use
`--source-only` only for an intentional source-only release.

Any nonzero exit is a hard stop. Review source, inventory, findings and assets,
not only overall status. Require exact `README.md` and `README_ZH.md` pages with
reciprocal top links. Never push, tag, create a release, upload assets, or change
authentication from this adapter.
