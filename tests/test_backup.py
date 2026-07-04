"""Tests for backup encryption stream format.

The DB-pipe path (pg_dump/psql) is exercised by integration runs and isn't
covered here — these tests pin the encrypt/decrypt format and reject
malformed input.
"""
import base64
import os
import secrets
from pathlib import Path

import pytest

from grcen.config import settings
from grcen.services import backup_service


@pytest.fixture
def encryption_on(monkeypatch):
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key)
    # Force the encryption singleton to re-init with the new key.
    from grcen.services import encryption as enc
    monkeypatch.setattr(enc, "_initialised", False)
    monkeypatch.setattr(enc, "_engine", None)
    yield


def test_round_trip_small_payload(tmp_path: Path, encryption_on):
    target = tmp_path / "out.bin"
    plaintext = b"hello, world\n" * 100
    backup_service.encrypt_stream([plaintext], target)
    assert target.read_bytes()[: len(backup_service.MAGIC)] == backup_service.MAGIC

    chunks = list(backup_service.decrypt_stream(target))
    assert b"".join(chunks) == plaintext


def test_round_trip_multi_chunk(tmp_path: Path, encryption_on):
    target = tmp_path / "out.bin"
    chunks = [os.urandom(8192), os.urandom(8192), os.urandom(1)]
    backup_service.encrypt_stream(chunks, target)
    decrypted = b"".join(backup_service.decrypt_stream(target))
    assert decrypted == b"".join(chunks)


def test_decrypt_rejects_missing_magic(tmp_path: Path, encryption_on):
    target = tmp_path / "bogus.bin"
    target.write_bytes(b"not a backup at all")
    with pytest.raises(backup_service.BackupError) as exc:
        list(backup_service.decrypt_stream(target))
    assert "magic" in str(exc.value).lower()


def test_decrypt_rejects_truncated_file(tmp_path: Path, encryption_on):
    target = tmp_path / "out.bin"
    backup_service.encrypt_stream([b"some data"], target)
    raw = target.read_bytes()
    target.write_bytes(raw[:-5])  # chop the EOF marker
    with pytest.raises(backup_service.BackupError):
        list(backup_service.decrypt_stream(target))


def test_decrypt_fails_with_different_key(tmp_path: Path, monkeypatch):
    target = tmp_path / "out.bin"
    # Encrypt under key A
    key_a = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key_a)
    from grcen.services import encryption as enc
    monkeypatch.setattr(enc, "_initialised", False)
    monkeypatch.setattr(enc, "_engine", None)
    backup_service.encrypt_stream([b"sensitive"], target)

    # Try to decrypt under key B
    key_b = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key_b)
    monkeypatch.setattr(enc, "_initialised", False)
    monkeypatch.setattr(enc, "_engine", None)
    with pytest.raises(Exception):
        list(backup_service.decrypt_stream(target))


def test_backup_requires_encryption_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "")
    from grcen.services import encryption as enc
    monkeypatch.setattr(enc, "_initialised", False)
    monkeypatch.setattr(enc, "_engine", None)
    with pytest.raises(backup_service.BackupError) as exc:
        backup_service.encrypt_stream([b"x"], tmp_path / "out.bin")
    assert "ENCRYPTION_KEY" in str(exc.value)


def test_v2_files_have_unique_salts(tmp_path: Path, encryption_on):
    """Each backup gets its own random HKDF salt in the header."""
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    backup_service.encrypt_stream([b"same plaintext"], a)
    backup_service.encrypt_stream([b"same plaintext"], b)
    magic_len = len(backup_service.MAGIC)
    salt_a = a.read_bytes()[magic_len:magic_len + 16]
    salt_b = b.read_bytes()[magic_len:magic_len + 16]
    assert salt_a != salt_b
    # And both still round-trip.
    assert b"".join(backup_service.decrypt_stream(a)) == b"same plaintext"
    assert b"".join(backup_service.decrypt_stream(b)) == b"same plaintext"


def test_legacy_v1_files_still_restore(tmp_path: Path, encryption_on):
    """A v1 file (fixed salt, GRCBKP\\x01 magic) decrypts transparently."""
    import struct

    target = tmp_path / "legacy.bin"
    key = backup_service._backup_key()  # legacy fixed salt
    plaintext = b"v1 era dump"
    nonce = os.urandom(12)
    ct = key.encrypt(nonce, plaintext, b"backup")
    with target.open("wb") as out:
        out.write(backup_service.MAGIC_V1)
        out.write(nonce)
        out.write(struct.pack(">I", len(ct)))
        out.write(ct)
        out.write(os.urandom(12))
        out.write(struct.pack(">I", 0))

    assert b"".join(backup_service.decrypt_stream(target)) == plaintext


def test_truncated_salt_header_rejected(tmp_path: Path, encryption_on):
    target = tmp_path / "cut.bin"
    target.write_bytes(backup_service.MAGIC + b"\x00" * 4)  # header cut short
    with pytest.raises(backup_service.BackupError):
        list(backup_service.decrypt_stream(target))
