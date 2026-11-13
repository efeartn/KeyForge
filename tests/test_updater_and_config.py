"""Automated tests for KeyForge configuration persistence, active profile backup, and secure auto-updater."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest

from keyforge.config import (
    ACTIVE_PROFILE_BACKUP_FILENAME,
    ACTIVE_PROFILE_FILENAME,
    CONFIG_BACKUP_FILENAME,
    CONFIG_FILENAME,
    MAX_CONFIG_FILE_BYTES,
    AppConfig,
    _is_unisolated_pytest_run,
    get_config_dir,
    load_active_profile_backup,
    load_app_config,
    save_active_profile_backup,
    save_app_config,
)
from keyforge.models.action import ClickType, MouseButton, make_key_press, make_mouse_click, make_wait
from keyforge.models.profile import MacroProfile, RepeatMode, create_default_profile
from keyforge.updater import (
    ReleaseInfo,
    SemVer,
    UpdateCheckError,
    _HttpsOnlyRedirectHandler,
    _open_https,
    _select_platform_asset,
    apply_update_and_restart,
    build_windows_hotswap_script,
    download_update_asset,
    fetch_latest_release,
    get_platform_asset_candidates,
    is_version_newer,
    parse_semver,
    validate_https_url,
)


class _FakeHTTPResponse:
    """Minimal urllib response stub supporting read(), geturl(), headers, and close()."""

    def __init__(
        self,
        chunks: list[bytes] | bytes,
        final_url: str,
        headers: dict[str, str] | None = None,
        on_read: Any = None,
    ) -> None:
        if isinstance(chunks, bytes):
            self._stream = io.BytesIO(chunks)
            self._chunks: list[bytes] | None = None
        else:
            self._chunks = list(chunks)
            self._stream = None
        self._final_url = final_url
        self.headers = headers or {}
        self._on_read = on_read
        self.closed = False
        self._read_calls = 0

    def read(self, amt: int = -1) -> bytes:
        self._read_calls += 1
        if self._on_read is not None:
            self._on_read(self._read_calls)
        if self._chunks is not None:
            if not self._chunks:
                return b""
            return self._chunks.pop(0)
        assert self._stream is not None
        return self._stream.read(amt)

    def geturl(self) -> str:
        return self._final_url

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# config.py tests
# ---------------------------------------------------------------------------


def test_get_config_dir_platform_and_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.delenv("KEYFORGE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    # 1. Windows %APPDATA% resolution + fallback
    monkeypatch.setattr(sys, "platform", "win32")
    win_appdata = tmp_path / "win_roaming"
    monkeypatch.setenv("APPDATA", str(win_appdata))
    assert get_config_dir() == win_appdata / "KeyForge"

    monkeypatch.delenv("APPDATA", raising=False)
    assert get_config_dir() == fake_home / "AppData" / "Roaming" / "KeyForge"

    # 2. macOS Application Support resolution
    monkeypatch.setattr(sys, "platform", "darwin")
    assert get_config_dir() == fake_home / "Library" / "Application Support" / "KeyForge"

    # 3. POSIX XDG_CONFIG_HOME resolution + ~/.config fallback
    monkeypatch.setattr(sys, "platform", "linux")
    xdg_dir = tmp_path / "xdg_cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    assert get_config_dir() == xdg_dir / "KeyForge"

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert get_config_dir() == fake_home / ".config" / "KeyForge"

    # 4. KEYFORGE_CONFIG_DIR overrides OS defaults
    env_override = tmp_path / "env_override"
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", f"  {env_override}  ")
    assert get_config_dir() == env_override

    # 5. Explicit custom_dir overrides KEYFORGE_CONFIG_DIR and expands '~'
    explicit = tmp_path / "explicit_dir"
    assert get_config_dir(custom_dir=explicit) == explicit
    assert get_config_dir(custom_dir=Path("~/kf_custom")) == fake_home / "kf_custom"


def test_app_config_save_and_load_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig(
        schema_version=2,
        ui_mode="studio",
        simple_task="spammer",
        window_geometry="1040x710+120+80",
        start_stop_hotkey="ctrl+shift+f6",
        record_hotkey="f8",
        panic_hotkey="alt+f12",
        dry_run=True,
        check_updates_on_startup=False,
        auto_save_active_profile=False,
        last_profile_path="C:/Macros/dungeon.kfmacro.json",
        simple_clicker={
            "button": "Right",
            "click_type": "Double",
            "interval_ms": 225,
            "jitter_enabled": False,
            "loc_mode": "fixed",
            "x": 640,
            "y": 360,
        },
        simple_spammer={
            "key": "Space",
            "interval_ms": 65,
            "jitter_enabled": False,
        },
        recorder_settings={
            "record_delays": False,
            "merge_key_taps": False,
            "capture_click_coordinates": True,
        },
    )

    saved_path = save_app_config(cfg, custom_dir=tmp_path)
    assert saved_path == tmp_path / CONFIG_FILENAME
    assert saved_path.is_file()
    assert not list(tmp_path.glob("*.tmp"))

    loaded = load_app_config(custom_dir=tmp_path)
    assert loaded.schema_version == 2
    assert loaded.ui_mode == "studio"
    assert loaded.simple_task == "spammer"
    assert loaded.window_geometry == "1040x710+120+80"
    assert loaded.start_stop_hotkey == "Ctrl+Shift+F6"
    assert loaded.record_hotkey == "F8"
    assert loaded.panic_hotkey == "Alt+F12"
    assert loaded.dry_run is True
    assert loaded.check_updates_on_startup is False
    assert loaded.auto_save_active_profile is False
    assert loaded.last_profile_path == "C:/Macros/dungeon.kfmacro.json"
    assert loaded.simple_clicker == {
        "button": "Right",
        "click_type": "Double",
        "interval_ms": 225,
        "jitter_enabled": False,
        "loc_mode": "fixed",
        "x": 640,
        "y": 360,
    }
    assert loaded.simple_spammer == {
        "key": "Space",
        "interval_ms": 65,
        "jitter_enabled": False,
    }
    assert loaded.recorder_settings == {
        "record_delays": False,
        "merge_key_taps": False,
        "capture_click_coordinates": True,
    }

    # Verify KEYFORGE_CONFIG_DIR works without explicit custom_dir
    monkeypatch.setenv("KEYFORGE_CONFIG_DIR", str(tmp_path))
    loaded_env = load_app_config()
    assert loaded_env.to_dict() == loaded.to_dict()


def test_app_config_backup_rotation_and_corruption_recovery(tmp_path: Path) -> None:
    cfg_v1 = AppConfig(ui_mode="studio", start_stop_hotkey="F8", dry_run=True)
    save_app_config(cfg_v1, custom_dir=tmp_path)

    cfg_v2 = AppConfig(ui_mode="simple", start_stop_hotkey="F9", dry_run=False)
    save_app_config(cfg_v2, custom_dir=tmp_path)

    primary = tmp_path / CONFIG_FILENAME
    backup = tmp_path / CONFIG_BACKUP_FILENAME
    assert primary.is_file()
    assert backup.is_file()
    assert load_app_config(custom_dir=tmp_path).start_stop_hotkey == "F9"

    # Corrupt primary config with malformed JSON -> recovers from .bak (cfg_v1)
    primary.write_text("{corrupt json", encoding="utf-8")
    recovered = load_app_config(custom_dir=tmp_path)
    assert recovered.ui_mode == "studio"
    assert recovered.start_stop_hotkey == "F8"
    assert recovered.dry_run is True

    # Non-dict JSON, NaN constants, invalid UTF-8, and oversized primary all recover from .bak
    for bad_bytes in (
        b"[1, 2, 3]",
        b'{"ui_mode": NaN}',
        b"\xff\xfe\x00",
        b" " * (MAX_CONFIG_FILE_BYTES + 1),
    ):
        primary.write_bytes(bad_bytes)
        assert load_app_config(custom_dir=tmp_path).start_stop_hotkey == "F8"

    # Corrupt .bak as well -> dual corruption cleanly falls back to defaults
    backup.write_text("not json either", encoding="utf-8")
    fallback = load_app_config(custom_dir=tmp_path)
    assert fallback == AppConfig()

    # Legacy config.bak candidate works when config.json and config.json.bak are unusable
    (tmp_path / "config.bak").write_text(json.dumps({"ui_mode": "studio", "panic_hotkey": "F11"}), encoding="utf-8")
    legacy_recovered = load_app_config(custom_dir=tmp_path)
    assert legacy_recovered.ui_mode == "studio"
    assert legacy_recovered.panic_hotkey == "F11"


def test_app_config_corrupt_primary_does_not_overwrite_valid_bak(tmp_path: Path) -> None:
    good_cfg = AppConfig(ui_mode="studio", start_stop_hotkey="F8")
    save_app_config(good_cfg, custom_dir=tmp_path)
    save_app_config(AppConfig(ui_mode="simple", start_stop_hotkey="F9"), custom_dir=tmp_path)

    primary = tmp_path / CONFIG_FILENAME
    backup = tmp_path / CONFIG_BACKUP_FILENAME
    primary.write_text("{broken", encoding="utf-8")

    # Saving while primary is corrupt must NOT rotate the broken bytes into .bak
    save_app_config(AppConfig(ui_mode="studio", start_stop_hotkey="F10"), custom_dir=tmp_path)
    backup_data = json.loads(backup.read_text(encoding="utf-8"))
    assert backup_data["start_stop_hotkey"] == "F8"
    assert load_app_config(custom_dir=tmp_path).start_stop_hotkey == "F10"


def test_app_config_legacy_migration_and_sanitization() -> None:
    # Documented legacy & emoji UI mode labels
    assert AppConfig.from_dict({"ui_mode": "✨ Simple"}).ui_mode == "simple"
    assert AppConfig.from_dict({"ui_mode": "Easy Mode"}).ui_mode == "simple"
    assert AppConfig.from_dict({"ui_mode": "simple_profile"}).ui_mode == "simple"
    assert AppConfig.from_dict({"ui_mode": "⚡ Studio"}).ui_mode == "studio"
    assert AppConfig.from_dict({"ui_mode": "Studio"}).ui_mode == "studio"
    assert AppConfig.from_dict({"ui_mode": "  STUDIO  "}).ui_mode == "studio"
    assert AppConfig.from_dict({"ui_mode": "Pro Mode"}).ui_mode == "studio"
    assert AppConfig.from_dict({"ui_mode": "pro"}).ui_mode == "studio"
    assert AppConfig.from_dict({"ui_mode": "⚡ Pro Mode"}).ui_mode == "studio"
    assert AppConfig.from_dict({"ui_mode": "advanced"}).ui_mode == "studio"
    assert AppConfig.from_dict({"ui_mode": "Advanced Mode"}).ui_mode == "studio"
    assert AppConfig.from_dict({"ui_mode": 42}).ui_mode == "simple"

    # Legacy & current simple task labels
    assert AppConfig.from_dict({"simple_task": "Clicker"}).simple_task == "clicker"
    assert AppConfig.from_dict({"simple_task": "Auto-Clicker"}).simple_task == "clicker"
    assert AppConfig.from_dict({"simple_task": "Key Spammer"}).simple_task == "spammer"
    assert AppConfig.from_dict({"simple_task": "Combo"}).simple_task == "sequence"
    assert AppConfig.from_dict({"simple_task": "Quick Sequence"}).simple_task == "sequence"
    assert AppConfig.from_dict({"simple_task": None}).simple_task == "clicker"

    # Field sanitization & bounds clamping
    sanitized = AppConfig.from_dict(
        {
            "schema_version": -5,
            "start_stop_hotkey": "NotAValidKey",
            "dry_run": "yes",  # non-bool rejected
            "window_geometry": "bad\x00geo",
            "simple_clicker": {"button": "right", "click_type": "double", "interval_ms": 0, "x": 999_999},
            "simple_spammer": {"key": "bad\x00key", "interval_ms": 9_999_999},
            "recorder_settings": "invalid",
        }
    )
    assert sanitized.schema_version == 1
    assert sanitized.start_stop_hotkey == "F6"
    assert sanitized.dry_run is False
    assert sanitized.window_geometry is None
    assert sanitized.simple_clicker["button"] == "Right"
    assert sanitized.simple_clicker["click_type"] == "Double"
    assert sanitized.simple_clicker["interval_ms"] == 1
    assert sanitized.simple_clicker["x"] == 65_535
    assert sanitized.simple_spammer["key"] == "E"
    assert sanitized.simple_spammer["interval_ms"] == 3_600_000
    assert sanitized.recorder_settings["record_delays"] is True


def test_active_profile_backup_sha256_and_tamper_recovery(tmp_path: Path) -> None:
    prof_v1 = MacroProfile(
        name="Backup V1",
        repeat_mode=RepeatMode.COUNT,
        repeat_count=3,
        actions=[make_key_press("E"), make_wait(120)],
    )
    prof_v2 = MacroProfile(
        name="Backup V2",
        repeat_mode=RepeatMode.COUNT,
        repeat_count=9,
        actions=[make_mouse_click(MouseButton.RIGHT, ClickType.SINGLE), make_wait(80)],
    )

    primary_path = save_active_profile_backup(prof_v1, custom_dir=tmp_path)
    assert primary_path == tmp_path / ACTIVE_PROFILE_FILENAME
    save_active_profile_backup(prof_v2, custom_dir=tmp_path)

    backup_path = tmp_path / ACTIVE_PROFILE_BACKUP_FILENAME
    assert primary_path.is_file()
    assert backup_path.is_file()

    loaded = load_active_profile_backup(custom_dir=tmp_path)
    assert loaded is not None
    assert loaded.name == "Backup V2"
    assert loaded.repeat_count == 9

    # Tamper with primary payload without updating integrity_sha256 -> falls back to .bak (Backup V1)
    envelope = json.loads(primary_path.read_text(encoding="utf-8"))
    assert "integrity_sha256" in envelope
    envelope["profile"]["repeat_count"] = 999
    primary_path.write_text(json.dumps(envelope), encoding="utf-8")

    recovered = load_active_profile_backup(custom_dir=tmp_path)
    assert recovered is not None
    assert recovered.name == "Backup V1"
    assert recovered.repeat_count == 3

    # Saving while primary is tampered must NOT overwrite valid .bak with tampered data
    prof_v3 = MacroProfile(
        name="Backup V3",
        repeat_mode=RepeatMode.COUNT,
        repeat_count=4,
        actions=[make_key_press("1"), make_wait(50)],
    )
    save_active_profile_backup(prof_v3, custom_dir=tmp_path)
    primary_after_v3 = load_active_profile_backup(custom_dir=tmp_path)
    assert primary_after_v3 is not None and primary_after_v3.name == "Backup V3"
    bak_still_v1 = json.loads(backup_path.read_text(encoding="utf-8"))
    assert bak_still_v1["profile"]["name"] == "Backup V1"

    # Strip SHA-256 signature from .bak too -> unsigned/tampered dual failure returns None
    bak_env = json.loads(backup_path.read_text(encoding="utf-8"))
    bak_env.pop("integrity_sha256", None)
    backup_path.write_text(json.dumps(bak_env), encoding="utf-8")
    primary_path.unlink()
    assert load_active_profile_backup(custom_dir=tmp_path) is None


def test_unisolated_pytest_guard_prevents_real_appdata_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary_dir = tmp_path / "canary_os_appdata"
    monkeypatch.setenv("APPDATA", str(canary_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(canary_dir))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: canary_dir))
    monkeypatch.delenv("KEYFORGE_CONFIG_DIR", raising=False)

    assert _is_unisolated_pytest_run(None) is True
    assert _is_unisolated_pytest_run(tmp_path) is False

    # Even if a file existed in the OS dir, unisolated pytest calls must ignore it and never write
    save_app_config(AppConfig(ui_mode="studio", start_stop_hotkey="F9"))
    save_active_profile_backup(create_default_profile())
    assert not canary_dir.exists()

    os_cfg_dir = get_config_dir()
    os_cfg_dir.mkdir(parents=True, exist_ok=True)
    (os_cfg_dir / CONFIG_FILENAME).write_text(json.dumps({"ui_mode": "studio"}), encoding="utf-8")

    assert load_app_config() == AppConfig()
    assert load_active_profile_backup() is None


# ---------------------------------------------------------------------------
# updater.py tests
# ---------------------------------------------------------------------------


def test_semver_parsing_and_comparisons() -> None:
    assert parse_semver("v1.2.0") == SemVer(1, 2, 0)
    assert parse_semver("V1.2.0") == parse_semver("1.2.0")
    assert parse_semver("1.2.0+build.5") == parse_semver("1.2.0")
    assert parse_semver("1.2.0-rc.1") == SemVer(1, 2, 0, ("rc", "1"))

    # Prerelease and numeric precedence per SemVer 2.0.0
    assert is_version_newer("1.2.0-rc.1", "1.2.0") is True
    assert is_version_newer("1.2.0", "1.2.0-rc.1") is False
    assert is_version_newer("1.2.0-alpha.1", "1.2.0-alpha.beta") is True
    assert is_version_newer("1.2.0-1", "1.2.0-alpha") is True
    assert is_version_newer("1.2.0-beta.2", "1.2.0-beta.11") is True

    # Multi-digit minor versions
    assert is_version_newer("1.2.0", "1.10.0") is True
    assert is_version_newer("1.10.0", "1.2.0") is False
    assert is_version_newer("v1.2.0", "1.2.0+build.5") is False

    for bad in ("", "1.2", "v1.2.0-", "abc", 123):
        with pytest.raises(ValueError):
            parse_semver(bad)  # type: ignore[arg-type]


def test_platform_asset_candidates_and_arch_isolation() -> None:
    assert get_platform_asset_candidates("Windows", "AMD64") == ["KeyForge-Windows-x64.exe"]
    assert get_platform_asset_candidates("Windows", "ARM64") == [
        "KeyForge-Windows-arm64.exe",
        "KeyForge-Windows-x64.exe",
    ]
    assert get_platform_asset_candidates("Darwin", "arm64") == [
        "KeyForge-macOS-arm64",
        "KeyForge-macOS-arm64.zip",
        "KeyForge-macOS-arm64.dmg",
    ]
    assert get_platform_asset_candidates("Darwin", "x86_64") == [
        "KeyForge-macOS-x64",
        "KeyForge-macOS-x64.zip",
        "KeyForge-macOS-x64.dmg",
    ]
    assert get_platform_asset_candidates("Linux", "x86_64") == ["KeyForge-Linux-x64"]
    assert get_platform_asset_candidates("Linux", "aarch64") == ["KeyForge-Linux-arm64"]

    multi_assets = [
        {"name": "KeyForge-Windows-x64.exe", "browser_download_url": "https://example.com/win.exe"},
        {"name": "KeyForge-Windows-x64.sha256.txt", "browser_download_url": "https://example.com/win.sha256.txt"},
        {"name": "KeyForge-macOS-arm64", "browser_download_url": "https://example.com/mac-arm64"},
        {"name": "KeyForge-macOS-x64", "browser_download_url": "https://example.com/mac-x64"},
        {"name": "KeyForge-Linux-x64", "browser_download_url": "https://example.com/linux-x64"},
        {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.com/SHA256SUMS.txt"},
    ]

    mac_arm_match = _select_platform_asset(multi_assets, get_platform_asset_candidates("Darwin", "arm64"))
    assert mac_arm_match is not None and mac_arm_match["name"] == "KeyForge-macOS-arm64"

    mac_x64_match = _select_platform_asset(multi_assets, get_platform_asset_candidates("Darwin", "x86_64"))
    assert mac_x64_match is not None and mac_x64_match["name"] == "KeyForge-macOS-x64"

    linux_match = _select_platform_asset(multi_assets, get_platform_asset_candidates("Linux", "x86_64"))
    assert linux_match is not None and linux_match["name"] == "KeyForge-Linux-x64"

    # Strict arch separation: macOS arm64 must NEVER match macOS x64 binary
    only_x64_mac = [{"name": "KeyForge-macOS-x64", "browser_download_url": "https://example.com/mac-x64"}]
    assert _select_platform_asset(only_x64_mac, get_platform_asset_candidates("Darwin", "arm64")) is None

    # Token-boundary fallback matches versioned filenames without arch collision
    versioned_assets = [
        {"name": "KeyForge-v1.2.0-macOS-x64.zip", "browser_download_url": "https://example.com/mac-x64.zip"},
        {"name": "KeyForge-v1.2.0-macOS-arm64.zip", "browser_download_url": "https://example.com/mac-arm64.zip"},
    ]
    matched_ver = _select_platform_asset(versioned_assets, get_platform_asset_candidates("Darwin", "arm64"))
    assert matched_ver is not None and matched_ver["name"] == "KeyForge-v1.2.0-macOS-arm64.zip"


def test_https_url_validation_and_redirect_downgrade_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        validate_https_url("  https://api.github.com/repos/efeartn/KeyForge/releases/latest  ")
        == "https://api.github.com/repos/efeartn/KeyForge/releases/latest"
    )

    for unsafe in (
        "",
        "   ",
        "http://github.com/efeartn/KeyForge",
        "file:///C:/Windows/System32/calc.exe",
        "javascript:alert(1)",
        "ftp://example.com/KeyForge.exe",
        "https://",
        "https://github.com/efeartn\r\nX-Inject: 1",
        "https://github.com/efeartn\x00",
    ):
        with pytest.raises(UpdateCheckError) as exc_info:
            validate_https_url(unsafe)
        assert exc_info.value.reason == "unsafe_url"

    # Mock HTTPS -> HTTP redirect downgrade inside urlopen()
    downgraded_resp = _FakeHTTPResponse(b"{}", final_url="http://downgraded.example.com/release.json")
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=8.0: downgraded_resp)

    with pytest.raises(UpdateCheckError) as exc_info:
        _open_https("https://api.github.com/repos/efeartn/KeyForge/releases/latest", timeout=5.0)
    assert exc_info.value.reason == "unsafe_url"
    assert downgraded_resp.closed is True

    # Pre-flight redirect handler blocks HTTPS -> HTTP downgrade before any HTTP socket opens
    handler = _HttpsOnlyRedirectHandler()
    origin_req = urllib.request.Request("https://api.github.com/repos/efeartn/KeyForge/releases/latest")
    bad_fp = _FakeHTTPResponse(b"", final_url="https://api.github.com/repos/efeartn/KeyForge/releases/latest")
    with pytest.raises(UpdateCheckError) as redir_exc:
        handler.redirect_request(origin_req, bad_fp, 302, "Found", {}, "http://evil.example.com/payload.exe")
    assert redir_exc.value.reason == "unsafe_url"
    assert bad_fp.closed is True

    ok_fp = _FakeHTTPResponse(b"", final_url="https://api.github.com/repos/efeartn/KeyForge/releases/latest")
    redirected_req = handler.redirect_request(origin_req, ok_fp, 302, "Found", {}, "/v2/release.json")
    assert redirected_req is not None
    assert redirected_req.full_url == "https://api.github.com/v2/release.json"
    assert ok_fp.closed is False


def test_fetch_latest_release_happy_path_checksum_formats_and_prerelease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("keyforge.updater.platform.system", lambda: "Windows")
    monkeypatch.setattr("keyforge.updater.platform.machine", lambda: "AMD64")

    digest_win = "a" * 64
    digest_linux = "b" * 64
    release_payload = {
        "tag_name": "v1.3.0",
        "name": "KeyForge v1.3.0",
        "body": "Security & UI update",
        "html_url": "https://github.com/efeartn/KeyForge/releases/tag/v1.3.0",
        "published_at": "2026-11-13T12:00:00Z",
        "prerelease": False,
        "assets": [
            {
                "name": "KeyForge-Windows-x64.exe",
                "browser_download_url": "https://github.com/efeartn/KeyForge/releases/download/v1.3.0/KeyForge-Windows-x64.exe",
                "size": 12_345_678,
            },
            {
                "name": "KeyForge-Windows-x64.sha256.txt",
                "browser_download_url": "https://github.com/efeartn/KeyForge/releases/download/v1.3.0/KeyForge-Windows-x64.sha256.txt",
                "size": 92,
            },
        ],
    }

    def _fake_open_single(url: str, timeout: float = 8.0, headers: dict[str, str] | None = None):
        if url.endswith("/releases/latest"):
            return _FakeHTTPResponse(json.dumps(release_payload).encode("utf-8"), url), url
        if url.endswith("KeyForge-Windows-x64.sha256.txt"):
            return _FakeHTTPResponse(f"{digest_win.upper()}  KeyForge-Windows-x64.exe\n".encode(), url), url
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("keyforge.updater._open_https", _fake_open_single)
    rel = fetch_latest_release(current_version="1.2.0", repo="efeartn/KeyForge")
    assert rel.tag_name == "v1.3.0"
    assert rel.version == "1.3.0"
    assert rel.asset_name == "KeyForge-Windows-x64.exe"
    assert rel.asset_size == 12_345_678
    assert rel.sha256 == digest_win
    assert rel.is_update_available is True
    assert rel.prerelease is False

    # Bare single-file hash AND combined SHA256SUMS.txt fallback + prerelease handling
    combined_payload = {
        "tag_name": "v1.3.0-rc.1",
        "html_url": "https://github.com/efeartn/KeyForge/releases/tag/v1.3.0-rc.1",
        "prerelease": True,
        "assets": [
            {
                "name": "KeyForge-Windows-x64.exe",
                "browser_download_url": "https://github.com/efeartn/KeyForge/releases/download/v1.3.0-rc.1/KeyForge-Windows-x64.exe",
                "size": 999,
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://github.com/efeartn/KeyForge/releases/download/v1.3.0-rc.1/SHA256SUMS.txt",
                "size": 256,
            },
        ],
    }
    sha256sums_body = (f"{digest_linux}  KeyForge-Linux-x64\n{digest_win} *KeyForge-Windows-x64.exe\n").encode()

    def _fake_open_combined(url: str, timeout: float = 8.0, headers: dict[str, str] | None = None):
        if url.endswith("/releases/latest"):
            return _FakeHTTPResponse(json.dumps(combined_payload).encode("utf-8"), url), url
        if url.endswith("SHA256SUMS.txt"):
            return _FakeHTTPResponse(sha256sums_body, url), url
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("keyforge.updater._open_https", _fake_open_combined)
    rel_pre = fetch_latest_release(current_version="1.3.0", repo="efeartn/KeyForge")
    assert rel_pre.prerelease is True
    assert rel_pre.sha256 == digest_win
    assert rel_pre.is_update_available is False  # 1.3.0-rc.1 is older than 1.3.0

    # Verify bare digest format in single checksum file
    bare_payload = dict(
        release_payload,
        assets=[
            release_payload["assets"][0],
            {
                "name": "KeyForge-Windows-x64.exe.sha256.txt",
                "browser_download_url": "https://github.com/efeartn/KeyForge/releases/download/v1.3.0/KeyForge-Windows-x64.exe.sha256.txt",
            },
        ],
    )

    def _fake_open_bare(url: str, timeout: float = 8.0, headers: dict[str, str] | None = None):
        if url.endswith("/releases/latest"):
            return _FakeHTTPResponse(json.dumps(bare_payload).encode("utf-8"), url), url
        return _FakeHTTPResponse(f"{digest_linux}\n".encode(), url), url

    monkeypatch.setattr("keyforge.updater._open_https", _fake_open_bare)
    rel_bare = fetch_latest_release(current_version="1.2.0", repo="efeartn/KeyForge")
    assert rel_bare.sha256 == digest_linux


def test_fetch_latest_release_error_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    api_url = "https://api.github.com/repos/efeartn/KeyForge/releases/latest"

    # 1. Invalid repo slug
    with pytest.raises(UpdateCheckError) as exc:
        fetch_latest_release(repo="invalid-repo")
    assert exc.value.reason == "invalid_response"

    # 2. HTTP 429 rate limit
    def _raise_429(req: Any, timeout: float = 8.0) -> Any:
        raise urllib.error.HTTPError(api_url, 429, "Too Many Requests", {}, io.BytesIO(b"slow down"))

    monkeypatch.setattr("urllib.request.urlopen", _raise_429)
    with pytest.raises(UpdateCheckError) as exc:
        fetch_latest_release()
    assert exc.value.reason == "rate_limited"
    assert exc.value.status_code == 429

    # 3. HTTP 403 with X-RateLimit-Remaining: 0
    def _raise_403_ratelimit(req: Any, timeout: float = 8.0) -> Any:
        raise urllib.error.HTTPError(
            api_url,
            403,
            "Forbidden",
            {"X-RateLimit-Remaining": "0"},
            io.BytesIO(b"API rate limit exceeded"),
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise_403_ratelimit)
    with pytest.raises(UpdateCheckError) as exc:
        fetch_latest_release()
    assert exc.value.reason == "rate_limited"
    assert exc.value.status_code == 403

    # 4. Non-rate-limit HTTP 500 error
    def _raise_500(req: Any, timeout: float = 8.0) -> Any:
        raise urllib.error.HTTPError(api_url, 500, "Server Error", {}, io.BytesIO(b"boom"))

    monkeypatch.setattr("urllib.request.urlopen", _raise_500)
    with pytest.raises(UpdateCheckError) as exc:
        fetch_latest_release()
    assert exc.value.reason == "http_error"
    assert exc.value.status_code == 500

    # 5. Network URLError
    def _raise_urlerror(req: Any, timeout: float = 8.0) -> Any:
        raise urllib.error.URLError("DNS resolution failed")

    monkeypatch.setattr("urllib.request.urlopen", _raise_urlerror)
    with pytest.raises(UpdateCheckError) as exc:
        fetch_latest_release()
    assert exc.value.reason == "offline"

    # 6. Malformed JSON and invalid metadata shapes
    for bad_body in (b"{malformed json", b"[]", json.dumps({"tag_name": "not-semver"}).encode("utf-8")):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=8.0, b=bad_body: _FakeHTTPResponse(b, api_url),
        )
        with pytest.raises(UpdateCheckError) as exc:
            fetch_latest_release()
        assert exc.value.reason == "invalid_response"

    # 7. Unsafe http:// download URL in matched asset metadata is rejected
    monkeypatch.setattr("keyforge.updater.platform.system", lambda: "Windows")
    monkeypatch.setattr("keyforge.updater.platform.machine", lambda: "AMD64")
    unsafe_asset_payload = json.dumps(
        {
            "tag_name": "v1.3.0",
            "assets": [
                {
                    "name": "KeyForge-Windows-x64.exe",
                    "browser_download_url": "http://insecure.example.com/KeyForge-Windows-x64.exe",
                }
            ],
        }
    ).encode("utf-8")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=8.0: _FakeHTTPResponse(unsafe_asset_payload, api_url),
    )
    with pytest.raises(UpdateCheckError) as exc:
        fetch_latest_release()
    assert exc.value.reason == "unsafe_url"


def test_download_update_asset_streaming_sha256_and_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"MZ-KEYFORGE-BINARY-DATA"
    valid_sha = hashlib.sha256(payload).hexdigest()
    dl_url = "https://github.com/efeartn/KeyForge/releases/download/v1.3.0/KeyForge-Windows-x64.exe"

    # 1. Happy path: streaming chunks + progress callback + SHA-256 verification + atomic promote
    release_ok = ReleaseInfo(
        tag_name="v1.3.0",
        version="1.3.0",
        asset_name="KeyForge-Windows-x64.exe",
        download_url=dl_url,
        asset_size=len(payload),
        sha256=valid_sha,
        is_update_available=True,
    )
    chunks = [payload[:8], payload[8:16], payload[16:]]
    monkeypatch.setattr(
        "keyforge.updater._open_https",
        lambda url, timeout=30.0: (
            _FakeHTTPResponse(chunks, url, headers={"Content-Length": str(len(payload))}),
            url,
        ),
    )

    progress_events: list[tuple[int, int | None]] = []
    dest = tmp_path / "updates" / "KeyForge-Windows-x64.exe"
    result = download_update_asset(
        release=release_ok,
        target_file=dest,
        progress_callback=lambda d, t: progress_events.append((d, t)),
        chunk_size=8,
        require_sha256=True,
    )
    assert result == dest
    assert dest.read_bytes() == payload
    assert not dest.with_name(f"{dest.name}.downloading").exists()
    assert progress_events == [(8, 23), (16, 23), (23, 23)]

    # 2. Tampered download / SHA-256 mismatch rejects and cleans .downloading without overwriting dest
    release_bad_sha = ReleaseInfo(
        tag_name="v1.3.0",
        version="1.3.0",
        asset_name="KeyForge-Windows-x64.exe",
        download_url=dl_url,
        sha256="f" * 64,
        is_update_available=True,
    )
    monkeypatch.setattr(
        "keyforge.updater._open_https",
        lambda url, timeout=30.0: (_FakeHTTPResponse([b"tampered-bytes"], url), url),
    )
    with pytest.raises(UpdateCheckError) as exc:
        download_update_asset(release_bad_sha, dest, require_sha256=True)
    assert exc.value.reason == "checksum_mismatch"
    assert not dest.with_name(f"{dest.name}.downloading").exists()
    assert dest.read_bytes() == payload  # Existing valid file remained untouched

    # 3. require_sha256=True rejects missing checksum before opening network
    release_no_sha = ReleaseInfo(
        tag_name="v1.3.0",
        version="1.3.0",
        asset_name="KeyForge-Windows-x64.exe",
        download_url=dl_url,
        sha256=None,
    )
    with pytest.raises(UpdateCheckError) as exc:
        download_update_asset(release_no_sha, tmp_path / "no_sha.exe", require_sha256=True)
    assert exc.value.reason == "invalid_response"

    # 4. Mid-stream cancel_check aborts cleanly and deletes .downloading
    cancel_flag = False

    def _on_chunk_read(call_num: int) -> None:
        nonlocal cancel_flag
        if call_num >= 1:
            cancel_flag = True

    monkeypatch.setattr(
        "keyforge.updater._open_https",
        lambda url, timeout=30.0: (
            _FakeHTTPResponse([b"part1", b"part2"], url, on_read=_on_chunk_read),
            url,
        ),
    )
    cancel_dest = tmp_path / "updates" / "cancelled.exe"
    with pytest.raises(UpdateCheckError) as exc:
        download_update_asset(
            release_ok,
            cancel_dest,
            cancel_check=lambda: cancel_flag,
        )
    assert exc.value.reason == "cancelled"
    assert not cancel_dest.exists()
    assert not cancel_dest.with_name(f"{cancel_dest.name}.downloading").exists()

    # 5. Pre-start cancel_check & empty 0-byte download rejection
    with pytest.raises(UpdateCheckError) as exc:
        download_update_asset(release_ok, tmp_path / "pre_cancel.exe", cancel_check=lambda: True)
    assert exc.value.reason == "cancelled"

    monkeypatch.setattr(
        "keyforge.updater._open_https",
        lambda url, timeout=30.0: (_FakeHTTPResponse([], url), url),
    )
    empty_dest = tmp_path / "updates" / "empty.exe"
    with pytest.raises(UpdateCheckError) as exc:
        download_update_asset(release_ok, empty_dest)
    assert exc.value.reason == "invalid_response"
    assert not empty_dest.with_name(f"{empty_dest.name}.downloading").exists()


def test_build_windows_hotswap_script_and_dry_run_powershell_ast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Source (unfrozen) environment must refuse self-replacement
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    with pytest.raises(RuntimeError, match="frozen KeyForge builds"):
        apply_update_and_restart(tmp_path / "KeyForge.exe", dry_run=True)

    # 2. Input validation guards
    with pytest.raises(ValueError, match="positive integer"):
        build_windows_hotswap_script(Path("new.exe"), Path("target.exe"), app_pid=0)
    with pytest.raises(ValueError, match="Invalid target_exe"):
        build_windows_hotswap_script(Path("new.exe"), Path("bad\npath.exe"), app_pid=1234)

    # 3. Tricky paths containing spaces, single quotes, and '&'
    tricky_new = PureWindowsPath(r"C:\Users\O'Connor & Co\App Data\updates\KeyForge's & New.exe")
    tricky_target = PureWindowsPath(r"C:\Users\O'Connor & Co\KeyForge App\KeyForge.exe")
    script = build_windows_hotswap_script(tricky_new, tricky_target, app_pid=54321)  # type: ignore[arg-type]

    assert "$TargetPid = 54321" in script
    assert "'C:\\Users\\O''Connor & Co\\App Data\\updates\\KeyForge''s & New.exe'" in script
    assert "'C:\\Users\\O''Connor & Co\\KeyForge App\\KeyForge.exe'" in script
    assert "'C:\\Users\\O''Connor & Co\\KeyForge App\\KeyForge.exe.old'" in script
    assert "Get-Process -Id $TargetPid" in script
    assert "for ($Attempt = 0; $Attempt -lt 15; $Attempt++)" in script
    assert "Move-Item -LiteralPath $BackupExe -Destination $TargetExe" in script
    assert "Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir" in script

    # 4. Dry-run command generation & UTF-16LE -EncodedCommand round-trip
    tricky_dir = tmp_path / "O'Connor & Co" / "KeyForge App"
    tricky_dir.mkdir(parents=True)
    real_target = tricky_dir / "KeyForge.exe"
    real_new = tricky_dir / "KeyForge's & Update.exe"
    real_new.write_bytes(b"MZ-NEW")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(real_target))
    monkeypatch.setattr(sys, "platform", "win32")

    cmd = apply_update_and_restart(real_new, app_pid=6789, dry_run=True)
    assert "-EncodedCommand" in cmd
    decoded_script = base64.b64decode(cmd[-1]).decode("utf-16-le")
    expected_script = build_windows_hotswap_script(real_new, real_target, app_pid=6789)
    assert decoded_script == expected_script

    # POSIX dry-run returns relaunch command cleanly without modifying files
    monkeypatch.setattr(sys, "platform", "linux")
    assert apply_update_and_restart(real_new, app_pid=6789, dry_run=True) == [str(real_target)]

    # 5. On Windows, parse the generated PowerShell script with System.Management.Automation.Language.Parser
    if os.name == "nt":
        ps_check = (
            "$errors = $null; $tokens = $null; "
            "$ast = [System.Management.Automation.Language.Parser]::ParseInput("
            "$env:KF_TEST_PS_SCRIPT, [ref]$tokens, [ref]$errors); "
            "if ($errors.Count -ne 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 2 }; "
            "$vals = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] "
            "-and $n.Left.VariablePath.UserPath -in @('NewBinary','TargetExe','BackupExe') }, $true) | "
            'ForEach-Object { "$($_.Left.VariablePath.UserPath)=$($_.Right.Expression.Value)" }; '
            '$vals -join "`n"'
        )
        env = dict(os.environ, KF_TEST_PS_SCRIPT=decoded_script)
        proc = subprocess.run(  # noqa: S603
            [cmd[0], "-NoProfile", "-NonInteractive", "-Command", ps_check],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert proc.returncode == 0, f"PowerShell AST parse failed: {proc.stderr}"
        parsed_vars = dict(line.split("=", 1) for line in proc.stdout.strip().splitlines() if "=" in line)
        assert parsed_vars == {
            "NewBinary": str(real_new),
            "TargetExe": str(real_target),
            "BackupExe": f"{real_target}.old",
        }
