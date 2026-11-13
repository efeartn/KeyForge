"""Secure cross-platform GitHub release checker, SHA-256 updater, and hot-swap installer."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from keyforge import __version__

DEFAULT_REPO = "efeartn/KeyForge"
USER_AGENT = f"KeyForge/{__version__}"
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_CHECKSUM_FILE_BYTES = 1 * 1024 * 1024  # 1 MiB

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SEMVER_PATTERN = re.compile(
    r"^[vV]?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_CHECKSUM_LINE_PATTERN = re.compile(r"^(?P<digest>[0-9a-fA-F]{64})(?:\s+\*?(?P<filename>.+))?$")
_NON_BINARY_SUFFIXES = (".txt", ".sha256", ".sha512", ".sig", ".asc", ".json", ".yml", ".yaml")


class UpdateCheckError(RuntimeError):
    """Raised when checking for, downloading, or verifying a KeyForge release fails."""

    def __init__(self, message: str, reason: str = "http_error", status_code: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


class SemVer(NamedTuple):
    """Semantic Version 2.0.0 representation with accurate prerelease precedence."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    def _sort_key(self) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
        pre_tokens = tuple((0, int(part)) if part.isdigit() else (1, part) for part in self.prerelease)
        # Normal releases (empty prerelease) have higher precedence than any prerelease.
        release_rank = 1 if not self.prerelease else 0
        return (self.major, self.minor, self.patch, release_rank, pre_tokens)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._sort_key() == other._sort_key()

    def __ne__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._sort_key() != other._sort_key()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._sort_key() < other._sort_key()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._sort_key() <= other._sort_key()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._sort_key() > other._sort_key()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._sort_key() >= other._sort_key()

    def __hash__(self) -> int:
        return hash(self._sort_key())


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """Metadata for the latest GitHub release and its matched platform binary."""

    tag_name: str
    version: str
    name: str = ""
    body: str = ""
    html_url: str = ""
    published_at: str = ""
    prerelease: bool = False
    asset_name: str | None = None
    download_url: str | None = None
    asset_size: int | None = None
    sha256: str | None = None
    checksum_url: str | None = None
    is_update_available: bool = False


def parse_semver(version_str: str) -> SemVer:
    """Parse a semantic version string (with optional leading 'v' and prerelease/build metadata)."""
    if not isinstance(version_str, str):
        raise ValueError(f"Version must be a string, got {type(version_str).__name__}.")
    match = _SEMVER_PATTERN.match(version_str.strip())
    if not match:
        raise ValueError(f"Invalid semantic version: {version_str!r}")
    pre_raw = match.group("pre")
    pre_parts = tuple(pre_raw.split(".")) if pre_raw else ()
    return SemVer(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=pre_parts,
    )


def is_version_newer(current: str, candidate: str) -> bool:
    """Return True if ``candidate`` is strictly newer than ``current`` per SemVer 2.0.0."""
    return parse_semver(candidate) > parse_semver(current)


def validate_https_url(url: str) -> str:
    """Ensure a URL uses the HTTPS scheme with a non-empty network location."""
    if not isinstance(url, str) or not url.strip():
        raise UpdateCheckError("Update URL must be a non-empty HTTPS URL.", reason="unsafe_url")
    cleaned = url.strip()
    if any(ch in cleaned for ch in ("\x00", "\r", "\n")):
        raise UpdateCheckError("Update URL contains invalid control characters.", reason="unsafe_url")
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise UpdateCheckError(f"Refusing non-HTTPS update URL: {cleaned!r}", reason="unsafe_url")
    return cleaned


def _normalize_arch(machine: str) -> str:
    m = machine.strip().lower()
    if any(tok in m for tok in ("arm64", "aarch64", "arm")):
        return "arm64"
    return "x64"


