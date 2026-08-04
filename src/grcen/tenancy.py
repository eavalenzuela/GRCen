"""Tenant context and row-level security enforcement.

Every per-tenant query in GRCen is *supposed* to carry ``WHERE organization_id
= $n``. That's 212 hand-written filters across 58 service modules, and the
failure mode when one is missing is a silent cross-tenant read — which is
exactly what happened in ``services/org_views.py``, where four functions had no
org filter at all. Review caught it; nothing structural would have.

This module makes the boundary structural. Postgres row-level security policies
on every tenant table filter by a session GUC, so a query that forgets its
filter returns **nothing** instead of another tenant's rows. Fail-closed, not
fail-open.

## How the context reaches the database

The obvious approach — ``SET LOCAL`` inside every transaction — doesn't fit:
404 of GRCen's 459 database calls are single-statement ``pool.fetch(...)`` with
no transaction, where ``SET LOCAL`` is a no-op. Rewriting them all would be a
huge mechanical change for a weaker guarantee.

Instead the context rides asyncpg's per-acquire ``setup`` hook
(``prepare_connection`` below, wired in ``database.init_pool``). Every
``pool.fetch()`` internally acquires a connection, so the hook runs first and
stamps the GUCs from a contextvar. Call sites are untouched.

Session-scoped GUCs are safe here specifically because asyncpg runs ``RESET
ALL`` on release (``Connection.get_reset_query``), so a connection returning to
the pool cannot carry one tenant's scope into the next borrower's request. That
is load-bearing: if it ever stopped being true, a pooled connection would serve
the *wrong* tenant's rows, which is worse than no RLS at all. ``tests/test_rls.py``
pins the behaviour.

## Two planes

- **Identity plane — no RLS.** ``users``, ``user_organizations``, ``api_tokens``.
  These are read *before* a tenant context exists (that's how you authenticate),
  and ``organization_id`` isn't their access key anyway: a user belongs to many
  orgs through ``user_organizations``, so filtering them by their home org would
  break org switching and lock people out of their own memberships.
- **Data plane — RLS.** Everything else carrying ``organization_id``. The table
  list is derived from the catalog at migration time rather than hardcoded, so a
  new tenant table is protected the moment it exists; forgetting to add it here
  can't leave it exposed.

## Why there is a second role

GRCen's database user is a **superuser** (the postgres image makes
``POSTGRES_USER`` one, and most hand-rolled installs follow suit). Superusers
and ``BYPASSRLS`` roles are exempt from row-level security *unconditionally* —
``FORCE ROW LEVEL SECURITY`` closes the weaker table-owner exemption but does
nothing here. Policies created and left at that are completely inert: they show
up in ``pg_policy``, the test suite goes green, and every tenant can still read
every other tenant's rows. That was measured, not theorised.

So enforcement runs under ``grcen_app`` — ``NOSUPERUSER NOBYPASSRLS``, granted
plain DML and nothing else. The pool still *connects* as the owning role (it
has to; it runs the migrations), and ``prepare_connection`` issues ``SET ROLE``
per acquire. ``assert_enforcing`` verifies at startup that the role is real and
unprivileged, so this can't silently regress into theatre again.

## The bypass

Some work is legitimately cross-tenant: scheduled jobs sweeping every org, CLI
management commands, the migration itself, and pre-auth lookups that resolve a
capability token *in order to* discover which tenant it belongs to. Those run
inside ``system_context()``, which skips the ``SET ROLE`` and stays the owning
role.

Because bypass is "don't drop privileges" rather than "set a flag the policy
trusts", it's a genuine privilege boundary: code already running as ``grcen_app``
cannot escape by setting a GUC. ``app.rls_bypass`` remains in the policy only so
the owning role's own queries behave predictably.

Note ``RESET ALL`` — which asyncpg runs on release — does **not** reset the
role. ``prepare_connection`` therefore sets the role explicitly in both
directions on every acquire rather than assuming a clean slate.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

import asyncpg

ORG_GUC = "app.current_org"
BYPASS_GUC = "app.rls_bypass"
POLICY_NAME = "grcen_tenant_isolation"
# Unprivileged role that enforced queries run as. Never logs in — reached only
# via SET ROLE from the connection's owning role.
APP_ROLE = "grcen_app"

# Read before a tenant context can exist; see "Two planes" above.
IDENTITY_TABLES = frozenset({"users", "user_organizations", "api_tokens"})

_current_org: ContextVar[str] = ContextVar("grcen_current_org", default="")
_bypass: ContextVar[bool] = ContextVar("grcen_rls_bypass", default=False)

# Catalog-sourced, but never interpolate an unvalidated identifier into DDL.
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


# ── context ──────────────────────────────────────────────────────────────────


def current_org() -> str:
    return _current_org.get()


def is_bypassing() -> bool:
    return _bypass.get()


def set_tenant(organization_id: UUID | str | None) -> None:
    """Scope this context to one tenant, and clear any bypass.

    Clearing bypass matters in tests: the suite runs ambiently in
    ``system_context()`` so assertions can read raw tables, and a request
    handler must not inherit that. Every request therefore drops to enforced
    mode, which turns the whole existing suite into an RLS integration test.
    """
    _current_org.set(str(organization_id) if organization_id else "")
    _bypass.set(False)


@contextmanager
def tenant_context(organization_id: UUID | str | None):
    org_token = _current_org.set(str(organization_id) if organization_id else "")
    bypass_token = _bypass.set(False)
    try:
        yield
    finally:
        _current_org.reset(org_token)
        _bypass.reset(bypass_token)


def enable_process_bypass() -> None:
    """Bypass RLS for this whole process context, not just a block.

    For entry points that are cross-tenant from the first line and never return
    to enforced mode: the test suite (so fixtures and assertions can read raw
    tables). Tasks copy the context at creation, so setting it once at import
    time reaches every task spawned afterwards — which a ``with`` block inside
    an async fixture would not, since that runs in its own task context.
    """
    _bypass.set(True)


@contextmanager
def system_context():
    """Run cross-tenant work: migrations, scheduled jobs, CLI, pre-auth lookups.

    Deliberately noisy to write and easy to grep for — every use is a place
    where tenant isolation is intentionally not enforced.
    """
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


async def prepare_connection(conn: asyncpg.Connection) -> None:
    """asyncpg ``setup`` hook — stamp the current context onto the connection.

    Runs on every acquire, including the implicit one inside ``pool.fetch()``,
    which is how tenant context reaches 459 call sites that were never written
    to carry it.

    GUCs are session-scoped (``is_local=false``) because most calls have no
    surrounding transaction, where ``SET LOCAL`` would be a silent no-op;
    asyncpg's ``RESET ALL`` on release is what stops one tenant's scope reaching
    the next borrower. The role is set explicitly in *both* directions because
    ``RESET ALL`` does not restore it.
    """
    from grcen.config import settings

    bypassing = _bypass.get()
    await conn.execute(
        "SELECT set_config($1, $2, false), set_config($3, $4, false)",
        ORG_GUC, _current_org.get(),
        BYPASS_GUC, "on" if bypassing else "off",
    )
    # The role only exists when RLS is on — ``init_schema`` provisions it under
    # the same flag. Dropping to it regardless meant an install with
    # RLS_ENABLED=false failed on every acquire that wasn't bypassed, i.e. every
    # authenticated request. With RLS off there are no policies to enforce, so
    # there is nothing to drop privileges for.
    if bypassing or not settings.RLS_ENABLED:
        await conn.execute("RESET ROLE")
    else:
        await conn.execute(f"SET ROLE {APP_ROLE}")


# ── policy management ────────────────────────────────────────────────────────


_USING = (
    f"current_setting('{BYPASS_GUC}', true) = 'on' "
    f"OR organization_id = NULLIF(current_setting('{ORG_GUC}', true), '')::uuid"
)


async def tenant_tables(conn) -> list[str]:
    """Data-plane tables: everything with ``organization_id`` bar the identity plane."""
    rows = await conn.fetch(
        """SELECT c.relname AS name
           FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
           JOIN pg_attribute a ON a.attrelid = c.oid
                              AND a.attname = 'organization_id' AND a.attnum > 0
           WHERE c.relkind = 'r'
           ORDER BY c.relname"""
    )
    return [
        r["name"] for r in rows
        if r["name"] not in IDENTITY_TABLES and _SAFE_IDENT.match(r["name"])
    ]


async def ensure_app_role(conn) -> None:
    """Create/refresh the unprivileged role enforced queries run as.

    Grants are re-applied every startup so tables added by the migrations that
    just ran are covered on this boot rather than the next one, and DEFAULT
    PRIVILEGES cover anything created later. The role is also granted *to* the
    connecting role so ``SET ROLE`` works when that role isn't a superuser
    (managed Postgres, where the app user merely owns its schema).
    """
    try:
        await conn.execute(
            f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                    CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS
                                           NOCREATEDB NOCREATEROLE;
                END IF;
            END $$;
            """
        )
    except asyncpg.InsufficientPrivilegeError as exc:
        # Managed Postgres often withholds CREATEROLE. Say what to do rather
        # than surfacing a bare permission error from three layers down.
        raise RuntimeError(
            f"RLS_ENABLED is set but this database user cannot create the "
            f"{APP_ROLE!r} role. Either create it once as an administrator:\n"
            f"    CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS;\n"
            f"    GRANT {APP_ROLE} TO <your_app_user>;\n"
            f"or set RLS_ENABLED=false to fall back to application-level "
            f"tenant filtering only."
        ) from exc
    # Idempotent, and repairs a role someone hand-edited into being privileged.
    await conn.execute(f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS NOLOGIN")
    await conn.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    await conn.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
    )
    await conn.execute(
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"
    )
    await conn.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    await conn.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )
    await conn.execute(f"GRANT {APP_ROLE} TO CURRENT_USER")


