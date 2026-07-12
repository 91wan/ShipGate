# Contributing

ShipGate requires Python 3.11+ and has no third-party runtime dependencies.
Optional development tools are declared in `pyproject.toml`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
make validate PYTHON=.venv/bin/python
```

Add a failing regression test before fixing confirmed fail-open behavior. Keep
CLI compatibility, use typed models, preserve one shared inventory, and update
both README languages plus agent adapters when behavior changes.

Do not add publication writes, runtime dependencies, `shell=True`, broad ignore
rules, report secrets, absolute paths, or unsafe recursive deletion. Follow
`docs/CODE_REVIEW.md` before submitting a change.
