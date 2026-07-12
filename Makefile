PYTHON ?= python3
INSTALL_DIR ?=
QUICK_VALIDATE ?=

ifeq ($(strip $(INSTALL_DIR)),)
INSTALL_ARGS = --scope user
else
INSTALL_ARGS = --scope custom --target "$(INSTALL_DIR)"
endif

.PHONY: compile lint format-check type-check test coverage skill-contract self-check validate install-local install-codex-home official-skill-validate

compile:
	$(PYTHON) -m compileall -q shipgate scripts tests

lint:
	$(PYTHON) -m ruff check .

format-check:
	$(PYTHON) -m ruff format --check .

type-check:
	$(PYTHON) -m mypy shipgate scripts

test:
	$(PYTHON) -m unittest discover -s tests -v

coverage:
	mkdir -p build
	$(PYTHON) -m coverage erase
	$(PYTHON) -m coverage run --branch -m unittest discover -s tests
	$(PYTHON) -m coverage report --show-missing --fail-under=95
	$(PYTHON) -m coverage json -o build/coverage.json
	$(PYTHON) scripts/check_coverage_thresholds.py build/coverage.json --line 95 --branch 90

skill-contract:
	$(PYTHON) scripts/shipgate.py check . --operation local --project-type codex-skill

self-check:
	mkdir -p build/shipgate
	$(PYTHON) scripts/shipgate.py check . --operation local --project-type codex-skill --report-md build/shipgate/local.md --report-json build/shipgate/local.json

official-skill-validate:
	@test -n "$(QUICK_VALIDATE)" || (echo "Set QUICK_VALIDATE to an external official validator path" >&2; exit 2)
	$(PYTHON) "$(QUICK_VALIDATE)" .

validate: compile lint format-check type-check test coverage skill-contract self-check

install-local: validate
	$(PYTHON) scripts/install_skill.py $(INSTALL_ARGS)

install-codex-home: validate
	$(PYTHON) scripts/install_skill.py --scope codex-home
