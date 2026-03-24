"""Batch encrypt / decrypt existing data when scopes are toggled.

Used by the admin UI and the ``rotate-keys`` CLI command.
"""

from __future__ import annotations

import json
import logging
import os

import asyncpg

from grcen.services.encryption import (
    ENCRYPTED_PREFIX,
    blind_index,
    decrypt_bytes,
    decrypt_field,
    encrypt_bytes,
    encrypt_field,
    get_engine,
)
from grcen.services.encryption_scopes import ALL_SCOPES, FieldTarget

log = logging.getLogger(__name__)


async def migrate_scope(
    pool: asyncpg.Pool,
    scope_name: str,
    *,
    encrypt: bool,
) -> int:
    """Encrypt or decrypt all existing data for *scope_name*.

    Returns the number of values changed.
    """
    scope = ALL_SCOPES.get(scope_name)
    if scope is None:
        raise ValueError(f"Unknown scope: {scope_name!r}")

    count = 0
    for target in scope.targets:
        count += await _migrate_target(pool, target, scope_name, encrypt=encrypt)

    # Scope-specific extras.
    if scope_name == "user_pii":
        count += await _migrate_user_blind_index(pool, encrypt=encrypt)

    if scope_name == "file_contents":
        count += await _migrate_files(pool, encrypt=encrypt)

    return count


async def rotate_scope(pool: asyncpg.Pool, scope_name: str) -> int:
    """Re-encrypt all data in *scope_name* with the current active key.

    Reads ciphertext encrypted under either the active or retired key,
    then writes it back encrypted under the active key only.
    """
    scope = ALL_SCOPES.get(scope_name)
    if scope is None:
        raise ValueError(f"Unknown scope: {scope_name!r}")

    count = 0
    for target in scope.targets:
        count += await _rotate_target(pool, target, scope_name)

    if scope_name == "user_pii":
        count += await _rotate_user_blind_index(pool)

    if scope_name == "file_contents":
        count += await _rotate_files(pool)

    return count


# ── column migration helpers ──────────────────────────────────────────────


async def _migrate_target(
    pool: asyncpg.Pool,
    target: FieldTarget,
    scope_name: str,
    *,
    encrypt: bool,
) -> int:
    if target.filter_key_column:
        return await _migrate_kv_target(pool, target, scope_name, encrypt=encrypt)
    return await _migrate_table_target(pool, target, scope_name, encrypt=encrypt)


async def _migrate_kv_target(
    pool: asyncpg.Pool,
    target: FieldTarget,
    scope_name: str,
    *,
    encrypt: bool,
) -> int:
    count = 0
    for key_val in target.filter_key_values:
        row = await pool.fetchrow(
            f"SELECT {target.column} FROM {target.table} "
            f"WHERE {target.filter_key_column} = $1",
            key_val,
        )
        if not row or not row[target.column]:
            continue
        current = row[target.column]
        is_enc = isinstance(current, str) and current.startswith(ENCRYPTED_PREFIX)

        if encrypt and is_enc:
            continue  # already encrypted
        if not encrypt and not is_enc:
            continue  # already plaintext

        if encrypt:
            new_val = encrypt_field(current, scope_name)
        else:
            new_val = decrypt_field(current, scope_name)

        await pool.execute(
            f"UPDATE {target.table} SET {target.column} = $1 "
            f"WHERE {target.filter_key_column} = $2",
            new_val,
            key_val,
        )
        count += 1
    return count


async def _migrate_table_target(
    pool: asyncpg.Pool,
    target: FieldTarget,
    scope_name: str,
    *,
    encrypt: bool,
    batch_size: int = 200,
) -> int:
    count = 0
    offset = 0
    while True:
        rows = await pool.fetch(
            f"SELECT id, {target.column} FROM {target.table} "
            f"WHERE {target.column} IS NOT NULL "
            f"ORDER BY id LIMIT $1 OFFSET $2",
            batch_size,
            offset,
        )
        if not rows:
            break
        for row in rows:
            current = row[target.column]
            # Handle JSONB (dict) columns — serialize to string for encryption.
            if isinstance(current, dict):
                current = json.dumps(current)
            if not isinstance(current, str):
                current = str(current)

            is_enc = current.startswith(ENCRYPTED_PREFIX)
            if encrypt and is_enc:
                continue
            if not encrypt and not is_enc:
                continue

            if encrypt:
                new_val = encrypt_field(current, scope_name)
            else:
                new_val = decrypt_field(current, scope_name)

            await pool.execute(
                f"UPDATE {target.table} SET {target.column} = $1 WHERE id = $2",
                new_val,
                row["id"],
            )
            count += 1
        offset += batch_size
    return count


# ── blind index helpers ───────────────────────────────────────────────────


