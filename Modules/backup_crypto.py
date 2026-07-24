"""Encrypt/decrypt Coffeecord server backup archives (.ccbak)."""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import lzma
import os
import secrets
import struct
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# v1 = gzip (legacy), v2 = lzma (better ratio for large JSON / message history)
MAGIC_V1 = b"CCBAK\x01"
MAGIC_V2 = b"CCBAK\x02"
MAGIC = MAGIC_V2
SCHEMA_VERSION = 2
SALT_LEN = 16
PHRASE_LEN = 18
# Avoid ambiguous 0/O/1/l/I characters.
PHRASE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
PBKDF2_ITERATIONS = 390_000
# Layout: MAGIC(6) | salt(16) | iterations(uint32 BE) | fernet_token


class BackupCryptoError(ValueError):
    """Raised when packing/unpacking a backup fails."""


def generate_passphrase(length: int = PHRASE_LEN) -> str:
    return "".join(secrets.choice(PHRASE_ALPHABET) for _ in range(length))


def _backup_secret() -> bytes:
    raw = os.getenv("BACKUP_SECRET", "").strip()
    if raw:
        return raw.encode("utf-8")
    # Fall back so local/dev still works; production should set BACKUP_SECRET.
    ticket = os.getenv("TICKET_SECRET", "").strip()
    if ticket:
        return ticket.encode("utf-8")
    return b"coffeecord-backup-dev-secret"


def sign_backup_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hmac.new(_backup_secret(), raw, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_backup_payload(payload: dict[str, Any], signature: str) -> bool:
    try:
        provided = base64.b64decode(signature.encode("ascii"), validate=True)
    except Exception:
        return False
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = hmac.new(_backup_secret(), raw, hashlib.sha256).digest()
    return hmac.compare_digest(expected, provided)


def _derive_fernet_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    key = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(key)


def _compress(raw: bytes) -> bytes:
    return lzma.compress(raw, preset=6)


def _decompress(magic: bytes, compressed: bytes) -> bytes:
    if magic == MAGIC_V2:
        return lzma.decompress(compressed)
    if magic == MAGIC_V1:
        return gzip.decompress(compressed)
    raise BackupCryptoError("Unsupported backup format version.")


def pack_backup(payload: dict[str, Any], passphrase: str) -> bytes:
    if len(passphrase) != PHRASE_LEN:
        raise BackupCryptoError(f"Passphrase must be exactly {PHRASE_LEN} characters.")
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "payload": payload,
        "signature": sign_backup_payload(payload),
    }
    raw_json = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = _compress(raw_json)
    salt = os.urandom(SALT_LEN)
    fernet = Fernet(_derive_fernet_key(passphrase, salt, PBKDF2_ITERATIONS))
    token = fernet.encrypt(compressed)
    return MAGIC + salt + struct.pack(">I", PBKDF2_ITERATIONS) + token


def unpack_backup(blob: bytes, passphrase: str) -> dict[str, Any]:
    if len(passphrase) != PHRASE_LEN:
        raise BackupCryptoError("Invalid passphrase.")
    if len(blob) < len(MAGIC) + SALT_LEN + 4 + 16:
        raise BackupCryptoError("Backup file is truncated or corrupt.")
    magic = blob[:6]
    if magic not in (MAGIC_V1, MAGIC_V2):
        raise BackupCryptoError("Not a Coffeecord backup file.")
    offset = 6
    salt = blob[offset : offset + SALT_LEN]
    offset += SALT_LEN
    (iterations,) = struct.unpack(">I", blob[offset : offset + 4])
    offset += 4
    token = blob[offset:]
    try:
        fernet = Fernet(_derive_fernet_key(passphrase, salt, iterations))
        compressed = fernet.decrypt(token)
    except InvalidToken as exc:
        raise BackupCryptoError("Invalid passphrase or corrupt backup.") from exc
    try:
        raw_json = _decompress(magic, compressed)
        envelope = json.loads(raw_json.decode("utf-8"))
    except Exception as exc:
        raise BackupCryptoError("Backup payload is corrupt.") from exc
    if not isinstance(envelope, dict):
        raise BackupCryptoError("Backup payload is corrupt.")
    payload = envelope.get("payload")
    signature = envelope.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise BackupCryptoError("Backup payload is corrupt.")
    if not verify_backup_payload(payload, signature):
        raise BackupCryptoError("Backup signature verification failed.")
    return payload
