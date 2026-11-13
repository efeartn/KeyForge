"""Minimalist Apple-style Software Update modal dialog for KeyForge."""

from __future__ import annotations

import contextlib
import queue
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

import customtkinter as ctk

from keyforge import __version__
from keyforge.config import get_config_dir
from keyforge.ui.theme import (
    ACCENT_PRIMARY,
    ACCENT_SUCCESS,
    ACCENT_WARNING,
    BADGE_CRIT_BG,
    BADGE_CRIT_FG,
    BADGE_OK_BG,
    BADGE_OK_FG,
    BADGE_WARN_BG,
    BADGE_WARN_FG,
    BG_CARD,
    BG_ELEVATED,
    BG_INPUT,
    BG_PANEL,
    BORDER_SUBTLE,
    BTN_NEUTRAL,
    BTN_NEUTRAL_HOVER,
    CTA_SUCCESS,
    CTA_SUCCESS_HOVER,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    apply_window_icon,
    center_window_on_parent,
    get_fonts,
    load_brand_ctk_image,
)
from keyforge.updater import (
    DEFAULT_REPO,
    ReleaseInfo,
    UpdateCheckError,
    apply_update_and_restart,
    download_update_asset,
    fetch_latest_release,
    validate_https_url,
)


def _format_bytes(size_bytes: int | None) -> str:
    if size_bytes is None or size_bytes <= 0:
        return "Size unknown"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