async def _migrate_user_blind_index(pool: asyncpg.Pool, *, encrypt: bool) -> int:
    """Populate or clear the email_blind_idx column."""
    if encrypt:
        return await _populate_blind_indexes(pool)
    # Clear blind indexes when disabling the scope.
    result = await pool.execute(
        "UPDATE users SET email_blind_idx = NULL WHERE email_blind_idx IS NOT NULL"
    )
    return int(result.split()[-1]) if result else 0


async def _populate_blind_indexes(pool: asyncpg.Pool) -> int:
    """Compute blind indexes for all users with an email."""
    rows = await pool.fetch("SELECT id, email FROM users WHERE email IS NOT NULL")
    count = 0
    for row in rows:
        email = row["email"]
        # Decrypt if already encrypted (migration may run after column encryption).
        if isinstance(email, str) and email.startswith(ENCRYPTED_PREFIX):
            email = decrypt_field(email, "user_pii")
        idx = blind_index(email)
        if idx is not None:
            await pool.execute(
                "UPDATE users SET email_blind_idx = $1 WHERE id = $2", idx, row["id"]
            )
            count += 1
    return count


# ── file encryption helpers ───────────────────────────────────────────────


async def _migrate_files(pool: asyncpg.Pool, *, encrypt: bool) -> int:
    """Encrypt or decrypt all uploaded files on disk."""
    rows = await pool.fetch(
        "SELECT id, url_or_path, encrypted FROM attachments "
        "WHERE kind = 'file' AND url_or_path IS NOT NULL"
    )
    count = 0
    for row in rows:
        path = row["url_or_path"]
        is_enc = row["encrypted"]
        if encrypt and is_enc:
            continue
        if not encrypt and not is_enc:
            continue
        if not os.path.isfile(path):
            log.warning("Skipping missing file: %s", path)
            continue

        with open(path, "rb") as f:
            data = f.read()

        if encrypt:
            data = encrypt_bytes(data, "file_contents")
        else:
            data = decrypt_bytes(data, "file_contents")

        with open(path, "wb") as f:
            f.write(data)

        await pool.execute(
            "UPDATE attachments SET encrypted = $1 WHERE id = $2", encrypt, row["id"]
        )
        count += 1
    return count


# ── key rotation helpers ──────────────────────────────────────────────────


async def _rotate_target(
    pool: asyncpg.Pool,
    target: FieldTarget,
    scope_name: str,
) -> int:
    """Decrypt with any valid key, re-encrypt with the active key."""
    if target.filter_key_column:
        count = 0
        for key_val in target.filter_key_values:
            row = await pool.fetchrow(
                f"SELECT {target.column} FROM {target.table} "
                f"WHERE {target.filter_key_column} = $1",
                key_val,
            )
            if not row or not row[target.column]:
                continue
            current = row[target.column]
            if not isinstance(current, str) or not current.startswith(ENCRYPTED_PREFIX):
                continue
            plaintext = decrypt_field(current, scope_name)
            new_ct = encrypt_field(plaintext, scope_name)
            await pool.execute(
                f"UPDATE {target.table} SET {target.column} = $1 "
                f"WHERE {target.filter_key_column} = $2",
                new_ct,
                key_val,
            )
            count += 1
        return count

    count = 0
    offset = 0
    batch_size = 200
    while True:
        rows = await pool.fetch(
            f"SELECT id, {target.column} FROM {target.table} "
            f"WHERE {target.column} IS NOT NULL ORDER BY id LIMIT $1 OFFSET $2",
            batch_size,
            offset,
        )
        if not rows:
            break
        for row in rows:
            current = row[target.column]
            if isinstance(current, dict):
                current = json.dumps(current)
            if not isinstance(current, str) or not current.startswith(ENCRYPTED_PREFIX):
                continue
            plaintext = decrypt_field(current, scope_name)
            new_ct = encrypt_field(plaintext, scope_name)
            await pool.execute(
                f"UPDATE {target.table} SET {target.column} = $1 WHERE id = $2",
                new_ct,
                row["id"],
            )
            count += 1
        offset += batch_size
    return count


async def _rotate_user_blind_index(pool: asyncpg.Pool) -> int:
    """Recompute blind indexes after master key change."""
    return await _populate_blind_indexes(pool)


async def _rotate_files(pool: asyncpg.Pool) -> int:
    """Re-encrypt all encrypted files with the current active key."""
    engine = get_engine()
    if engine is None:
        return 0
    rows = await pool.fetch(
        "SELECT id, url_or_path FROM attachments WHERE encrypted = true AND url_or_path IS NOT NULL"
    )
    count = 0
    for row in rows:
        path = row["url_or_path"]
        if not os.path.isfile(path):
            log.warning("Skipping missing file during rotation: %s", path)
            continue
        with open(path, "rb") as f:
            data = f.read()
        # Decrypt with any valid key, re-encrypt with active.
        plaintext = decrypt_bytes(data, "file_contents")
        new_ct = encrypt_bytes(plaintext, "file_contents")
        with open(path, "wb") as f:
            f.write(new_ct)
        count += 1
    return count
