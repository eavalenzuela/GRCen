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