def get_platform_asset_candidates(system: str | None = None, machine: str | None = None) -> list[str]:
    """Return ordered release asset filenames matching the current OS and CPU architecture.

    Strictly separates ``macOS-arm64`` vs ``macOS-x64``, ``Windows-x64``, and ``Linux-x64``
    to prevent cross-architecture or substring collisions.
    """
    sys_name = (system if system is not None else platform.system()).strip().lower()
    arch = _normalize_arch(machine if machine is not None else platform.machine())

    if sys_name == "windows":
        if arch == "arm64":
            return ["KeyForge-Windows-arm64.exe", "KeyForge-Windows-x64.exe"]
        return ["KeyForge-Windows-x64.exe"]

    if sys_name == "darwin":
        if arch == "arm64":
            return ["KeyForge-macOS-arm64", "KeyForge-macOS-arm64.zip", "KeyForge-macOS-arm64.dmg"]
        return ["KeyForge-macOS-x64", "KeyForge-macOS-x64.zip", "KeyForge-macOS-x64.dmg"]

    if arch == "arm64":
        return ["KeyForge-Linux-arm64"]
    return ["KeyForge-Linux-x64"]


def _is_binary_asset_name(name: str) -> bool:
    lowered = name.strip().lower()
    if not lowered or lowered == "sha256sums.txt":
        return False
    return not any(lowered.endswith(suffix) for suffix in _NON_BINARY_SUFFIXES)


def _tokenize_asset_name(name: str) -> set[str]:
    stem = Path(name).stem.lower()
    return {tok for tok in re.split(r"[-_.]+", stem) if tok}


def _select_platform_asset(
    assets: list[dict[str, Any]],
    candidates: list[str],
) -> dict[str, Any] | None:
    """Select the best matching binary asset without substring collision."""
    binary_assets: list[dict[str, Any]] = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and _is_binary_asset_name(name):
            binary_assets.append(item)

    # Pass 1: exact case-insensitive filename match in candidate priority order
    by_lower_name = {str(item["name"]).strip().lower(): item for item in binary_assets}
    for candidate in candidates:
        matched = by_lower_name.get(candidate.lower())
        if matched is not None:
            return matched

    # Pass 2: token-boundary fallback (e.g. versioned release names) without arch/OS collisions
    for candidate in candidates:
        cand_lower = candidate.lower()
        cand_tokens = _tokenize_asset_name(candidate)
        cand_suffix = Path(cand_lower).suffix

        want_os = "windows" if "windows" in cand_tokens else ("macos" if "macos" in cand_tokens else "linux")
        want_arch = "arm64" if "arm64" in cand_tokens else "x64"
        os_aliases = {"macos": {"macos", "darwin"}, "windows": {"windows", "win"}, "linux": {"linux"}}
        arch_aliases = {"arm64": {"arm64", "aarch64"}, "x64": {"x64", "x86_64", "amd64"}}
        forbidden_arch = arch_aliases["x64"] if want_arch == "arm64" else arch_aliases["arm64"]
        forbidden_os = set().union(*(v for k, v in os_aliases.items() if k != want_os))

        for item in binary_assets:
            asset_name = str(item["name"]).strip()
            asset_lower = asset_name.lower()
            if cand_suffix and not asset_lower.endswith(cand_suffix):
                continue
            if not cand_suffix and asset_lower.endswith(".exe"):
                continue
            tokens = _tokenize_asset_name(asset_name)
            if tokens & forbidden_os or tokens & forbidden_arch:
                continue
            if (tokens & os_aliases[want_os]) and (tokens & arch_aliases[want_arch]):
                return item

    return None


@contextlib.contextmanager
def _close_response(resp: Any) -> Any:
    try:
        yield resp
    finally:
        close_fn = getattr(resp, "close", None)
        if callable(close_fn):
            close_fn()


_ORIG_URLOPEN = urllib.request.urlopen


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects to non-HTTPS URLs before opening a plaintext connection."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        resolved_url = urllib.parse.urljoin(req.full_url, newurl)
        try:
            safe_url = validate_https_url(resolved_url)
        except UpdateCheckError:
            with contextlib.suppress(Exception):
                fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


_HTTPS_ONLY_OPENER = urllib.request.build_opener(_HttpsOnlyRedirectHandler())


