"""Nox session definitions for the quality-gate pipeline.

Sessions: lint, typecheck, tests, security, deps, arch.
Run all gates:  nox
Run one gate:   nox -s lint
"""

from __future__ import annotations

import nox

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True

PYTHON = "3.11"

# ---------------------------------------------------------------------------
# Lint: ruff check + ruff format --check
# ---------------------------------------------------------------------------

@nox.session(python=PYTHON)
def lint(session: nox.Session) -> None:
    """Run ruff linter."""
    session.install("ruff>=0.3,<1")
    session.run("ruff", "check", ".")


# ---------------------------------------------------------------------------
# Type-check: mypy (basedpyright added separately in B-007-T3)
# ---------------------------------------------------------------------------

@nox.session(python=PYTHON)
def typecheck(session: nox.Session) -> None:
    """Run mypy and basedpyright type checking."""
    session.install("-e", ".[all]")
    session.install("basedpyright")
    session.run(
        "mypy",
        "libs",
        "apps",
        "tests/integration",
        "tests/contract",
        "tests/e2e",
    )
    session.run("basedpyright")


# ---------------------------------------------------------------------------
# Tests: pytest
# ---------------------------------------------------------------------------

@nox.session(python=PYTHON)
def tests(session: nox.Session) -> None:
    """Run the full test suite."""
    session.install("-e", ".[all]")
    session.run("pytest", "-q", *session.posargs)


# ---------------------------------------------------------------------------
# Security: pip-audit + semgrep
# ---------------------------------------------------------------------------

@nox.session(python=PYTHON)
def security(session: nox.Session) -> None:
    """Run security audits (pip-audit)."""
    session.install("-e", ".[all]")
    session.install("pip-audit")
    session.run("pip-audit", "--strict", "--progress-spinner=off")


# ---------------------------------------------------------------------------
# Deps: deptry — detect unused, missing, or transitive-only imports
# ---------------------------------------------------------------------------

@nox.session(python=PYTHON)
def deps(session: nox.Session) -> None:
    """Check dependency hygiene with deptry."""
    session.install("-e", ".[all]")
    session.install("deptry")
    session.run("deptry", "libs", "apps")


# ---------------------------------------------------------------------------
# Arch: import-linter — enforce layered architecture constraints
# ---------------------------------------------------------------------------

@nox.session(python=PYTHON)
def arch(session: nox.Session) -> None:
    """Enforce architectural import constraints with import-linter."""
    session.install("-e", ".[all]")
    session.install("import-linter")
    session.run("lint-imports")
