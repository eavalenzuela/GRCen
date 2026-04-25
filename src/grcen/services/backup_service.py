"""Encrypted database backup and restore.

Pipes a `pg_dump` of the configured database through AES-256-GCM (one chunk
per write) using the existing master encryption key. Output format:

    GRCBKP\x01           magic + version
    [12-byte nonce][4-byte BE chunk-len][ciphertext+tag][...repeat...]
    [12-byte nonce][0x00000000 chunk-len]    EOF marker

A fresh nonce per chunk so the GCM tag stays valid even on multi-chunk dumps,
and the magic header lets `restore` reject corrupt or wrong-format input
before bothering pg_restore.
"""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from grcen.config import settings
from grcen.services.encryption import is_encryption_enabled

MAGIC = b"GRCBKP\x01"
_CHUNK_SIZE = 64 * 1024  # 64 KiB plaintext chunks
_INFO = b"grcen-backup"


class BackupError(RuntimeError):
    pass


def _backup_key() -> AESGCM:
    """Derive a backup-only key from the master ENCRYPTION_KEY via HKDF."""
    if not is_encryption_enabled():
        raise BackupError("Backup encryption requires ENCRYPTION_KEY to be set.")
    import base64
    master = base64.urlsafe_b64decode(settings.ENCRYPTION_KEY)
    derived = HKDF(
        algorithm=SHA256(), length=32, salt=b"backup-salt", info=_INFO
    ).derive(master)
    return AESGCM(derived)


def _dsn_to_pg_args() -> tuple[list[str], dict[str, str]]:
    """Translate the configured DATABASE_URL into pg_dump CLI args + env."""
    from urllib.parse import urlparse, parse_qs

    raw = settings.DATABASE_URL
    if raw.startswith("postgresql+asyncpg://"):
        raw = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    parsed = urlparse(raw)
    args = []
    env = {}
    if parsed.hostname:
        args += ["-h", parsed.hostname]
    if parsed.port:
        args += ["-p", str(parsed.port)]
    if parsed.username:
        args += ["-U", parsed.username]
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    if parsed.path and parsed.path.lstrip("/"):
        args.append(parsed.path.lstrip("/"))
    return args, env


def _spawn(cmd: list[str], extra_args: list[str], extra_env: dict[str, str], **kwargs):
    full_env = {**os.environ, **extra_env}
    return subprocess.Popen(cmd + extra_args, env=full_env, **kwargs)


def encrypt_stream(plaintext_iter, out_path: Path) -> int:
    """Write the plaintext chunks to *out_path* in encrypted-stream format."""
    key = _backup_key()
    bytes_written = 0
    with out_path.open("wb") as out:
        out.write(MAGIC)
        bytes_written += len(MAGIC)
        for chunk in plaintext_iter:
            if not chunk:
                continue
            nonce = os.urandom(12)
            ct = key.encrypt(nonce, chunk, b"backup")
            out.write(nonce)
            out.write(struct.pack(">I", len(ct)))
            out.write(ct)
            bytes_written += 12 + 4 + len(ct)
        # EOF marker
        out.write(os.urandom(12))
        out.write(struct.pack(">I", 0))
        bytes_written += 16
    return bytes_written


def decrypt_stream(in_path: Path):
    """Generator yielding decrypted chunks read from *in_path*."""
    key = _backup_key()
    with in_path.open("rb") as src:
        head = src.read(len(MAGIC))
        if head != MAGIC:
            raise BackupError("Not a GRCen backup file (magic header mismatch).")
        while True:
            nonce = src.read(12)
            length_buf = src.read(4)
            if len(nonce) < 12 or len(length_buf) < 4:
                raise BackupError("Truncated backup file.")
            (length,) = struct.unpack(">I", length_buf)
            if length == 0:
                return
            ct = src.read(length)
            if len(ct) < length:
                raise BackupError("Truncated backup file.")
            yield key.decrypt(nonce, ct, b"backup")


def create_backup(out_path: Path) -> int:
    """Run pg_dump and stream its output into the encrypted file at *out_path*.

    Returns the number of encrypted bytes written. Raises BackupError if
    pg_dump exits non-zero.
    """
    args, env = _dsn_to_pg_args()
    proc = _spawn(
        ["pg_dump", "--no-owner", "--no-acl"], args, env, stdout=subprocess.PIPE
    )

    def _chunks():
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk

    written = encrypt_stream(_chunks(), out_path)
    rc = proc.wait()
    if rc != 0:
        raise BackupError(f"pg_dump exited with code {rc}")
    return written


def restore_backup(in_path: Path) -> None:
    """Decrypt *in_path* and pipe the SQL into psql to restore.

    The user is responsible for first dropping/recreating the target database.
    psql --set ON_ERROR_STOP=1 means a single SQL error halts the restore so
    you don't end up with a partially-applied dump.
    """
    args, env = _dsn_to_pg_args()
    proc = _spawn(
        ["psql", "-v", "ON_ERROR_STOP=1"], args, env, stdin=subprocess.PIPE
    )
    try:
        assert proc.stdin is not None
        for chunk in decrypt_stream(in_path):
            proc.stdin.write(chunk)
        proc.stdin.close()
    except BrokenPipeError:
        # psql aborted early; fall through to capture the exit code below.
        pass
    rc = proc.wait()
    if rc != 0:
        raise BackupError(f"psql exited with code {rc}")