async def assert_enforcing(conn) -> None:
    """Fail startup if RLS is configured but wouldn't actually enforce.

    The failure this guards against is specific and was hit for real during
    development: policies present, ``FORCE`` set, suite green — and every one of
    them inert because the connecting role was a superuser. Silent non-
    enforcement is the worst outcome available here, so it's a hard error.
    """
    role = await conn.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = $1", APP_ROLE
    )
    if role is None:
        raise RuntimeError(
            f"RLS_ENABLED is set but the {APP_ROLE!r} role is missing. The database "
            f"user needs privileges to create it, or set RLS_ENABLED=false."
        )
    if role["rolsuper"] or role["rolbypassrls"]:
        raise RuntimeError(
            f"{APP_ROLE!r} is SUPERUSER or has BYPASSRLS, so row-level security "
            f"would not apply. Run: ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS;"
        )
    missing = set(await tenant_tables(conn)) - await protected_tables(conn)
    if missing:
        raise RuntimeError(
            "RLS policies missing or unforced on: " + ", ".join(sorted(missing))
        )


async def apply_policies(conn) -> list[str]:
    """Enable + FORCE RLS and (re)create the isolation policy. Idempotent."""
    applied = []
    for table in await tenant_tables(conn):
        await conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # Without FORCE the owning role — which is the app role — ignores every
        # policy below. This line is the difference between real and theatre.
        await conn.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        await conn.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        await conn.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"FOR ALL USING ({_USING}) WITH CHECK ({_USING})"
        )
        applied.append(table)
    return applied


async def drop_policies(conn) -> list[str]:
    """Reverse ``apply_policies`` so RLS_ENABLED=false is actually reversible."""
    dropped = []
    for table in await tenant_tables(conn):
        await conn.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        await conn.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        await conn.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        dropped.append(table)
    return dropped


async def protected_tables(conn) -> set[str]:
    """Tables where the policy is present *and* forced — i.e. actually enforcing."""
    rows = await conn.fetch(
        """SELECT c.relname AS name
           FROM pg_policy p
           JOIN pg_class c ON c.oid = p.polrelid
           WHERE p.polname = $1 AND c.relrowsecurity AND c.relforcerowsecurity""",
        POLICY_NAME,
    )
    return {r["name"] for r in rows}
