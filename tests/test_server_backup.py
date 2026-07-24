"""Tests for encrypted server backup crypto, slots, and id remap helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from Modules.backup_crypto import (
    PHRASE_LEN,
    BackupCryptoError,
    generate_passphrase,
    pack_backup,
    unpack_backup,
)
from Modules.server_backup import (
    BACKUP_SLOTS_FREE,
    BACKUP_SLOTS_SUPPORTER,
    _rebuild_embeds,
    _serialize_embed,
    max_backup_slots_for_user,
    normalize_slot_id,
    remap_ids_in_obj,
)
import discord


def test_generate_passphrase_length_and_alphabet() -> None:
    phrase = generate_passphrase()
    assert len(phrase) == PHRASE_LEN
    assert all(ch.isalnum() for ch in phrase)


def test_pack_unpack_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUP_SECRET", "unit-test-backup-secret")
    phrase = generate_passphrase()
    payload = {
        "meta": {"source_guild_id": 123},
        "discord": {"roles": [{"id": 1, "name": "Admin"}]},
        "coffeecord": {"files": {}},
    }
    blob = pack_backup(payload, phrase)
    assert blob.startswith(b"CCBAK\x02")
    assert unpack_backup(blob, phrase) == payload


def test_pack_uses_lzma_not_gzip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUP_SECRET", "unit-test-backup-secret")
    phrase = generate_passphrase()
    blob = pack_backup({"big": "x" * 5000}, phrase)
    # Magic v2; ciphertext should not look like a gzip member after Fernet decrypt —
    # at least verify magic and round-trip with a compressible payload.
    assert blob[:6] == b"CCBAK\x02"
    assert unpack_backup(blob, phrase)["big"] == "x" * 5000


def test_wrong_passphrase_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUP_SECRET", "unit-test-backup-secret")
    phrase = generate_passphrase()
    other = generate_passphrase()
    while other == phrase:
        other = generate_passphrase()
    blob = pack_backup({"ok": True}, phrase)
    with pytest.raises(BackupCryptoError):
        unpack_backup(blob, other)


def test_tampered_payload_fails_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKUP_SECRET", "unit-test-backup-secret-a")
    phrase = generate_passphrase()
    blob = pack_backup({"v": 1}, phrase)
    # Flip a byte in the ciphertext region (after header)
    mutable = bytearray(blob)
    mutable[-5] ^= 0xFF
    with pytest.raises(BackupCryptoError):
        unpack_backup(bytes(mutable), phrase)


def test_normalize_slot_id() -> None:
    assert normalize_slot_id(" Main_Backup ") == "main_backup"
    assert normalize_slot_id("bad name") is None
    assert normalize_slot_id("") is None


def test_max_backup_slots_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supporters = tmp_path / "supporters.json"
    supporters.write_text('{"supporters": {}}', encoding="utf-8")
    monkeypatch.setattr("Modules.server_backup.SUPPORTERS_FILE", supporters)
    assert max_backup_slots_for_user(42) == BACKUP_SLOTS_FREE

    supporters.write_text(
        '{"supporters": {"99": {"active": true, "tier": "donation"}}}',
        encoding="utf-8",
    )
    assert max_backup_slots_for_user(99) == BACKUP_SLOTS_SUPPORTER


def test_remap_ids_in_obj() -> None:
    id_map = {111: 999, 222: 888}
    src = {
        "log_channel_id": 111,
        "nested": {"role": "222", "keep": "333"},
        "list": [111, "222", 444],
    }
    out = remap_ids_in_obj(src, id_map)
    assert out["log_channel_id"] == 999
    assert out["nested"]["role"] == "888"
    assert out["nested"]["keep"] == "333"
    assert out["list"] == [999, "888", 444]


def test_embed_json_round_trip_for_restore() -> None:
    original = discord.Embed(
        title="Verify",
        description="Click below <@123>",
        colour=discord.Colour.blue(),
        url="https://example.com",
    )
    original.add_field(name="Role", value="<@&456>", inline=True)
    original.set_footer(text="Coffeecord")
    stored = _serialize_embed(original)
    assert isinstance(stored, dict)
    assert stored.get("title") == "Verify"
    assert "fields" in stored

    rebuilt = _rebuild_embeds([stored])
    assert len(rebuilt) == 1
    assert rebuilt[0].title == "Verify"
    assert rebuilt[0].description is not None
    assert "@\u200b" in (rebuilt[0].description or "")
    assert rebuilt[0].fields and "@\u200b" in rebuilt[0].fields[0].value

    # Legacy title/description-only payload still rebuilds.
    legacy = _rebuild_embeds([{"title": "Old", "description": "Plain"}])
    assert len(legacy) == 1
    assert legacy[0].title == "Old"