class UpdateDialog(ctk.CTkToplevel):
    """Modal Software Update sheet with GitHub check, SHA-256 verification, and safe restart."""

    def __init__(
        self,
        master: ctk.CTk,
        initial_release: ReleaseInfo | None = None,
        on_release_checked: Callable[[ReleaseInfo], None] | None = None,
        on_before_restart: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title("Software Update — KeyForge")
        self.resizable(False, False)
        self.configure(fg_color=BG_PANEL)

        self._master_app = master
        self._fonts = get_fonts(master)
        self._release: ReleaseInfo | None = initial_release
        self._on_release_checked = on_release_checked
        self._on_before_restart = on_before_restart

        self._closed = False
        self._cancel_requested = False
        self._is_checking = False
        self._is_downloading = False
        self._downloaded_path: Path | None = None
        self._ui_queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self._poll_after_id: str | None = None

        center_window_on_parent(self, master, 540, 500)
        apply_window_icon(self)

        self.transient(master)
        with contextlib.suppress(Exception):
            self.grab_set()

        self.bind("<Escape>", lambda _e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()

        if self._release is not None:
            self._render_release(self._release)
        else:
            self.check_for_updates()

    # ------------------------------------------------------------------
    # Main-Thread Queue Polling (Zero Cross-Thread Tk/Tcl Calls)
    # ------------------------------------------------------------------

    def _ensure_queue_polling(self) -> None:
        """Start 40ms main-thread polling of worker events while active."""
        if self._closed or self._poll_after_id is not None:
            return
        with contextlib.suppress(Exception):
            if self.winfo_exists():
                self._poll_after_id = self.after(40, self._poll_ui_queue)

    def _poll_ui_queue(self) -> None:
        self._poll_after_id = None
        if self._closed:
            return
        with contextlib.suppress(Exception):
            if not self.winfo_exists():
                return
        self._drain_ui_queue()
        if not self._closed and (self._is_checking or self._is_downloading):
            with contextlib.suppress(Exception):
                if self.winfo_exists():
                    self._poll_after_id = self.after(40, self._poll_ui_queue)

    def _drain_ui_queue(self) -> None:
        """Process queued worker updates strictly on the Tk main thread."""
        latest_progress: tuple[int, int | None] | None = None
        events: list[tuple[str, Any]] = []
        while True:
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "download_progress":
                latest_progress = payload
            else:
                if latest_progress is not None:
                    events.append(("download_progress", latest_progress))
                    latest_progress = None
                events.append((kind, payload))
        if latest_progress is not None:
            events.append(("download_progress", latest_progress))

        for kind, payload in events:
            if self._closed:
                break
            if kind == "check_success":
                self._on_check_success(payload)
            elif kind == "check_error":
                self._on_check_error(payload)
            elif kind == "download_progress":
                downloaded, total = payload
                self._update_download_progress(downloaded, total)
            elif kind == "download_complete":
                self._on_download_complete(payload)
            elif kind == "download_error":
                message, cancelled = payload
                self._on_download_error(message, cancelled)

    def _cancel_timers(self) -> None:
        for attr in ("_poll_after_id", "_kf_icon_after_id"):
            aid = getattr(self, attr, None)
            if aid is not None:
                with contextlib.suppress(Exception):
                    self.after_cancel(aid)
                setattr(self, attr, None)

    # ------------------------------------------------------------------
    # UI Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Header Row
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(16, 10))

        self._icon_img = load_brand_ctk_image((34, 34))
        if self._icon_img is not None:
            ctk.CTkLabel(hdr, text="", image=self._icon_img).pack(side="left", padx=(0, 10))

        title_col = ctk.CTkFrame(hdr, fg_color="transparent")
        title_col.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            title_col,
            text="Software Update",
            font=self._fonts["title"],
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_col,
            text=f"Installed: KeyForge v{__version__}",
            font=self._fonts["small"],
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(anchor="w")

        self.check_again_btn = ctk.CTkButton(
            hdr,
            text="Check Again",
            width=104,
            height=30,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self._fonts["small_bold"],
            command=self.check_for_updates,
        )
        self.check_again_btn.pack(side="right", padx=(8, 0))

        # Version & Verification Card
        card = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        card.pack(fill="x", padx=20, pady=(0, 10))

        top_card = ctk.CTkFrame(card, fg_color="transparent")
        top_card.pack(fill="x", padx=14, pady=(12, 6))

        self.status_title_lbl = ctk.CTkLabel(
            top_card,
            text="Checking GitHub Releases...",
            font=self._fonts["heading"],
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.status_title_lbl.pack(side="left", fill="x", expand=True)

        self.sha_pill = ctk.CTkLabel(
            top_card,
            text="SHA-256 Pending",
            font=self._fonts["caption_bold"],
            fg_color=BG_ELEVATED,
            text_color=TEXT_SECONDARY,
            corner_radius=6,
            padx=8,
            pady=3,
        )
        self.sha_pill.pack(side="right", padx=(8, 0))

        self.meta_lbl = ctk.CTkLabel(
            card,
            text="Contacting api.github.com...",
            font=self._fonts["small"],
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self.meta_lbl.pack(fill="x", padx=14, pady=(0, 12))

        # Release Notes Header + Fallback Link
        notes_hdr = ctk.CTkFrame(self, fg_color="transparent")
        notes_hdr.pack(fill="x", padx=20, pady=(2, 4))

        ctk.CTkLabel(
            notes_hdr,
            text="Release Notes",
            font=self._fonts["small_bold"],
            text_color=TEXT_SECONDARY,
        ).pack(side="left")

        self.release_link_btn = ctk.CTkButton(
            notes_hdr,
            text="Release Page ↗",
            width=108,
            height=24,
            fg_color="transparent",
            hover_color=BTN_NEUTRAL,
            text_color=ACCENT_PRIMARY,
            font=self._fonts["small_bold"],
            command=self._open_release_page,
        )
        self.release_link_btn.pack(side="right")

        self.notes_box = ctk.CTkTextbox(
            self,
            fg_color=BG_INPUT,
            border_width=1,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
            font=self._fonts["body"],
            corner_radius=10,
            wrap="word",
        )
        self.notes_box.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self._set_notes_text("Checking for the latest KeyForge release...")

        # Progress Area
        prog_frame = ctk.CTkFrame(self, fg_color="transparent")
        prog_frame.pack(fill="x", padx=20, pady=(0, 8))

        self.progress_lbl = ctk.CTkLabel(
            prog_frame,
            text="Ready",
            font=self._fonts["small"],
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self.progress_lbl.pack(fill="x", pady=(0, 4))

        self.progress_bar = ctk.CTkProgressBar(
            prog_frame,
            height=7,
            fg_color=BG_INPUT,
            progress_color=ACCENT_PRIMARY,
        )
        self.progress_bar.set(0.0)
        self.progress_bar.pack(fill="x")

        # Footer Buttons
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(4, 16))

        self.secondary_btn = ctk.CTkButton(
            footer,
            text="Close",
            width=116,
            height=34,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self._fonts["body_bold"],
            command=self._on_secondary_clicked,
        )
        self.secondary_btn.pack(side="left")

        self.install_btn = ctk.CTkButton(
            footer,
            text="Download & Restart",
            width=170,
            height=34,
            fg_color=CTA_SUCCESS,
            hover_color=CTA_SUCCESS_HOVER,
            font=self._fonts["body_bold"],
            state="disabled",
            command=self.start_download_and_install,
        )
        self.install_btn.pack(side="right")

    def _set_notes_text(self, text: str) -> None:
        self.notes_box.configure(state="normal")
        self.notes_box.delete("1.0", "end")
        self.notes_box.insert("1.0", text)
        self.notes_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Update Check & Rendering
    # ------------------------------------------------------------------

    def check_for_updates(self) -> None:
        """Query GitHub Releases asynchronously without blocking the UI thread."""
        if self._is_checking or self._is_downloading or self._closed:
            return
        self._is_checking = True
        self.check_again_btn.configure(state="disabled", text="Checking...")
        self.install_btn.configure(state="disabled")
        self.status_title_lbl.configure(text="Checking for Updates...", text_color=TEXT_PRIMARY)
        self.meta_lbl.configure(text=f"Querying github.com/{DEFAULT_REPO}...")
        self.sha_pill.configure(text="Checking...", fg_color=BG_ELEVATED, text_color=TEXT_SECONDARY)
        self.progress_lbl.configure(text="Contacting GitHub Releases...", text_color=TEXT_SECONDARY)
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()

        self._ensure_queue_polling()
        threading.Thread(
            target=self._check_worker,
            name="KeyForge-UpdateCheck",
            daemon=True,
        ).start()

    def _check_worker(self) -> None:
        try:
            release = fetch_latest_release(current_version=__version__, repo=DEFAULT_REPO, timeout=8.0)
            if not self._closed:
                self._ui_queue.put(("check_success", release))
        except Exception as exc:
            if not self._closed:
                self._ui_queue.put(("check_error", str(exc)))

    def _on_check_success(self, release: ReleaseInfo) -> None:
        self._is_checking = False
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(0.0)
        self.check_again_btn.configure(state="normal", text="Check Again")
        self._release = release
        self._render_release(release)
        if self._on_release_checked is not None:
            with contextlib.suppress(Exception):
                self._on_release_checked(release)

    def _on_check_error(self, message: str) -> None:
        self._is_checking = False
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(0.0)
        self.check_again_btn.configure(state="normal", text="Check Again")
        self.status_title_lbl.configure(text="Update Check Failed", text_color=ACCENT_WARNING)
        self.meta_lbl.configure(text=message[:140])
        self.sha_pill.configure(text="Check Error", fg_color=BADGE_CRIT_BG, text_color=BADGE_CRIT_FG)
        self.progress_lbl.configure(
            text="Could not reach update server. Try again or open Release Page.", text_color=TEXT_SECONDARY
        )
        if self._release is None:
            self._set_notes_text(
                f"Error checking for updates:\n\n{message}\n\nYou can inspect releases manually via Release Page ↗."
            )

    def _render_release(self, release: ReleaseInfo) -> None:
        size_str = _format_bytes(release.asset_size)
        asset_label = release.asset_name or "No matching platform binary"
        notes_text = release.body.strip() or "No release notes provided for this version."
        self._set_notes_text(notes_text)

        if release.sha256:
            self.sha_pill.configure(
                text=f"SHA-256: {release.sha256[:10]}…",
                fg_color=BADGE_OK_BG,
                text_color=BADGE_OK_FG,
            )
        elif release.download_url:
            self.sha_pill.configure(
                text="No SHA-256 Asset",
                fg_color=BADGE_WARN_BG,
                text_color=BADGE_WARN_FG,
            )
        else:
            self.sha_pill.configure(
                text="No Binary Asset",
                fg_color=BG_ELEVATED,
                text_color=TEXT_MUTED,
            )

        if release.is_update_available:
            display_name = release.name or f"v{release.version}"
            self.status_title_lbl.configure(
                text=f"Update Available — v{release.version}",
                text_color=ACCENT_SUCCESS,
            )
            self.meta_lbl.configure(text=f"{display_name}  •  {asset_label} ({size_str})")
            if release.download_url:
                self.install_btn.configure(
                    state="normal",
                    text="Update Now" if getattr(sys, "frozen", False) else "Download & Verify",
                )
                mode_hint = (
                    "All configs & macros will be preserved automatically."
                    if getattr(sys, "frozen", False)
                    else "Source mode: downloads & verifies binary without replacing python.exe."
                )
                self.progress_lbl.configure(text=mode_hint, text_color=TEXT_SECONDARY)
            else:
                self.install_btn.configure(state="disabled", text="Manual Download")
                self.progress_lbl.configure(
                    text="No matching executable asset found for this OS. Use Release Page ↗.",
                    text_color=ACCENT_WARNING,
                )
        else:
            self.status_title_lbl.configure(
                text=f"You're Up to Date (v{__version__})",
                text_color=TEXT_PRIMARY,
            )
            self.meta_lbl.configure(text=f"Latest release on GitHub: v{release.version}")
            self.install_btn.configure(state="disabled", text="Up to Date")
            self.progress_lbl.configure(text="KeyForge is running the latest version.", text_color=TEXT_SECONDARY)

    # ------------------------------------------------------------------
    # Streaming Download, SHA-256 Verification & Hot-Swap Restart
    # ------------------------------------------------------------------

    def start_download_and_install(self) -> None:
        if self._is_downloading or self._is_checking or self._release is None or not self._release.download_url:
            return

        self._is_downloading = True
        self._cancel_requested = False
        self.check_again_btn.configure(state="disabled")
        self.install_btn.configure(state="disabled", text="Downloading...")
        self.secondary_btn.configure(text="Cancel Download")
        self.progress_bar.configure(mode="determinate")
        self.progress_bar.set(0.0)
        self.progress_lbl.configure(text="Starting secure HTTPS download...", text_color=TEXT_PRIMARY)

        release = self._release
        updates_dir = get_config_dir() / "updates"
        filename = release.asset_name or f"KeyForge-v{release.version}.bin"
        target_path = updates_dir / Path(filename).name

        self._ensure_queue_polling()
        threading.Thread(
            target=self._download_worker,
            args=(release, target_path),
            name="KeyForge-UpdateDownload",
            daemon=True,
        ).start()

    def _download_worker(self, release: ReleaseInfo, target_path: Path) -> None:
        last_ui_ts = 0.0

        def _on_progress(downloaded: int, total: int | None) -> None:
            nonlocal last_ui_ts
            if self._closed or self._cancel_requested:
                return
            now = time.monotonic()
            done = total is not None and downloaded >= total
            if not done and (now - last_ui_ts) < 0.10:
                return
            last_ui_ts = now
            self._ui_queue.put(("download_progress", (downloaded, total)))

        try:
            dest = download_update_asset(
                release=release,
                target_file=target_path,
                progress_callback=_on_progress,
                cancel_check=lambda: self._cancel_requested or self._closed,
                require_sha256=False,
            )
            if not self._closed:
                self._ui_queue.put(("download_complete", dest))
        except UpdateCheckError as exc:
            if not self._closed:
                self._ui_queue.put(("download_error", (str(exc), exc.reason == "cancelled")))
        except Exception as exc:
            if not self._closed:
                self._ui_queue.put(("download_error", (str(exc), False)))

    def _update_download_progress(self, downloaded: int, total: int | None) -> None:
        if total and total > 0:
            ratio = min(1.0, max(0.0, downloaded / float(total)))
            pct = int(ratio * 100)
            self.progress_bar.set(ratio)
            self.progress_lbl.configure(
                text=f"Downloading {_format_bytes(downloaded)} / {_format_bytes(total)} ({pct}%)",
                text_color=TEXT_PRIMARY,
            )
        else:
            self.progress_lbl.configure(
                text=f"Downloading {_format_bytes(downloaded)}...",
                text_color=TEXT_PRIMARY,
            )

    def _on_download_error(self, message: str, cancelled: bool) -> None:
        self._is_downloading = False
        self.check_again_btn.configure(state="normal")
        self.secondary_btn.configure(text="Close", state="normal")
        if self._release and self._release.is_update_available and self._release.download_url:
            self.install_btn.configure(
                state="normal",
                text="Update Now" if getattr(sys, "frozen", False) else "Download & Verify",
            )
        self.progress_bar.set(0.0)
        if cancelled:
            self.progress_lbl.configure(text="Download cancelled.", text_color=TEXT_SECONDARY)
        else:
            self.progress_lbl.configure(text=f"Update failed: {message}", text_color=BADGE_CRIT_FG)

    def _on_download_complete(self, downloaded_path: Path) -> None:
        self._is_downloading = False
        self._downloaded_path = downloaded_path
        self.progress_bar.set(1.0)
        self.secondary_btn.configure(text="Close", state="normal")
        self.check_again_btn.configure(state="normal")

        if self._release and self._release.sha256:
            self.sha_pill.configure(text="SHA-256 Verified ✓", fg_color=BADGE_OK_BG, text_color=BADGE_OK_FG)

        if not getattr(sys, "frozen", False):
            self.install_btn.configure(state="disabled", text="Downloaded")
            self.progress_lbl.configure(
                text=f"Verified binary saved to updates/{downloaded_path.name} (source mode: restart skipped).",
                text_color=ACCENT_SUCCESS,
            )
            return

        self.progress_lbl.configure(text="Verified! Preserving state and restarting...", text_color=ACCENT_SUCCESS)
        try:
            if self._on_before_restart is not None:
                self._on_before_restart()
            apply_update_and_restart(downloaded_path)
        except Exception as exc:
            self.progress_lbl.configure(text=f"Restart error: {exc}", text_color=BADGE_CRIT_FG)
            return

        # Close dialog and exit master cleanly so Windows PID releases file lock for hot-swap
        self._closed = True
        self._cancel_timers()
        with contextlib.suppress(Exception):
            self.grab_release()
        with contextlib.suppress(Exception):
            self.destroy()
        with contextlib.suppress(Exception):
            on_close = getattr(self._master_app, "_on_close", None)
            if callable(on_close):
                on_close()
            else:
                self._master_app.destroy()
        with contextlib.suppress(Exception):
            self._master_app.quit()

    # ------------------------------------------------------------------
    # Actions & Cleanup
    # ------------------------------------------------------------------

    def _open_release_page(self) -> None:
        fallback_url = f"https://github.com/{DEFAULT_REPO}/releases"
        raw_url = self._release.html_url if self._release and self._release.html_url else fallback_url
        with contextlib.suppress(Exception):
            safe_url = validate_https_url(raw_url)
            webbrowser.open(safe_url)

    def _on_secondary_clicked(self) -> None:
        if self._is_downloading:
            self._cancel_requested = True
            self.secondary_btn.configure(state="disabled", text="Cancelling...")
            self.progress_lbl.configure(text="Cancelling download...", text_color=ACCENT_WARNING)
            return
        self._on_close()

    def _on_close(self) -> None:
        self._closed = True
        self._cancel_requested = True
        self._cancel_timers()
        while True:
            try:
                self._ui_queue.get_nowait()
            except queue.Empty:
                break
        with contextlib.suppress(Exception):
            self.grab_release()
        with contextlib.suppress(Exception):
            self.destroy()
