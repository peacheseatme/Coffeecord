"""Tests for scripts/ccord_release_resolve.py (mocked GitHub API)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ccord_release_resolve as resolve  # noqa: E402
import ccord_release_apply as apply  # noqa: E402


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GITHUB_REPO", "owner/coffeecord")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return tmp_path


def _api_release(tag: str, *, with_asset: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tag_name": tag,
        "zipball_url": f"https://api.github.com/repos/owner/coffeecord/zipball/{tag}",
        "tarball_url": f"https://api.github.com/repos/owner/coffeecord/tarball/{tag}",
        "assets": [],
    }
    if with_asset:
        data["assets"] = [
            {
                "name": "coffeecord-release.zip",
                "browser_download_url": "https://example.com/coffeecord-release.zip",
            }
        ]
    return data


def test_resolve_latest_json(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api(path: str) -> tuple[int, dict | list | None, str]:
        assert path.endswith("/releases/latest")
        return 200, _api_release("1.0.4"), ""

    monkeypatch.setattr(resolve, "_api_request", fake_api)
    payload = resolve.resolve(repo_root)
    assert payload["tag"] == "1.0.4"
    assert payload["schema_version"] == resolve.SCHEMA_VERSION
    assert payload["zipball_url"].endswith("/zipball/1.0.4")
    assert payload["preferred_archive_url"] == payload["zipball_url"]
    assert payload["asset_url"] is None
    assert payload["source"] == "release"


def test_resolve_latest_cli_json(repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_api(path: str) -> tuple[int, dict | list | None, str]:
        return 200, _api_release("v1.0.4", with_asset=True), ""

    monkeypatch.setattr(resolve, "_api_request", fake_api)
    rc = resolve.main([str(repo_root), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["tag"] == "v1.0.4"
    assert out["asset_url"] == "https://example.com/coffeecord-release.zip"
    assert out["preferred_archive_url"].endswith("/zipball/v1.0.4")


def test_resolve_pinned_tag_variants(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_api(path: str) -> tuple[int, dict | list | None, str]:
        calls.append(path)
        if path.endswith("/releases/tags/1.0.4"):
            return 404, {"message": "Not Found"}, ""
        if path.endswith("/releases/tags/v1.0.4"):
            return 200, _api_release("v1.0.4"), ""
        return 404, None, ""

    monkeypatch.setattr(resolve, "_api_request", fake_api)
    payload = resolve.resolve(repo_root, "1.0.4")
    assert payload["tag"] == "v1.0.4"
    assert any(p.endswith("/releases/tags/1.0.4") for p in calls)
    assert any(p.endswith("/releases/tags/v1.0.4") for p in calls)


def test_resolve_plain_tag_stdout(repo_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        resolve,
        "_api_request",
        lambda path: (200, _api_release("1.0.3"), ""),
    )
    rc = resolve.main([str(repo_root)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "1.0.3"


def test_apply_preserves_local_paths(tmp_path: Path) -> None:
    install = tmp_path / "install"
    src = tmp_path / "src_tree"
    for base in (install, src):
        (base / "Modules").mkdir(parents=True)
        (base / "Src").mkdir(parents=True)
        (base / "Storage" / "Config").mkdir(parents=True)
        (base / "Storage" / "Data").mkdir(parents=True)
        (base / ".venv" / "bin").mkdir(parents=True)

    (install / "Src" / ".env").write_text("SECRET=keep\n", encoding="utf-8")
    (install / "Src" / "ticket.env").write_text("TICKET=keep\n", encoding="utf-8")
    (install / "Storage" / "Config" / "c-cord.json").write_text('{"x":1}\n', encoding="utf-8")
    (install / "Storage" / "Data" / "banned_users.json").write_text('{"banned_users":[]}\n', encoding="utf-8")
    (install / ".venv" / "bin" / "python").write_text("old\n", encoding="utf-8")
    (install / "Modules" / "old.py").write_text("old\n", encoding="utf-8")

    (src / "Src" / ".env").write_text("SECRET=overwrite\n", encoding="utf-8")
    (src / "Src" / "ticket.env").write_text("TICKET=overwrite\n", encoding="utf-8")
    (src / "Src" / "Bot.py").write_text("new bot\n", encoding="utf-8")
    (src / "Storage" / "Config" / "c-cord.json").write_text('{"x":99}\n', encoding="utf-8")
    (src / "Storage" / "Data" / "banned_users.json").write_text('{"banned_users":[1]}\n', encoding="utf-8")
    (src / ".venv" / "bin" / "python").write_text("new\n", encoding="utf-8")
    (src / "Modules" / "old.py").write_text("new module\n", encoding="utf-8")
    (src / "Modules" / "new.py").write_text("added\n", encoding="utf-8")

    copied, skipped = apply._overlay(src, install)
    assert copied >= 2
    assert skipped >= 1
    assert (install / "Src" / ".env").read_text(encoding="utf-8") == "SECRET=keep\n"
    assert (install / "Src" / "ticket.env").read_text(encoding="utf-8") == "TICKET=keep\n"
    assert (install / "Storage" / "Config" / "c-cord.json").read_text(encoding="utf-8") == '{"x":1}\n'
    assert (install / "Storage" / "Data" / "banned_users.json").read_text(encoding="utf-8") == '{"banned_users":[]}\n'
    assert (install / ".venv" / "bin" / "python").read_text(encoding="utf-8") == "old\n"
    assert (install / "Modules" / "old.py").read_text(encoding="utf-8") == "new module\n"
    assert (install / "Modules" / "new.py").read_text(encoding="utf-8") == "added\n"
    assert (install / "Src" / "Bot.py").read_text(encoding="utf-8") == "new bot\n"


def test_is_preserved_helpers() -> None:
    assert apply._is_preserved("Storage/Config/c-cord.json")
    assert apply._is_preserved("Storage/Data/banned_users.json")
    assert apply._is_preserved(".venv/lib/x")
    assert apply._is_preserved("Src/.env")
    assert apply._is_preserved("Src/ticket.env")
    assert not apply._is_preserved("Modules/automod.py")
    assert not apply._is_preserved("Src/Bot.py")
    assert not apply._is_preserved("bot.sh")
    assert not apply._is_preserved("data/banned_users.json")
