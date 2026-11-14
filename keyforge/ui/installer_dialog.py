"""Minimalist Apple-style Setup & Uninstall wizard window for KeyForge."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path

import customtkinter as ctk

from keyforge import __version__
from keyforge.config import get_config_dir
from keyforge.installer import (
    get_default_install_dir,
    install_keyforge,
    uninstall_keyforge,
)
from keyforge.ui.theme import (
    ACCENT_DANGER,
    ACCENT_DANGER_HOVER,
    ACCENT_PRIMARY,
    ACCENT_SUCCESS,
    BG_CARD,
    BG_INPUT,
    BG_PANEL,
    BG_ROOT,
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


class InstallerWindow(ctk.CTk):
    """Standalone Dark Titanium Setup & Uninstaller window for KeyForge."""

    def __init__(
        self,
        mode: str = "setup",
        install_dir: Path | None = None,
    ) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._mode = "uninstall" if str(mode).strip().lower() == "uninstall" else "setup"
        self._target_dir = Path(install_dir) if install_dir is not None else get_default_install_dir()
        self._closing = False
        self._finished = False

        self.title(
            f"Uninstall KeyForge v{__version__}" if self._mode == "uninstall" else f"KeyForge v{__version__} Setup"
        )
        self.resizable(False, False)
        self.configure(fg_color=BG_ROOT)

        self.fonts = get_fonts(self)
        apply_window_icon(self)
        center_window_on_parent(self, None, 540, 490)

        self.desktop_var = ctk.BooleanVar(value=True)
        self.start_menu_var = ctk.BooleanVar(value=True)
        self.launch_var = ctk.BooleanVar(value=True)
        self.purge_var = ctk.BooleanVar(value=False)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        shell = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=0,
        )
        shell.pack(fill="both", expand=True)

        # Header
        hdr = ctk.CTkFrame(shell, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 12))

        self._icon_img = load_brand_ctk_image((38, 38))
        if self._icon_img is not None:
            ctk.CTkLabel(hdr, text="", image=self._icon_img).pack(side="left", padx=(0, 12))

        title_col = ctk.CTkFrame(hdr, fg_color="transparent")
        title_col.pack(side="left", fill="x", expand=True)

        heading = f"KeyForge v{__version__} Setup" if self._mode == "setup" else f"Uninstall KeyForge v{__version__}"
        subtitle = (
            "Install KeyForge to your PC while preserving existing configs & macros"
            if self._mode == "setup"
            else "Remove KeyForge binaries, shortcuts, and app registration"
        )
        ctk.CTkLabel(
            title_col,
            text=heading,
            font=self.fonts["hero"],
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_col,
            text=subtitle,
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        # Location Card
        loc_card = ctk.CTkFrame(
            shell,
            fg_color=BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        loc_card.pack(fill="x", padx=24, pady=(0, 12))

        ctk.CTkLabel(
            loc_card,
            text="Destination Folder" if self._mode == "setup" else "Installed Location",
            font=self.fonts["small_bold"],
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(10, 4))

        dir_box = ctk.CTkFrame(loc_card, fg_color=BG_INPUT, corner_radius=8)
        dir_box.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(
            dir_box,
            text=str(self._target_dir),
            font=self.fonts["mono_small"],
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", padx=10, pady=8)

        # Options Card
        opts_card = ctk.CTkFrame(
            shell,
            fg_color=BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        opts_card.pack(fill="x", padx=24, pady=(0, 12))

        config_dir = get_config_dir()
        if self._mode == "setup":
            ctk.CTkCheckBox(
                opts_card,
                text="Create Desktop shortcut",
                variable=self.desktop_var,
                font=self.fonts["body"],
            ).pack(anchor="w", padx=14, pady=(12, 6))
            ctk.CTkCheckBox(
                opts_card,
                text="Create Start Menu shortcut",
                variable=self.start_menu_var,
                font=self.fonts["body"],
            ).pack(anchor="w", padx=14, pady=6)
            ctk.CTkCheckBox(
                opts_card,
                text="Launch KeyForge when setup finishes",
                variable=self.launch_var,
                font=self.fonts["body"],
            ).pack(anchor="w", padx=14, pady=6)
            note_text = f"Configs & saved macros in {config_dir} are automatically preserved."
        else:
            ctk.CTkCheckBox(
                opts_card,
                text="Also delete my saved macros, custom presets & configuration",
                variable=self.purge_var,
                font=self.fonts["body_bold"],
                fg_color=ACCENT_DANGER,
                hover_color=ACCENT_DANGER_HOVER,
            ).pack(anchor="w", padx=14, pady=(16, 8))
            note_text = f"Left unchecked, your saved data in {config_dir} remains safe for future reinstalls."

        ctk.CTkLabel(
            opts_card,
            text=note_text,
            font=self.fonts["small"],
            text_color=TEXT_MUTED,
            anchor="w",
            wraplength=460,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(8, 12))

        # Status & Action Footer
        self.status_lbl = ctk.CTkLabel(
            shell,
            text="Ready to install." if self._mode == "setup" else "Ready to uninstall.",
            font=self.fonts["small"],
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self.status_lbl.pack(fill="x", padx=24, pady=(0, 6))

        footer = ctk.CTkFrame(shell, fg_color="transparent")
        footer.pack(fill="x", padx=24, pady=(0, 18))

        self.cancel_btn = ctk.CTkButton(
            footer,
            text="Cancel",
            width=110,
            height=36,
            fg_color=BTN_NEUTRAL,
            hover_color=BTN_NEUTRAL_HOVER,
            font=self.fonts["body_bold"],
            command=self._on_close,
        )
        self.cancel_btn.pack(side="left")

        btn_text = f"Install KeyForge v{__version__}" if self._mode == "setup" else "Uninstall KeyForge"
        btn_fg = CTA_SUCCESS if self._mode == "setup" else ACCENT_DANGER
        btn_hover = CTA_SUCCESS_HOVER if self._mode == "setup" else ACCENT_DANGER_HOVER
        self.action_btn = ctk.CTkButton(
            footer,
            text=btn_text,
            width=210,
            height=36,
            fg_color=btn_fg,
            hover_color=btn_hover,
            font=self.fonts["cta"],
            command=self._on_primary_action,
        )
        self.action_btn.pack(side="right")

    def _on_primary_action(self) -> None:
        if self._finished:
            self._on_close()
            return
        self.action_btn.configure(state="disabled")
        try:
            if self._mode == "setup":
                manifest = install_keyforge(
                    install_dir=self._target_dir,
                    create_desktop_shortcut=bool(self.desktop_var.get()),
                    create_start_menu_shortcut=bool(self.start_menu_var.get()),
                    register_uninstaller=True,
                )
                self._finished = True
                installed_exe = Path(manifest["installed_exe"])
                self.status_lbl.configure(
                    text=f"Installed KeyForge v{__version__} to {self._target_dir}",
                    text_color=ACCENT_SUCCESS,
                )
                self.action_btn.configure(text="Finish", state="normal", fg_color=ACCENT_PRIMARY)
                if self.launch_var.get() and installed_exe.is_file():
                    with contextlib.suppress(Exception):
                        if sys.platform == "win32" or installed_exe.suffix.lower() == ".exe":
                            subprocess.Popen([str(installed_exe)], close_fds=True)  # noqa: S603
                    self._on_close()
            else:
                uninstall_keyforge(
                    install_dir=self._target_dir,
                    purge_user_data=bool(self.purge_var.get()),
                    spawn_self_delete=True,
                )
                self._finished = True
                kept_note = (
                    "All KeyForge files and user configs removed."
                    if self.purge_var.get()
                    else "KeyForge uninstalled. Saved macros and configs were preserved."
                )
                self.status_lbl.configure(text=kept_note, text_color=ACCENT_SUCCESS)
                self.action_btn.configure(text="Done", state="normal", fg_color=ACCENT_PRIMARY)
        except Exception as exc:
            self.status_lbl.configure(text=f"Operation failed: {exc}", text_color=ACCENT_DANGER)
            self.action_btn.configure(state="normal")

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        aid = getattr(self, "_kf_icon_after_id", None)
        if aid is not None:
            with contextlib.suppress(Exception):
                self.after_cancel(aid)
            self._kf_icon_after_id = None
        with contextlib.suppress(Exception):
            self.destroy()
