# Contributing to GRCen

Thanks for your interest in improving GRCen. This guide covers how to set up a
development environment, the standards we hold code to, and what to expect when
opening a pull request.

## Development Setup

Requirements: Python 3.12+, a running PostgreSQL instance, and (optionally)
Docker Compose.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # edit DATABASE_URL and set a SECRET_KEY
grcen createadmin
grcen runserver
```

The schema is created automatically on startup — there is no separate migration
step for local development.

## Running Tests

Tests require a PostgreSQL database (default `grcen_test`). The suite manages its
own schema and truncates tables between tests.

```bash
docker compose exec -T db psql -U grcen -c "CREATE DATABASE grcen_test"  # once
.venv/bin/pytest
```

Always run tests with `.venv/bin/pytest` rather than a system Python so the
correct dependencies are used. New features and bug fixes should come with
tests; the suite is integration-style and exercises the full HTTP stack via
`httpx.ASGITransport` (see `tests/conftest.py` for fixtures like `auth_client`,
`editor_client`, `viewer_client`, and `auditor_client`).

## Code Standards

We use [ruff](https://docs.astral.sh/ruff/) for linting/formatting and
[mypy](https://mypy-lang.org/) for type checking. Both are installed by the
`[dev]` extra. Before opening a PR:

```bash
.venv/bin/ruff check src/
.venv/bin/mypy src/grcen
```

- Match the style and conventions of the surrounding code.
- Keep async paths async — no blocking I/O (use `asyncpg`, `aiosmtplib`,
  `aiofiles`, `httpx.AsyncClient`).
- Use parameterized SQL via asyncpg. Never build SQL with f-strings or
  `.format()`.
- Org Views trees use orthogonal (right-angle) connectors, not curves or
  diagonals.

## Security

GRCen handles sensitive compliance data and takes its security posture
seriously. Before changing authentication, cryptography, session handling, or
any input-handling code, read **[secure_coding_requirements.md](secure_coding_requirements.md)**
and **[security_features_and_requirements.md](security_features_and_requirements.md)**.

PRs touching those areas should call out the security considerations explicitly
in the description. If you discover a security vulnerability, please report it
privately rather than opening a public issue.

## Pull Requests

- Branch off `main` and keep PRs focused on a single concern.
- Ensure `ruff`, `mypy`, and `pytest` all pass.
- Write clear commit messages describing the *why*, not just the *what*.
- Update documentation (`README.md`, `CLAUDE.md`, `feature_roadmap.md`) when
  behavior or features change.