def _open_https(url: str, timeout: float, headers: dict[str, str] | None = None) -> tuple[Any, str]:
    """Open an HTTPS request and verify all redirects and final URLs remain HTTPS."""
    safe_url = validate_https_url(url)
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(safe_url, headers=req_headers)  # noqa: S310

    try:
        if urllib.request.urlopen is not _ORIG_URLOPEN:
            resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        else:
            resp = _HTTPS_ONLY_OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body_hint = ""
        if exc.fp is not None:
            try:
                body_hint = exc.read(4096).decode("utf-8", errors="ignore").lower()
            except OSError:
                body_hint = ""
        remaining = ""
        if exc.headers is not None:
            remaining = str(exc.headers.get("X-RateLimit-Remaining", "")).strip()
        is_rate_limited = exc.code == 429 or (
            exc.code == 403 and (remaining == "0" or "rate limit" in body_hint or "rate limit" in str(exc).lower())
        )
        if is_rate_limited:
            raise UpdateCheckError(
                f"GitHub API rate limit exceeded (HTTP {exc.code}). Please try again later.",
                reason="rate_limited",
                status_code=exc.code,
            ) from exc
        raise UpdateCheckError(
            f"HTTP {exc.code} while requesting {safe_url}: {exc.reason}",
            reason="http_error",
            status_code=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateCheckError(
            f"Network error while contacting update server: {exc}",
            reason="offline",
        ) from exc

    geturl_fn = getattr(resp, "geturl", None)
    final_url = geturl_fn() if callable(geturl_fn) else safe_url
    if isinstance(final_url, str):
        try:
            validate_https_url(final_url)
        except UpdateCheckError:
            resp.close()
            raise
    else:
        final_url = safe_url

    return resp, final_url


def _read_bounded_bytes(resp: Any, max_bytes: int) -> bytes:
    try:
        data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateCheckError(f"Network error while reading response: {exc}", reason="offline") from exc
    if not isinstance(data, (bytes, bytearray)):
        raise UpdateCheckError("Unexpected non-bytes response payload.", reason="invalid_response")
    if len(data) > max_bytes:
        raise UpdateCheckError(f"Response exceeds maximum size of {max_bytes} bytes.", reason="invalid_response")
    return bytes(data)


def _extract_sha256_from_checksum_text(text: str, asset_name: str, is_combined_file: bool) -> str | None:
    target_names = {asset_name.lower()}
    stem_lower = Path(asset_name).stem.lower()
    if stem_lower:
        target_names.add(stem_lower)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _CHECKSUM_LINE_PATTERN.match(line)
        if not match:
            continue
        digest = match.group("digest").lower()
        filename_part = match.group("filename")
        if filename_part:
            entry_name = Path(filename_part.strip()).name.lower()
            if entry_name == asset_name.lower() or (not is_combined_file and entry_name in target_names):
                return digest
        elif not is_combined_file:
            return digest

    return None


def _resolve_asset_sha256(
    assets: list[dict[str, Any]],
    asset_name: str,
    timeout: float,
) -> tuple[str | None, str | None]:
    """Fetch and parse SHA-256 digest from ``<asset>.sha256.txt`` or ``SHA256SUMS.txt``."""
    by_lower_name: dict[str, dict[str, Any]] = {}
    for item in assets:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            by_lower_name[item["name"].strip().lower()] = item

    stem = Path(asset_name).stem
    checksum_candidates: list[tuple[str, bool]] = [
        (f"{asset_name.lower()}.sha256.txt", False),
        (f"{stem.lower()}.sha256.txt", False),
        (f"{asset_name.lower()}.sha256", False),
        (f"{stem.lower()}.sha256", False),
        ("sha256sums.txt", True),
    ]

    for cand_name, is_combined in checksum_candidates:
        entry = by_lower_name.get(cand_name)
        if entry is None:
            continue
        url_raw = entry.get("browser_download_url")
        if not isinstance(url_raw, str):
            continue
        safe_url = validate_https_url(url_raw)
        try:
            resp, _ = _open_https(safe_url, timeout=timeout)
            with _close_response(resp):
                raw_bytes = _read_bounded_bytes(resp, MAX_CHECKSUM_FILE_BYTES)
            text = raw_bytes.decode("utf-8", errors="strict")
        except UpdateCheckError as exc:
            if exc.reason == "unsafe_url":
                raise
            continue
        except UnicodeDecodeError:
            continue

        digest = _extract_sha256_from_checksum_text(text, asset_name=asset_name, is_combined_file=is_combined)
        if digest is not None:
            return digest, safe_url

    return None, None


def fetch_latest_release(
    current_version: str = __version__,
    repo: str = DEFAULT_REPO,
    timeout: float = 8.0,
) -> ReleaseInfo:
    """Query GitHub Releases over HTTPS, select the matching binary asset, and resolve its SHA-256."""
    if not isinstance(repo, str) or not _REPO_PATTERN.fullmatch(repo.strip()):
        raise UpdateCheckError(
            f"Invalid GitHub repository identifier: {repo!r}. Expected 'owner/repo'.",
            reason="invalid_response",
        )
    repo_clean = repo.strip()
    parse_semver(current_version)

    api_url = f"https://api.github.com/repos/{repo_clean}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    resp, _ = _open_https(api_url, timeout=timeout, headers=headers)
    with _close_response(resp):
        raw_payload = _read_bounded_bytes(resp, MAX_API_RESPONSE_BYTES)

    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError(f"Malformed JSON in GitHub release response: {exc}", reason="invalid_response") from exc

    if not isinstance(payload, dict):
        raise UpdateCheckError("GitHub release metadata must be a JSON object.", reason="invalid_response")

    tag_name = payload.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name.strip():
        raise UpdateCheckError("Release metadata missing valid 'tag_name'.", reason="invalid_response")
    tag_clean = tag_name.strip()

    try:
        parse_semver(tag_clean)
    except ValueError as exc:
        raise UpdateCheckError(
            f"Release tag_name is not valid SemVer: {tag_clean!r}",
            reason="invalid_response",
        ) from exc

    version_str = tag_clean.lstrip("vV")
    newer = is_version_newer(current_version, tag_clean)

    html_url_raw = payload.get("html_url") or ""
    if not isinstance(html_url_raw, str):
        raise UpdateCheckError("Invalid 'html_url' field in release metadata.", reason="invalid_response")
    html_url = validate_https_url(html_url_raw) if html_url_raw.strip() else ""

    assets_raw = payload.get("assets", [])
    if not isinstance(assets_raw, list):
        raise UpdateCheckError("Release 'assets' field must be a list.", reason="invalid_response")

    candidates = get_platform_asset_candidates()
    matched_asset = _select_platform_asset(assets_raw, candidates)

    asset_name: str | None = None
    download_url: str | None = None
    asset_size: int | None = None
    sha256: str | None = None
    checksum_url: str | None = None

    if matched_asset is not None:
        asset_name = str(matched_asset["name"]).strip()
        raw_dl_url = matched_asset.get("browser_download_url")
        if not isinstance(raw_dl_url, str):
            raise UpdateCheckError(
                f"Selected asset {asset_name!r} is missing 'browser_download_url'.",
                reason="invalid_response",
            )
        download_url = validate_https_url(raw_dl_url)
        raw_size = matched_asset.get("size")
        if isinstance(raw_size, int) and not isinstance(raw_size, bool) and raw_size >= 0:
            asset_size = raw_size
        sha256, checksum_url = _resolve_asset_sha256(assets_raw, asset_name=asset_name, timeout=timeout)

    name_val = payload.get("name")
    body_val = payload.get("body")
    published_val = payload.get("published_at")

    return ReleaseInfo(
        tag_name=tag_clean,
        version=version_str,
        name=str(name_val).strip() if isinstance(name_val, str) and name_val.strip() else tag_clean,
        body=str(body_val) if isinstance(body_val, str) else "",
        html_url=html_url,
        published_at=str(published_val) if isinstance(published_val, str) else "",
        prerelease=bool(payload.get("prerelease", False)),
        asset_name=asset_name,
        download_url=download_url,
        asset_size=asset_size,
        sha256=sha256,
        checksum_url=checksum_url,
        is_update_available=newer,
    )


def download_update_asset(
    release: ReleaseInfo,
    target_file: Path | str,
    progress_callback: Callable[[int, int | None], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    chunk_size: int = 65536,
    timeout: float = 30.0,
    require_sha256: bool = False,
) -> Path:
    """Stream-download a release asset over HTTPS, verify SHA-256, and atomically place ``target_file``."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer.")

    if not release.download_url:
        raise UpdateCheckError("Release does not contain a download URL for this platform.", reason="invalid_response")

    safe_url = validate_https_url(release.download_url)

    expected_sha = release.sha256.strip().lower() if isinstance(release.sha256, str) else None
    if require_sha256 and not expected_sha:
        raise UpdateCheckError(
            "SHA-256 verification is required, but the release provided no SHA-256 checksum.",
            reason="invalid_response",
        )
    if expected_sha is not None and not _SHA256_HEX_PATTERN.fullmatch(expected_sha):
        raise UpdateCheckError(f"Malformed expected SHA-256 checksum: {expected_sha!r}", reason="invalid_response")

    if cancel_check is not None and cancel_check():
        raise UpdateCheckError("Update download cancelled before starting.", reason="cancelled")

    dest = Path(target_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_file = dest.with_name(f"{dest.name}.downloading")
    temp_file.unlink(missing_ok=True)

    try:
        resp, _ = _open_https(safe_url, timeout=timeout)
        with _close_response(resp):
            total_bytes: int | None = None
            headers = getattr(resp, "headers", None)
            if headers is not None:
                cl_raw = headers.get("Content-Length")
                if isinstance(cl_raw, str) and cl_raw.strip().isdigit():
                    parsed_len = int(cl_raw.strip())
                    if parsed_len > 0:
                        total_bytes = parsed_len
            if total_bytes is None and release.asset_size and release.asset_size > 0:
                total_bytes = release.asset_size

            hasher = hashlib.sha256()
            downloaded = 0

            with temp_file.open("wb") as handle:
                while True:
                    if cancel_check is not None and cancel_check():
                        raise UpdateCheckError("Update download cancelled by user.", reason="cancelled")
                    try:
                        chunk = resp.read(chunk_size)
                    except (urllib.error.URLError, TimeoutError, OSError) as exc:
                        raise UpdateCheckError(
                            f"Network error during update download: {exc}",
                            reason="offline",
                        ) from exc
                    if not chunk:
                        break
                    handle.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total_bytes)

                if cancel_check is not None and cancel_check():
                    raise UpdateCheckError("Update download cancelled by user.", reason="cancelled")
                handle.flush()
                os.fsync(handle.fileno())

        if downloaded == 0:
            raise UpdateCheckError("Downloaded update binary is empty (0 bytes).", reason="invalid_response")

        if expected_sha is not None:
            actual_sha = hasher.hexdigest().lower()
            if not hmac.compare_digest(actual_sha, expected_sha):
                raise UpdateCheckError(
                    f"SHA-256 verification failed! Expected {expected_sha}, got {actual_sha}.",
                    reason="checksum_mismatch",
                )

        os.replace(temp_file, dest)
        return dest
    except BaseException:
        temp_file.unlink(missing_ok=True)
        raise


def _ps_single_quote(value: str) -> str:
    """Safely encode a literal string for PowerShell single-quoted syntax."""
    return "'" + value.replace("'", "''") + "'"


def _validate_hotswap_path(path: Path | str, label: str) -> str:
    raw = str(path)
    if not raw.strip() or any(ch in raw for ch in ("\x00", "\r", "\n")):
        raise ValueError(f"Invalid {label} path for hot-swap.")
    return raw


def build_windows_hotswap_script(new_binary: Path, target_exe: Path, app_pid: int) -> str:
    """Build an injection-proof PowerShell hot-swap script for Windows executable replacement."""
    if isinstance(app_pid, bool) or not isinstance(app_pid, int) or app_pid <= 0:
        raise ValueError(f"app_pid must be a positive integer, got {app_pid!r}.")

    new_bin_str = _validate_hotswap_path(new_binary, "new_binary")
    target_exe_str = _validate_hotswap_path(target_exe, "target_exe")
    backup_exe_str = f"{target_exe_str}.old"

    ps_new = _ps_single_quote(new_bin_str)
    ps_target = _ps_single_quote(target_exe_str)
    ps_backup = _ps_single_quote(backup_exe_str)

    return "\n".join(
        [
            f"$TargetPid = {app_pid}",
            f"$NewBinary = {ps_new}",
            f"$TargetExe = {ps_target}",
            f"$BackupExe = {ps_backup}",
            "$WorkDir = Split-Path -LiteralPath $TargetExe -Parent",
            "",
            "$Deadline = (Get-Date).AddSeconds(15)",
            "while ((Get-Date) -lt $Deadline) {",
            "    $proc = Get-Process -Id $TargetPid -ErrorAction SilentlyContinue",
            "    if ($null -eq $proc) { break }",
            "    Start-Sleep -Milliseconds 200",
            "}",
            "",
            "if (Test-Path -LiteralPath $BackupExe) {",
            "    Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue",
            "}",
            "",
            "$Swapped = $false",
            "for ($Attempt = 0; $Attempt -lt 15; $Attempt++) {",
            "    try {",
            "        if (Test-Path -LiteralPath $TargetExe) {",
            "            if (-not (Test-Path -LiteralPath $BackupExe)) {",
            "                Move-Item -LiteralPath $TargetExe -Destination $BackupExe -Force -ErrorAction Stop",
            "            } else {",
            "                Remove-Item -LiteralPath $TargetExe -Force -ErrorAction Stop",
            "            }",
            "        }",
            "        Move-Item -LiteralPath $NewBinary -Destination $TargetExe -Force -ErrorAction Stop",
            "        $Swapped = $true",
            "        break",
            "    } catch {",
            "        Start-Sleep -Milliseconds 300",
            "    }",
            "}",
            "",
            "if (-not $Swapped) {",
            "    if ((-not (Test-Path -LiteralPath $TargetExe)) -and (Test-Path -LiteralPath $BackupExe)) {",
            "        Move-Item -LiteralPath $BackupExe -Destination $TargetExe -Force -ErrorAction SilentlyContinue",
            "    }",
            "    exit 1",
            "}",
            "",
            "if (Test-Path -LiteralPath $BackupExe) {",
            "    Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue",
            "}",
            "",
            "Start-Process -FilePath $TargetExe -WorkingDirectory $WorkDir",
        ]
    )


def _resolve_powershell_executable() -> str:
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(sys_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("powershell.exe") or shutil.which("powershell") or "powershell.exe"


def apply_update_and_restart(
    new_binary: Path,
    app_pid: int | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Replace the running frozen KeyForge executable with ``new_binary`` and relaunch cleanly.

    Raises ``RuntimeError`` when invoked from an unfrozen Python source environment.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "Self-update hot-swap is only available in packaged/frozen KeyForge builds "
            "(cannot self-replace Python interpreter in source mode)."
        )

    new_bin_path = Path(new_binary)
    if not new_bin_path.is_file():
        raise FileNotFoundError(f"Downloaded update binary does not exist: {new_bin_path}")

    target_exe = Path(sys.executable)
    pid = os.getpid() if app_pid is None else app_pid

    if sys.platform == "win32":
        script = build_windows_hotswap_script(new_bin_path, target_exe, pid)
        encoded_cmd = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        cmd = [
            _resolve_powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded_cmd,
        ]
        if not dry_run:
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(  # noqa: S603
                cmd,
                creationflags=flags,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return cmd

    # POSIX atomic same-filesystem swap + relaunch
    relaunch_cmd = [str(target_exe)]
    if dry_run:
        return relaunch_cmd

    target_exe.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_str = tempfile.mkstemp(
        prefix=f".{target_exe.name}.",
        suffix=".staging",
        dir=str(target_exe.parent),
    )
    os.close(fd)
    staged_path = Path(staged_str)
    try:
        shutil.copy2(new_bin_path, staged_path)
        staged_path.chmod(0o755)
        os.replace(staged_path, target_exe)
        target_exe.chmod(0o755)
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise

    subprocess.Popen(  # noqa: S603
        relaunch_cmd,
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return relaunch_cmd
