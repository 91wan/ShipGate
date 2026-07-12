# ShipGate Engineering Rules

## Repository Map

- `shipgate/`: typed runtime package and checks.
- `scripts/shipgate.py`: compatibility entry point only.
- `scripts/install_skill.py`: bounded skill installer.
- `tests/`: unit, filesystem, Git, CLI, and installation tests.
- `docs/`: architecture, threat model, report schema, and review contract.

## Required Commands

Use Python 3.11+ and install `.[dev]` in an isolated environment. Before calling
work complete, run:

```bash
make validate PYTHON=.venv/bin/python
```

Run external official skill validation separately when its path is available;
never substitute ShipGate self-check for that validator.

## Hard Constraints

- Keep runtime dependencies standard-library only.
- Keep ShipGate read-only. Never add push, tag, release, upload, remote, or auth writes.
- Fail on unreadable, unscanned, ambiguous, shallow, or unverified required content.
- Use one inventory for gates; checks must not independently walk the project.
- Report relative POSIX paths and safe fingerprints, never full secrets or host paths.
- Require exact split `README.md` and `README_ZH.md` pages with reciprocal top links.
- Add regression tests for every behavior change and update both README languages.
- Do not use `shell=True`, unbounded deletion, broad ignore directories, or external symlink following.

## Definition of Done

Ruff, format, mypy, compileall, all tests, line coverage >=95%, branch coverage
>=90%, local self-check, and privacy assertions must pass. Review
`docs/ARCHITECTURE.md` and `docs/CODE_REVIEW.md` before changing core policy.
Do not push, tag, release, or upload as part of repository validation.
