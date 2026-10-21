"""
Автокликер для Windows: клики и клавиши в выбранное окно, последовательности действий,
горячие клавиши, съём координат, запись, трей, обновление из GitHub.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

import actions
import hotkeys
import input_backend
import recorder
import settings
import tray
import updater
import winapi
from actions import KIND_KEY, KIND_MOUSE, KIND_PAUSE, KIND_TEXT, Position, Step
from library import Library, NamedAction, NamedSequence, Profile, WindowIdentity, match_window
from winapi import Hotkey, KeySpec, user32

__version__ = "2.1.0"

# Спокойная палитра: без кислотных цветов.
C_IDLE = "#5b6472"
C_RUN = "#3f9d5a"
C_REC = "#c0564f"
C_WARN = "#b98a2f"
C_ERR = "#c0564f"
C_MUTED = ("#6b7280", "#9aa3b2")
C_CARD = ("#f3f4f6", "#2b2d31")
C_START = ("#2f855a", "#3f9d5a")
C_START_HOVER = ("#276749", "#2f855a")
C_STOP = ("#c53030", "#c0564f")
C_STOP_HOVER = ("#9b2c2c", "#a2443e")

DEFAULT_HOTKEYS = {
    "toggle": Hotkey(0, 0x75),   # F6 — старт/стоп
    "capture": Hotkey(0, 0x77),  # F8 — снять координаты
    "record": Hotkey(0, 0x78),   # F9 — запись последовательности
}
HOTKEY_TITLES = {
    "toggle": "Старт / Стоп",
    "capture": "Снять координаты под курсором",
    "record": "Запись последовательности",
}


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


def parse_float(value: str, name: str, minimum: float = 0.0) -> float | None:
    try:
        parsed = float(value.strip().replace(",", "."))
    except ValueError:
        messagebox.showerror("Ошибка", f"Некорректное значение: {name}")
        return None
    if parsed < minimum:
        messagebox.showerror("Ошибка", f"{name}: не меньше {minimum:g}")
        return None
    return parsed


def parse_int(value: str, name: str, minimum: int = 0) -> int | None:
    try:
        parsed = int(value.strip())
    except ValueError:
        messagebox.showerror("Ошибка", f"Некорректное значение: {name}")
        return None
    if parsed < minimum:
        messagebox.showerror("Ошибка", f"{name}: не меньше {minimum}")
        return None
    return parsed


# ==========================================================================
# Диалоги захвата
# ==========================================================================
class _CaptureDialog(ctk.CTkToplevel):
    def __init__(self, parent: ctk.CTk, title: str, text: str) -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result = None
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(padx=28, pady=24)
        ctk.CTkLabel(body, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack()
        ctk.CTkLabel(body, text=text, text_color=C_MUTED, justify="center").pack(pady=(6, 12))
        self.preview_var = ctk.StringVar(value="…")
        ctk.CTkLabel(body, textvariable=self.preview_var, font=ctk.CTkFont(size=20)).pack(pady=(0, 14))
        ctk.CTkButton(body, text="Отмена", width=120, fg_color=C_IDLE, command=self.destroy).pack()
        self.bind("<KeyPress>", self._on_key)
        self.after(50, self._center, parent)
        self.after(120, self.grab_set)
        self.after(120, self.focus_force)

    def _center(self, parent: ctk.CTk) -> None:
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(px, 0)}+{max(py, 0)}")

    def _on_key(self, event) -> str:
        return "break"


class KeyCaptureDialog(_CaptureDialog):
    def __init__(self, parent: ctk.CTk) -> None:
        super().__init__(
            parent, "Нажмите клавишу",
            "Символ запоминается в той раскладке,\nкоторая активна в момент нажатия.",
        )

    def _on_key(self, event) -> str:
        vk = int(event.keycode)
        char = event.char if event.char and event.char.isprintable() else None
        name = "Пробел" if char == " " else (char if char else winapi.key_name_from_vk(vk))
        self.result = KeySpec(name, vk, char)
        self.preview_var.set(self.result.label)
        self.after(150, self.destroy)
        return "break"


class HotkeyCaptureDialog(_CaptureDialog):
    def __init__(self, parent: ctk.CTk) -> None:
        super().__init__(
            parent, "Нажмите сочетание",
            "Например F6, Ctrl+Shift+S или Alt+F10.\nОдин модификатор без клавиши не подходит.",
        )

    def _on_key(self, event) -> str:
        hk = hotkeys.hotkey_from_tk_event(event.keycode, event.state)
        if hk is None:
            self.preview_var.set("… + ?")
            return "break"
        self.result = hk
        self.preview_var.set(hk.label)
        self.after(150, self.destroy)
        return "break"


# ==========================================================================
# Редактор одного шага (используется во вкладке «Действие» и в диалоге шага)
# ==========================================================================
class StepEditor(ctk.CTkFrame):
    KINDS = [("Клик мышью", KIND_MOUSE), ("Клавиша", KIND_KEY), ("Текст", KIND_TEXT), ("Пауза", KIND_PAUSE)]

    def __init__(self, master, app: "App", show_pause: bool = True) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._captured_size: tuple[int, int] = (0, 0)
        self._key: KeySpec | None = None

        kinds = [label for label, kind in self.KINDS if show_pause or kind != KIND_PAUSE]
        self.kind_seg = ctk.CTkSegmentedButton(self, values=kinds, command=lambda _v: self._show_kind())
        self.kind_seg.set(kinds[0])
        self.kind_seg.pack(anchor="w", pady=(0, 10))

        self.panels: dict[str, ctk.CTkFrame] = {}
        self._build_mouse()
        self._build_key()
        self._build_text()
        self._build_pause()
        self._show_kind()

    # ------------------------------------------------------------- mouse
    def _build_mouse(self) -> None:
        p = ctk.CTkFrame(self, fg_color="transparent")
        self.panels[KIND_MOUSE] = p

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row, text="Кнопка", width=110, anchor="w").pack(side="left")
        self.button_seg = ctk.CTkSegmentedButton(row, values=["Левая", "Правая", "Средняя"])
        self.button_seg.set("Левая")
        self.button_seg.pack(side="left")
        ctk.CTkLabel(row, text="Клик", width=60, anchor="e").pack(side="left", padx=(16, 8))
        self.clicks_seg = ctk.CTkSegmentedButton(row, values=["Одинарный", "Двойной", "Тройной"])
        self.clicks_seg.set("Одинарный")
        self.clicks_seg.pack(side="left")

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row, text="Где кликать", width=110, anchor="w").pack(side="left")
        self.pos_mode_var = ctk.StringVar(value="fixed")
        ctk.CTkRadioButton(row, text="В точке окна", variable=self.pos_mode_var, value="fixed",
                           command=self._show_kind).pack(side="left")
        ctk.CTkRadioButton(row, text="Там, где курсор", variable=self.pos_mode_var, value="cursor",
                           command=self._show_kind).pack(side="left", padx=(14, 0))

        self.pos_frame = ctk.CTkFrame(p, fg_color="transparent")
        self.pos_frame.pack(fill="x")
        row = ctk.CTkFrame(self.pos_frame, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row, text="Точка X / Y", width=110, anchor="w").pack(side="left")
        self.x_var = ctk.StringVar(value="50")
        self.y_var = ctk.StringVar(value="50")
        ctk.CTkEntry(row, textvariable=self.x_var, width=70).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.y_var, width=70).pack(side="left", padx=(6, 0))
        self.capture_btn = ctk.CTkButton(row, text="Снять координаты", width=150,
                                         command=self.app.capture_point_here)
        self.capture_btn.pack(side="left", padx=(12, 0))
        self.relative_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(row, text="Привязка к размеру окна", variable=self.relative_var).pack(side="left", padx=(14, 0))

        row = ctk.CTkFrame(self.pos_frame, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row, text="Разброс ±", width=110, anchor="w").pack(side="left")
        self.spread_x_var = ctk.StringVar(value="3")
        self.spread_y_var = ctk.StringVar(value="3")
        ctk.CTkEntry(row, textvariable=self.spread_x_var, width=70).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.spread_y_var, width=70).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(row, text="пикс., 0 = точно в точку", text_color=C_MUTED).pack(side="left", padx=(12, 0))

        self.pos_info_var = ctk.StringVar(value="")
        ctk.CTkLabel(self.pos_frame, textvariable=self.pos_info_var, text_color=C_MUTED,
                     anchor="w", justify="left", wraplength=620).pack(fill="x")

    # --------------------------------------------------------------- key
    def _build_key(self) -> None:
        p = ctk.CTkFrame(self, fg_color="transparent")
        self.panels[KIND_KEY] = p
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row, text="Группа", width=110, anchor="w").pack(side="left")
        self.group_var = ctk.StringVar(value=winapi.KEY_GROUP_NAMES[0])
        ctk.CTkOptionMenu(row, values=winapi.KEY_GROUP_NAMES, variable=self.group_var, width=190,
                          command=lambda _v: self._on_group_change()).pack(side="left")
        ctk.CTkLabel(row, text="Клавиша", width=70, anchor="e").pack(side="left", padx=(16, 8))
        self.key_var = ctk.StringVar()
        self.key_menu = ctk.CTkOptionMenu(row, values=[""], variable=self.key_var, width=190,
                                          command=lambda _v: self._on_key_pick())
        self.key_menu.pack(side="left")
        ctk.CTkButton(row, text="Нажать клавишу…", width=140, command=self._capture_key).pack(side="left", padx=(12, 0))

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row, text="Отправлять как", width=110, anchor="w").pack(side="left")
        self.key_mode_seg = ctk.CTkSegmentedButton(row, values=["Нажатие клавиши", "Символ"])
        self.key_mode_seg.set("Нажатие клавиши")
        self.key_mode_seg.pack(side="left")

        self.key_info_var = ctk.StringVar()
        ctk.CTkLabel(p, textvariable=self.key_info_var, text_color=C_MUTED, anchor="w",
                     justify="left", wraplength=620).pack(fill="x")
        self._on_group_change()

    # -------------------------------------------------------------- text
    def _build_text(self) -> None:
        p = ctk.CTkFrame(self, fg_color="transparent")
        self.panels[KIND_TEXT] = p
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row, text="Текст", width=110, anchor="w").pack(side="left")
        self.text_var = ctk.StringVar()
        ctk.CTkEntry(row, textvariable=self.text_var, width=420).pack(side="left")
        ctk.CTkLabel(p, text="Текст вводится посимвольно и не зависит от раскладки целевого окна.",
                     text_color=C_MUTED, anchor="w").pack(fill="x")

    def _build_pause(self) -> None:
        p = ctk.CTkFrame(self, fg_color="transparent")
        self.panels[KIND_PAUSE] = p
        ctk.CTkLabel(p, text="Шаг ничего не делает — только ждёт указанную паузу.",
                     text_color=C_MUTED, anchor="w").pack(fill="x")

    # ----------------------------------------------------------- helpers
    def _kind(self) -> str:
        label = self.kind_seg.get()
        for text, kind in self.KINDS:
            if text == label:
                return kind
        return KIND_MOUSE

    def _show_kind(self, *_args) -> None:
        kind = self._kind()
        for k, panel in self.panels.items():
            panel.pack_forget()
        self.panels[kind].pack(fill="x")
        if kind == KIND_MOUSE:
            if self.pos_mode_var.get() == "fixed":
                self.pos_frame.pack(fill="x")
            else:
                self.pos_frame.pack_forget()

    def _on_group_change(self) -> None:
        keys = winapi.KEY_GROUPS[self.group_var.get()]
        labels = [k.label for k in keys]
        self.key_menu.configure(values=labels)
        self.key_var.set(labels[0])
        self._on_key_pick()

    def _on_key_pick(self) -> None:
        self._key = winapi.key_by_label(self.key_var.get())
        self._refresh_key_info()

    def _capture_key(self) -> None:
        dialog = KeyCaptureDialog(self.app)
        self.app.wait_window(dialog)
        if dialog.result is not None:
            self._key = dialog.result
            self.key_var.set("")
            self._refresh_key_info(captured=True)

    def _refresh_key_info(self, captured: bool = False) -> None:
        key = self._key
        if key is None:
            self.key_info_var.set("Клавиша не выбрана.")
            return
        src = "Захвачено" if captured else "Выбрано"
        ch = f"символ «{key.char}»" if key.char and key.char.strip() else "без символа"
        self.key_info_var.set(
            f"{src}: {key.label}, {ch}. «Нажатие клавиши» — код клавиши, символ зависит от раскладки "
            f"окна. «Символ» — сам символ независимо от раскладки."
        )

    def apply_capture(self, pos: Position) -> None:
        self.kind_seg.set("Клик мышью")
        self.pos_mode_var.set("fixed")
        self._show_kind()
        self.x_var.set(str(pos.x))
        self.y_var.set(str(pos.y))
        self._captured_size = (pos.client_w, pos.client_h)
        pct = pos.percent
        if pct:
            self.pos_info_var.set(
                f"Снято: ({pos.x}, {pos.y}) при размере окна {pos.client_w}×{pos.client_h} — "
                f"{pct[0]:.1f}% × {pct[1]:.1f}%. При включённой привязке точка пересчитывается, "
                f"если окно изменит размер или масштаб."
            )
        else:
            self.pos_info_var.set(f"Снято: ({pos.x}, {pos.y}).")

    # ------------------------------------------------------- get / set
    def get_step(self) -> Step | None:
        kind = self._kind()
        step = Step(kind=kind)
        if kind == KIND_MOUSE:
            step.button = {"Левая": "left", "Правая": "right", "Средняя": "middle"}[self.button_seg.get()]
            step.clicks = {"Одинарный": 1, "Двойной": 2, "Тройной": 3}[self.clicks_seg.get()]
            step.use_cursor = self.pos_mode_var.get() == "cursor"
            if not step.use_cursor:
                x = parse_int(self.x_var.get(), "X")
                y = parse_int(self.y_var.get(), "Y")
                sx = parse_int(self.spread_x_var.get(), "Разброс X")
                sy = parse_int(self.spread_y_var.get(), "Разброс Y")
                if None in (x, y, sx, sy):
                    return None
                cw, ch = self._captured_size
                step.position = Position(x, y, cw, ch, relative=self.relative_var.get())
                step.spread_x, step.spread_y = sx, sy
        elif kind == KIND_KEY:
            if self._key is None:
                messagebox.showwarning("Действие", "Выберите клавишу или нажмите «Нажать клавишу…».")
                return None
            step.key = self._key
            step.key_mode = "char" if self.key_mode_seg.get() == "Символ" else "key"
            if step.key_mode == "char" and not step.key.char:
                messagebox.showwarning("Действие", f"У клавиши {step.key.name} нет печатного символа. "
                                       "Выберите «Нажатие клавиши».")
                return None
        elif kind == KIND_TEXT:
            step.text = self.text_var.get()
            if not step.text:
                messagebox.showwarning("Действие", "Введите текст.")
                return None
        return step

    def set_step(self, step: Step) -> None:
        for text, kind in self.KINDS:
            if kind == step.kind and text in self.kind_seg._value_list:
                self.kind_seg.set(text)
        self.button_seg.set({"left": "Левая", "right": "Правая", "middle": "Средняя"}.get(step.button, "Левая"))
        self.clicks_seg.set({1: "Одинарный", 2: "Двойной", 3: "Тройной"}.get(step.clicks, "Одинарный"))
        self.pos_mode_var.set("cursor" if step.use_cursor else "fixed")
        self.x_var.set(str(step.position.x))
        self.y_var.set(str(step.position.y))
        self.spread_x_var.set(str(step.spread_x))
        self.spread_y_var.set(str(step.spread_y))
        self.relative_var.set(step.position.relative)
        self._captured_size = (step.position.client_w, step.position.client_h)
        pct = step.position.percent
        self.pos_info_var.set(
            f"Точка снята при размере окна {step.position.client_w}×{step.position.client_h} "
            f"({pct[0]:.1f}% × {pct[1]:.1f}%)." if pct else ""
        )
        if step.key is not None:
            self._key = step.key
            self.key_var.set(step.key.label if winapi.key_by_label(step.key.label) else "")
            self._refresh_key_info(captured=not bool(self.key_var.get()))
        self.key_mode_seg.set("Символ" if step.key_mode == "char" else "Нажатие клавиши")
        self.text_var.set(step.text)
        self._show_kind()


class StepDialog(ctk.CTkToplevel):
    """Диалог добавления/редактирования шага последовательности."""

    def __init__(self, app: "App", step: Step | None = None) -> None:
        super().__init__(app)
        self.app = app
        self.result: Step | None = None
        self.title("Шаг последовательности" if step else "Новый шаг")
        self.resizable(False, False)
        self.transient(app)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(padx=20, pady=16, fill="both")
        self.editor = StepEditor(body, app)
        self.editor.pack(fill="x")

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(row, text="Пауза после, сек", width=110, anchor="w").pack(side="left")
        self.dmin_var = ctk.StringVar(value="0.4")
        self.dmax_var = ctk.StringVar(value="0.6")
        ctk.CTkLabel(row, text="от").pack(side="left")
        ctk.CTkEntry(row, textvariable=self.dmin_var, width=70).pack(side="left", padx=(4, 10))
        ctk.CTkLabel(row, text="до").pack(side="left")
        ctk.CTkEntry(row, textvariable=self.dmax_var, width=70).pack(side="left", padx=(4, 0))

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x", pady=(16, 0))
        ctk.CTkButton(btns, text="Сохранить", width=130, command=self._ok).pack(side="right")
        ctk.CTkButton(btns, text="Отмена", width=100, fg_color=C_IDLE, command=self.destroy).pack(side="right", padx=(0, 8))

        if step is not None:
            self.editor.set_step(step)
            self.dmin_var.set(f"{step.delay_min:g}")
            self.dmax_var.set(f"{step.delay_max:g}")
        app.set_capture_consumer(self.editor)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(120, self.grab_set)
        self.after(120, self.focus_force)

    def _ok(self) -> None:
        step = self.editor.get_step()
        if step is None:
            return
        dmin = parse_float(self.dmin_var.get(), "Пауза от")
        dmax = parse_float(self.dmax_var.get(), "Пауза до")
        if dmin is None or dmax is None:
            return
        if dmin > dmax:
            messagebox.showerror("Ошибка", "«Пауза от» не может быть больше «Пауза до».")
            return
        step.delay_min, step.delay_max = dmin, dmax
        self.result = step
        self._close()

    def _close(self) -> None:
        self.app.set_capture_consumer(None)
        self.destroy()


class LibraryPickDialog(ctk.CTkToplevel):
    """Выбор элемента библиотеки, привязанного к другим окнам (или ни к одному)."""

    def __init__(self, app: "App", title: str, items: list[tuple[str, str, str]]) -> None:
        super().__init__(app)
        self.result: tuple[str, str] | None = None  # (id, "link" | "copy")
        self.title(title)
        self.resizable(False, False)
        self.transient(app)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(padx=20, pady=16, fill="both")
        ctk.CTkLabel(body, text=title, font=ctk.CTkFont(size=15, weight="bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(body, text="«Привязать» — один элемент на несколько окон, правки видны везде. "
                     "«Копировать» — независимая копия для этого окна.",
                     text_color=C_MUTED, anchor="w", justify="left", wraplength=520).pack(fill="x", pady=(4, 10))
        self.choice = ctk.StringVar(value=items[0][0] if items else "")
        box = ctk.CTkScrollableFrame(body, width=540, height=min(260, 36 * max(1, len(items)) + 10))
        box.pack(fill="x")
        for item_id, name, windows in items:
            ctk.CTkRadioButton(box, text=f"{name}   —   {windows}", variable=self.choice, value=item_id).pack(anchor="w", pady=3)
        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x", pady=(14, 0))
        ctk.CTkButton(btns, text="Копировать", width=120, command=lambda: self._done("copy")).pack(side="right")
        ctk.CTkButton(btns, text="Привязать", width=120, command=lambda: self._done("link")).pack(side="right", padx=(0, 8))
        ctk.CTkButton(btns, text="Отмена", width=100, fg_color=C_IDLE, command=self.destroy).pack(side="right", padx=(0, 8))
        self.after(120, self.grab_set)
        self.after(120, self.focus_force)

    def _done(self, mode: str) -> None:
        if self.choice.get():
            self.result = (self.choice.get(), mode)
        self.destroy()


def ask_name(app: "App", title: str, text: str, initial: str = "") -> str | None:
    dialog = ctk.CTkInputDialog(text=text, title=title)
    if initial:
        dialog.after(150, lambda: (dialog._entry.insert(0, initial), dialog._entry.select_range(0, "end")))
    value = dialog.get_input()
    if value is None:
        return None
    value = value.strip()
    return value or None


# ==========================================================================
# Главное окно
# ==========================================================================
class App(ctk.CTk):
    def __init__(self) -> None:
        winapi.enable_dpi_awareness()
        super().__init__()
        self.title(f"Автокликер {__version__}")
        self.minsize(860, 680)

        self._settings = settings.load()
        self._ui_queue: queue.Queue[tuple[Callable, tuple]] = queue.Queue()
        self._windows: list[tuple[int, str]] = []
        self._window_by_label: dict[str, int] = {}
        self._identity_by_hwnd: dict[int, WindowIdentity] = {}
        self._runner: actions.Runner | None = None
        self._updating = False
        self._closing = False
        self._capture_consumer: StepEditor | None = None
        self._focus_cache: dict[int, int] = {}
        self._loading = False

        self.library = Library.from_dict(self._settings.get("library"))
        if "library" not in self._settings and ("single" in self._settings or "sequence" in self._settings):
            self.library.migrate_v2(self._settings)
        self.profile: Profile | None = None
        self._current_action: NamedAction | None = None
        self._current_sequence: NamedSequence | None = None
        self._steps: list[Step] = []
        self._action_by_label: dict[str, str] = {}
        self._sequence_by_label: dict[str, str] = {}

        self._hotkeys: dict[str, Hotkey | None] = {}
        saved_hk = self._settings.get("hotkeys") or {}
        for name in DEFAULT_HOTKEYS:
            if name in saved_hk:
                self._hotkeys[name] = Hotkey.from_dict(saved_hk[name])
            else:
                self._hotkeys[name] = DEFAULT_HOTKEYS[name]

        ctk.set_appearance_mode(self._settings.get("appearance", "dark"))
        ctk.set_default_color_theme("dark-blue")
        self._style_treeview()

        self._build_ui()
        self._apply_settings()
        self._set_icon()

        self.hotkey_manager = hotkeys.HotkeyManager(self._on_hotkey_thread)
        self._register_hotkeys()
        self.recorder = recorder.Recorder(on_event=lambda kind: self.post_ui(self._on_record_event))
        self.tray = tray.TrayIcon(
            on_show=lambda: self.post_ui(self.show_window),
            on_toggle=lambda: self.post_ui(self.toggle_run),
            on_exit=lambda: self.post_ui(self.quit_app),
        )
        self.tray.start()

        self.refresh_windows(select_saved=True)
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.bind("<Unmap>", self._on_unmap)
        self._set_status("Готов")
        self._timers: dict[str, str] = {}
        self._timers["queue"] = self.after(30, self._drain_ui_queue)
        self._timers["poll"] = self.after(1000, self._poll_target)

    # ------------------------------------------------ межпоточные вызовы
    def post_ui(self, fn: Callable, *args) -> None:
        """Выполнить fn(*args) в главном потоке (можно звать из любого потока)."""
        self._ui_queue.put((fn, args))

    def _drain_ui_queue(self) -> None:
        if self._closing:
            return
        for _ in range(200):
            try:
                fn, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()
        if not self._closing:
            self._timers["queue"] = self.after(30, self._drain_ui_queue)

    def _poll_target(self) -> None:
        """Раз в секунду: запоминаем окно фокуса цели и находим окно заново после перезапуска."""
        if self._closing:
            return
        hwnd = self.target_hwnd()
        if hwnd and user32.IsWindow(hwnd):
            focus = winapi.focused_child(hwnd)
            if focus and focus != hwnd:
                self._focus_cache[hwnd] = focus
        elif self.profile is not None and self._runner is None and not self.recorder.active:
            if self._try_rebind_window():
                self._set_status(f"Окно «{self.profile.identity.title}» найдено заново после перезапуска", C_RUN)
        if not self._closing:
            self._timers["poll"] = self.after(1000, self._poll_target)

    # --------------------------------------------------------------- UI
    def _set_icon(self) -> None:
        ico = resource_path("icon.ico")
        if ico.exists():
            try:
                self.after(250, lambda: self.iconbitmap(str(ico)))
            except Exception:  # noqa: BLE001
                pass

    def _style_treeview(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        dark = ctk.get_appearance_mode() == "Dark"
        bg = "#1f2124" if dark else "#ffffff"
        fg = "#e5e7eb" if dark else "#1f2937"
        head = "#2b2d31" if dark else "#e5e7eb"
        style.configure("Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=30, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=head, foreground=fg, relief="flat",
                        font=("Segoe UI Semibold", 10), padding=6)
        style.map("Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", head)])

    def _build_ui(self) -> None:
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=18, pady=(14, 10))

        header = ctk.CTkFrame(root, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left")
        ctk.CTkLabel(titles, text="Автокликер", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(titles, text="клики, клавиши и последовательности в выбранное окно",
                     text_color=C_MUTED).pack(anchor="w")
        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.pack(side="right")
        self.status_pill = ctk.CTkLabel(controls, text="  Остановлен  ", fg_color=C_IDLE, corner_radius=14,
                                        text_color="#ffffff", font=ctk.CTkFont(size=12, weight="bold"), height=28)
        self.status_pill.pack(side="left", padx=(0, 14))
        self.start_btn = ctk.CTkButton(controls, text="▶  Старт", width=120, height=38,
                                       font=ctk.CTkFont(size=14, weight="bold"),
                                       fg_color=C_START, hover_color=C_START_HOVER, command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ctk.CTkButton(controls, text="■  Стоп", width=110, height=38,
                                      font=ctk.CTkFont(size=14, weight="bold"),
                                      fg_color=C_STOP, hover_color=C_STOP_HOVER, state="disabled", command=self.stop)
        self.stop_btn.pack(side="left", padx=(8, 0))

        # --- целевое окно и профиль
        card = ctk.CTkFrame(root, fg_color=C_CARD, corner_radius=12)
        card.pack(fill="x", pady=(0, 10))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(row, text="Целевое окно", font=ctk.CTkFont(weight="bold"), width=110, anchor="w").pack(side="left")
        self.window_var = ctk.StringVar()
        self.window_menu = ctk.CTkOptionMenu(row, values=[""], variable=self.window_var, width=470,
                                             dynamic_resizing=False, command=lambda _v: self._on_window_selected())
        self.window_menu.pack(side="left")
        ctk.CTkButton(row, text="Обновить список", width=130, command=self.refresh_windows).pack(side="left", padx=(8, 0))
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=(0, 4))
        ctk.CTkLabel(row2, text="Что запускать", width=110, anchor="w").pack(side="left")
        self.run_mode_seg = ctk.CTkSegmentedButton(row2, values=["Одно действие", "Последовательность"],
                                                   command=lambda _v: self._commit_current())
        self.run_mode_seg.set("Одно действие")
        self.run_mode_seg.pack(side="left")
        self.window_state_var = ctk.StringVar()
        self.window_state_lbl = ctk.CTkLabel(row2, textvariable=self.window_state_var, text_color=C_MUTED, anchor="w")
        self.window_state_lbl.pack(side="left", padx=(16, 0))
        self.profile_var = ctk.StringVar()
        ctk.CTkLabel(card, textvariable=self.profile_var, text_color=C_MUTED, anchor="w").pack(fill="x", padx=14, pady=(0, 10))

        self.tabs = ctk.CTkTabview(root, corner_radius=12)
        self.tabs.pack(fill="both", expand=True)
        self._build_action_tab(self.tabs.add("Действие"))
        self._build_sequence_tab(self.tabs.add("Последовательность"))
        self._build_hotkeys_tab(self.tabs.add("Горячие клавиши"))
        self._build_settings_tab(self.tabs.add("Настройки"))

        bar = ctk.CTkFrame(root, fg_color="transparent")
        bar.pack(fill="x", pady=(8, 0))
        self.status_var = ctk.StringVar(value="Готов")
        self.status_lbl = ctk.CTkLabel(bar, textvariable=self.status_var, anchor="w")
        self.status_lbl.pack(side="left")
        self.hint_var = ctk.StringVar()
        ctk.CTkLabel(bar, textvariable=self.hint_var, text_color=C_MUTED).pack(side="right")

    def _build_item_row(self, parent, label: str, on_select, on_new, on_rename, on_delete, on_library):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=(6, 8))
        ctk.CTkLabel(row, text=label, font=ctk.CTkFont(weight="bold"), width=140, anchor="w").pack(side="left")
        var = ctk.StringVar()
        menu = ctk.CTkOptionMenu(row, values=["—"], variable=var, width=260, dynamic_resizing=False,
                                 command=lambda _v: on_select())
        menu.pack(side="left")
        ctk.CTkButton(row, text="Новое", width=70, command=on_new).pack(side="left", padx=(8, 0))
        ctk.CTkButton(row, text="Переименовать", width=120, fg_color=C_IDLE, command=on_rename).pack(side="left", padx=(6, 0))
        ctk.CTkButton(row, text="Удалить", width=80, fg_color=C_IDLE, command=on_delete).pack(side="left", padx=(6, 0))
        ctk.CTkButton(row, text="Из других окон…", width=130, fg_color=C_IDLE, command=on_library).pack(side="left", padx=(6, 0))
        return var, menu

    def _build_action_tab(self, tab) -> None:
        self.action_var, self.action_menu = self._build_item_row(
            tab, "Действие", self._on_action_pick, self.action_new, self.action_rename,
            self.action_delete, self.action_from_library,
        )
        self.action_editor = StepEditor(tab, self, show_pause=False)
        self.action_editor.pack(fill="x", padx=6, pady=(0, 10))

        box = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=10)
        box.pack(fill="x", padx=6)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=10)
        ctk.CTkLabel(row, text="Интервал, сек", width=110, anchor="w").pack(side="left")
        self.interval_min_var = ctk.StringVar(value="0.4")
        self.interval_max_var = ctk.StringVar(value="0.6")
        ctk.CTkLabel(row, text="от").pack(side="left")
        ctk.CTkEntry(row, textvariable=self.interval_min_var, width=70).pack(side="left", padx=(4, 10))
        ctk.CTkLabel(row, text="до").pack(side="left")
        ctk.CTkEntry(row, textvariable=self.interval_max_var, width=70).pack(side="left", padx=(4, 0))
        ctk.CTkLabel(row, text="Повторов", width=80, anchor="e").pack(side="left", padx=(24, 8))
        self.count_var = ctk.StringVar(value="0")
        ctk.CTkEntry(row, textvariable=self.count_var, width=70).pack(side="left")
        ctk.CTkLabel(row, text="0 = бесконечно", text_color=C_MUTED).pack(side="left", padx=(8, 0))

    def _build_sequence_tab(self, tab) -> None:
        self.sequence_var, self.sequence_menu = self._build_item_row(
            tab, "Последовательность", self._on_sequence_pick, self.sequence_new, self.sequence_rename,
            self.sequence_delete, self.sequence_from_library,
        )
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(0, 6))
        ctk.CTkButton(top, text="＋ Шаг", width=90, command=self.seq_add).pack(side="left")
        ctk.CTkButton(top, text="Изменить", width=90, command=self.seq_edit).pack(side="left", padx=(6, 0))
        ctk.CTkButton(top, text="Удалить", width=80, fg_color=C_IDLE, command=self.seq_delete).pack(side="left", padx=(6, 0))
        ctk.CTkButton(top, text="▲", width=36, fg_color=C_IDLE, command=lambda: self.seq_move(-1)).pack(side="left", padx=(12, 0))
        ctk.CTkButton(top, text="▼", width=36, fg_color=C_IDLE, command=lambda: self.seq_move(1)).pack(side="left", padx=(4, 0))
        self.record_btn = ctk.CTkButton(top, text="● Запись", width=100, fg_color=C_REC, hover_color="#a2443e",
                                        command=self.toggle_record)
        self.record_btn.pack(side="left", padx=(12, 0))
        ctk.CTkButton(top, text="Импорт…", width=90, fg_color=C_IDLE, command=self.seq_load).pack(side="right")
        ctk.CTkButton(top, text="Экспорт…", width=90, fg_color=C_IDLE, command=self.seq_save).pack(side="right", padx=(0, 6))

        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=6, pady=(8, 4))
        ctk.CTkLabel(bottom, text="Повторов последовательности").pack(side="left")
        self.repeat_var = ctk.StringVar(value="0")
        ctk.CTkEntry(bottom, textvariable=self.repeat_var, width=70).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(bottom, text="0 = бесконечно", text_color=C_MUTED).pack(side="left", padx=(8, 0))
        self.seq_info_var = ctk.StringVar()
        ctk.CTkLabel(bottom, textvariable=self.seq_info_var, text_color=C_MUTED).pack(side="right")

        table = ctk.CTkFrame(tab, fg_color="transparent")
        table.pack(fill="both", expand=True, padx=6)
        self.tree = ttk.Treeview(table, columns=("n", "kind", "desc", "delay"), show="headings", selectmode="browse")
        self.tree.heading("n", text="#")
        self.tree.heading("kind", text="Действие")
        self.tree.heading("desc", text="Описание")
        self.tree.heading("delay", text="Пауза после")
        self.tree.column("n", width=40, anchor="center", stretch=False)
        self.tree.column("kind", width=130, stretch=False)
        self.tree.column("desc", width=420)
        self.tree.column("delay", width=110, anchor="center", stretch=False)
        self.tree.tag_configure("rec", foreground="#e07b73")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self.seq_edit())
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self._refresh_tree()

    def _build_hotkeys_tab(self, tab) -> None:
        ctk.CTkLabel(tab, text="Глобальные сочетания работают, даже если окно программы свёрнуто или не активно.",
                     text_color=C_MUTED, anchor="w").pack(fill="x", padx=8, pady=(8, 10))
        self.hotkey_labels: dict[str, ctk.CTkLabel] = {}
        for name, title in HOTKEY_TITLES.items():
            row = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=10)
            row.pack(fill="x", padx=6, pady=4)
            ctk.CTkLabel(row, text=title, width=260, anchor="w").pack(side="left", padx=(14, 0), pady=10)
            lbl = ctk.CTkLabel(row, text="", width=160, anchor="w", font=ctk.CTkFont(weight="bold"))
            lbl.pack(side="left")
            self.hotkey_labels[name] = lbl
            ctk.CTkButton(row, text="Изменить", width=100, command=lambda n=name: self.change_hotkey(n)).pack(side="left")
            ctk.CTkButton(row, text="Отключить", width=100, fg_color=C_IDLE,
                          command=lambda n=name: self.change_hotkey(n, clear=True)).pack(side="left", padx=(6, 14))
        ctk.CTkLabel(
            tab, wraplength=720, justify="left", anchor="w", text_color=C_MUTED,
            text="Съём координат: откройте целевое окно, наведите курсор на нужную точку и нажмите сочетание. "
                 "Окно под курсором станет целевым, координаты запишутся во вкладку «Действие» или в открытый "
                 "диалог шага. Запись: нажмите сочетание, выполните клики и нажатия в целевом окне — шаги появляются "
                 "в последовательности сразу; нажмите ещё раз, чтобы закончить.",
        ).pack(fill="x", padx=8, pady=(12, 0))

    def _build_settings_tab(self, tab) -> None:
        card = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=10)
        card.pack(fill="x", padx=6, pady=(8, 6))
        ctk.CTkLabel(card, text="Способ ввода", font=ctk.CTkFont(weight="bold"), anchor="w").pack(fill="x", padx=14, pady=(10, 4))
        self.input_mode_var = ctk.StringVar(value=input_backend.MODE_POST)
        ctk.CTkRadioButton(card, text="Фоновый — сообщения прямо в окно, окно может быть неактивным, курсор не двигается",
                           variable=self.input_mode_var, value=input_backend.MODE_POST).pack(anchor="w", padx=14, pady=3)
        ctk.CTkRadioButton(card, text="Реальный ввод — через системную очередь (SendInput), окно активируется, курсор двигается",
                           variable=self.input_mode_var, value=input_backend.MODE_SEND).pack(anchor="w", padx=14, pady=3)
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.pack(fill="x", padx=14, pady=(4, 10))
        self.activate_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(opts, text="Активировать окно перед запуском", variable=self.activate_var).pack(side="left")
        self.restore_cursor_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(opts, text="Вернуть курсор на место после остановки", variable=self.restore_cursor_var).pack(side="left", padx=(20, 0))
        ctk.CTkLabel(card, wraplength=720, justify="left", anchor="w", text_color=C_MUTED,
                     text="Реальный ввод — максимум, доступный без драйвера: программы получают события из той же "
                          "очереди, что и с физических устройств. Низкоуровневые хуки всё же могут отличить его по "
                          "флагу INJECTED. В фоновом режиме клавиши идут в поле с фокусом внутри окна; у неактивного "
                          "окна фокус часто сброшен, поэтому активация перед запуском помогает, а после клика клавиши "
                          "идут туда, куда кликнули.").pack(fill="x", padx=14, pady=(0, 10))

        card = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=10)
        card.pack(fill="x", padx=6, pady=6)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(row, text="Оформление", width=110, anchor="w").pack(side="left")
        self.appearance_seg = ctk.CTkSegmentedButton(row, values=["Тёмная", "Светлая", "Системная"],
                                                     command=self._on_appearance)
        self.appearance_seg.pack(side="left")
        self.tray_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(row, text="Сворачивать в трей", variable=self.tray_var).pack(side="left", padx=(24, 0))

        card = ctk.CTkFrame(tab, fg_color=C_CARD, corner_radius=10)
        card.pack(fill="x", padx=6, pady=6)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(row, text=f"Версия {__version__}", width=110, anchor="w").pack(side="left")
        self.update_btn = ctk.CTkButton(row, text="Обновить программу", width=170, command=self.check_updates)
        self.update_btn.pack(side="left")
        ctk.CTkLabel(row, text=updater.REPO_URL, text_color=C_MUTED).pack(side="left", padx=(14, 0))
        ctk.CTkLabel(card, wraplength=720, justify="left", anchor="w", text_color=C_MUTED,
                     text=f"Профили окон, действия и последовательности хранятся в {settings.settings_path()}. "
                          "Окно узнаётся по имени процесса, классу окна и заголовку, поэтому после перезапуска "
                          "программы-цели её действия подхватываются автоматически.").pack(fill="x", padx=14, pady=(0, 10))

    # ------------------------------------------------------- настройки
    def _apply_settings(self) -> None:
        s = self._settings
        self.input_mode_var.set(s.get("input_mode", input_backend.MODE_POST))
        self.activate_var.set(bool(s.get("activate_window", True)))
        self.restore_cursor_var.set(bool(s.get("restore_cursor", True)))
        self.tray_var.set(bool(s.get("minimize_to_tray", True)))
        mode = s.get("appearance", "dark")
        self.appearance_seg.set({"dark": "Тёмная", "light": "Светлая", "system": "Системная"}.get(mode, "Тёмная"))
        self._refresh_hotkey_labels()
        geo = s.get("geometry")
        if isinstance(geo, str) and "x" in geo:
            try:
                self.geometry(geo.split("+", 1)[0])
            except Exception:  # noqa: BLE001
                pass

    def _collect_settings(self) -> dict:
        self._commit_current()
        return {
            "version": 3,
            "library": self.library.to_dict(),
            "last_profile": self.profile.identity.key if self.profile else None,
            "hotkeys": {name: (hk.to_dict() if hk else None) for name, hk in self._hotkeys.items()},
            "input_mode": self.input_mode_var.get(),
            "activate_window": self.activate_var.get(),
            "restore_cursor": self.restore_cursor_var.get(),
            "appearance": self._appearance_code(),
            "minimize_to_tray": self.tray_var.get(),
            "geometry": self.geometry(),
        }

    def save_settings(self) -> None:
        try:
            self._settings = self._collect_settings()
            settings.save(self._settings)
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()

    def _appearance_code(self) -> str:
        return {"Тёмная": "dark", "Светлая": "light", "Системная": "system"}.get(self.appearance_seg.get(), "dark")

    def _on_appearance(self, _value: str) -> None:
        ctk.set_appearance_mode(self._appearance_code())
        self.after(100, self._style_treeview)

    # ----------------------------------------------------------- окна
    def refresh_windows(self, select_saved: bool = False) -> None:
        own = user32.GetAncestor(self.winfo_id(), winapi.GA_ROOT)
        self._windows = [(h, t) for h, t in winapi.enum_windows() if h != own]
        self._identity_by_hwnd = {h: WindowIdentity.of(h) for h, _ in self._windows}
        labels: list[str] = []
        self._window_by_label = {}
        seen: dict[str, int] = {}
        for hwnd, title in self._windows:
            ident = self._identity_by_hwnd[hwnd]
            base = f"{title}   [{ident.process}]" if ident.process else title
            seen[base] = seen.get(base, 0) + 1
            label = base if seen[base] == 1 else f"{base} ({seen[base]})"
            labels.append(label)
            self._window_by_label[label] = hwnd
        if not labels:
            labels = ["— окна не найдены —"]
        self.window_menu.configure(values=labels)

        wanted: int | None = None
        identity: WindowIdentity | None = None
        if self.profile is not None:
            identity = self.profile.identity
        elif select_saved:
            saved = self.library.profiles.get(self._settings.get("last_profile") or "")
            identity = saved.identity if saved else None
        if identity is not None:
            wanted = match_window(identity, list(self._identity_by_hwnd.items()))
        if wanted is None:
            current = self._window_by_label.get(self.window_var.get())
            wanted = current if current in self._identity_by_hwnd else None
        label = next((lab for lab, h in self._window_by_label.items() if h == wanted), labels[0])
        self.window_var.set(label)
        self._on_window_selected()
        self._set_status(f"Найдено окон: {len(self._windows)}")

    def _try_rebind_window(self) -> bool:
        """После перезапуска программы-цели находит её окно заново по профилю."""
        if self.profile is None:
            return False
        before = self.profile.identity.key
        self.refresh_windows()
        hwnd = self.target_hwnd()
        return bool(hwnd and self.profile is not None and self.profile.identity.key == before and user32.IsWindow(hwnd))

    def target_hwnd(self) -> int | None:
        return self._window_by_label.get(self.window_var.get())

    def select_window(self, hwnd: int) -> None:
        if hwnd not in self._identity_by_hwnd:
            self.refresh_windows()
        for label, h in self._window_by_label.items():
            if h == hwnd:
                self.window_var.set(label)
                self._on_window_selected()
                return

    def _on_window_selected(self) -> None:
        hwnd = self.target_hwnd()
        if hwnd is None:
            self._update_window_state()
            return
        identity = self._identity_by_hwnd.get(hwnd) or WindowIdentity.of(hwnd)
        if self.profile is not None and self.profile.identity.key == identity.key:
            self._update_window_state()
            return
        self._commit_current()
        self.profile = self.library.profile_for(identity)
        self._ensure_profile_items()
        self._load_profile()
        self._update_window_state()

    def _update_window_state(self, *_args) -> None:
        hwnd = self.target_hwnd()
        if hwnd is None:
            self.window_state_var.set("")
        elif not user32.IsWindow(hwnd):
            self.window_state_var.set("окно закрыто — ждём его появления")
            self.window_state_lbl.configure(text_color=C_ERR)
        elif user32.IsIconic(hwnd):
            self.window_state_var.set("окно свёрнуто — клики могут не обрабатываться")
            self.window_state_lbl.configure(text_color=C_WARN)
        else:
            w, h = winapi.client_size(hwnd)
            self.window_state_var.set(f"окно открыто, {w}×{h}")
            self.window_state_lbl.configure(text_color=C_MUTED)
        if self.profile is not None:
            p = self.profile
            self.profile_var.set(
                f"Профиль: {p.identity.label}  ·  действий: {len(p.action_ids)}, "
                f"последовательностей: {len(p.sequence_ids)}  ·  узнаётся по процессу и классу окна"
            )
        else:
            self.profile_var.set("")

    # ------------------------------------------------------- профиль
    def _ensure_profile_items(self) -> None:
        p = self.profile
        if p is None:
            return
        if not p.action_ids:
            self.library.add_action(p, NamedAction(name="Действие 1"))
        if not p.sequence_ids:
            self.library.add_sequence(p, NamedSequence(name="Последовательность 1"))
        if p.current_action not in p.action_ids:
            p.current_action = p.action_ids[0]
        if p.current_sequence not in p.sequence_ids:
            p.current_sequence = p.sequence_ids[0]

    def _load_profile(self) -> None:
        p = self.profile
        if p is None:
            return
        self._loading = True
        try:
            self.run_mode_seg.set("Последовательность" if p.run_mode == "sequence" else "Одно действие")
            self._refresh_action_menu()
            self._refresh_sequence_menu()
            self._load_action(self.library.actions.get(p.current_action or ""))
            self._load_sequence(self.library.sequences.get(p.current_sequence or ""))
        finally:
            self._loading = False

    @staticmethod
    def _labels(items: list) -> tuple[list[str], dict[str, str]]:
        labels: list[str] = []
        by_label: dict[str, str] = {}
        seen: dict[str, int] = {}
        for item in items:
            seen[item.name] = seen.get(item.name, 0) + 1
            label = item.name if seen[item.name] == 1 else f"{item.name} ({seen[item.name]})"
            labels.append(label)
            by_label[label] = item.id
        return labels, by_label

    def _refresh_action_menu(self) -> None:
        p = self.profile
        items = [self.library.actions[i] for i in p.action_ids if i in self.library.actions] if p else []
        labels, self._action_by_label = self._labels(items)
        self.action_menu.configure(values=labels or ["—"])
        current = next((lab for lab, i in self._action_by_label.items() if p and i == p.current_action), labels[0] if labels else "—")
        self.action_var.set(current)

    def _refresh_sequence_menu(self) -> None:
        p = self.profile
        items = [self.library.sequences[i] for i in p.sequence_ids if i in self.library.sequences] if p else []
        labels, self._sequence_by_label = self._labels(items)
        self.sequence_menu.configure(values=labels or ["—"])
        current = next((lab for lab, i in self._sequence_by_label.items() if p and i == p.current_sequence), labels[0] if labels else "—")
        self.sequence_var.set(current)

    def _load_action(self, action: NamedAction | None) -> None:
        self._current_action = action
        if action is None:
            return
        self._loading = True
        try:
            self.action_editor.set_step(action.step)
            self.interval_min_var.set(f"{action.interval_min:g}")
            self.interval_max_var.set(f"{action.interval_max:g}")
            self.count_var.set(str(action.count))
        finally:
            self._loading = False

    def _load_sequence(self, seq: NamedSequence | None) -> None:
        self._current_sequence = seq
        self._steps = seq.steps if seq is not None else []
        self.repeat_var.set(str(seq.repeat) if seq is not None else "0")
        self._refresh_tree()

    def _quiet_step(self) -> bool:
        e = self.action_editor
        if e._kind() == KIND_MOUSE and e.pos_mode_var.get() == "fixed":
            for v in (e.x_var, e.y_var, e.spread_x_var, e.spread_y_var):
                try:
                    int(v.get().strip())
                except ValueError:
                    return False
        if e._kind() == KIND_KEY and (e._key is None or (e.key_mode_seg.get() == "Символ" and not e._key.char)):
            return False
        if e._kind() == KIND_TEXT and not e.text_var.get():
            return False
        return True

    def _commit_current(self) -> None:
        """Переносит содержимое редакторов в текущие элементы библиотеки (без диалогов)."""
        if self._loading:
            return
        a = self._current_action
        if a is not None and self._quiet_step():
            step = self.action_editor.get_step()
            if step is not None:
                a.step = step
            try:
                a.interval_min = float(self.interval_min_var.get().strip().replace(",", "."))
                a.interval_max = float(self.interval_max_var.get().strip().replace(",", "."))
                a.count = int(self.count_var.get().strip())
            except ValueError:
                pass
        s = self._current_sequence
        if s is not None:
            s.steps = self._steps
            try:
                s.repeat = int(self.repeat_var.get().strip())
            except ValueError:
                pass
        if self.profile is not None:
            self.profile.run_mode = "sequence" if self.run_mode_seg.get() == "Последовательность" else "single"

    # ------------------------------------------------ действия (библиотека)
    def _on_action_pick(self) -> None:
        if self.profile is None:
            return
        self._commit_current()
        action_id = self._action_by_label.get(self.action_var.get())
        if action_id and action_id in self.library.actions:
            self.profile.current_action = action_id
            self._load_action(self.library.actions[action_id])
            self.save_settings()

    def action_new(self) -> None:
        if self.profile is None:
            return
        name = ask_name(self, "Новое действие", "Название действия:", f"Действие {len(self.profile.action_ids) + 1}")
        if not name:
            return
        self._commit_current()
        self.library.add_action(self.profile, NamedAction(name=name))
        self._refresh_action_menu()
        self._load_action(self.library.actions[self.profile.current_action])
        self._update_window_state()
        self.save_settings()

    def action_rename(self) -> None:
        a = self._current_action
        if a is None:
            return
        name = ask_name(self, "Переименовать", "Новое название:", a.name)
        if name:
            a.name = name
            self._refresh_action_menu()
            self.save_settings()

    def action_delete(self) -> None:
        a = self._current_action
        if a is None or self.profile is None:
            return
        others = [w for w in self.library.windows_of_action(a.id) if w != self.profile.identity.label]
        note = f"\n\nОно также привязано к: {', '.join(others)} — там останется." if others else ""
        if not messagebox.askyesno("Удалить действие", f"Удалить «{a.name}» для этого окна?{note}"):
            return
        self.library.unlink_action(self.profile, a.id)
        if not self.library.windows_of_action(a.id):
            self.library.delete_action(a.id)
        self._current_action = None
        self._ensure_profile_items()
        self._refresh_action_menu()
        self._load_action(self.library.actions.get(self.profile.current_action or ""))
        self._update_window_state()
        self.save_settings()

    def action_from_library(self) -> None:
        if self.profile is None:
            return
        items = []
        for a in self.library.actions.values():
            if a.id in self.profile.action_ids:
                continue
            windows = self.library.windows_of_action(a.id)
            items.append((a.id, a.name, ", ".join(windows) if windows else "без окна"))
        if not items:
            messagebox.showinfo("Действия", "В библиотеке нет действий, не привязанных к этому окну.")
            return
        dialog = LibraryPickDialog(self, "Действия других окон", items)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        self._commit_current()
        src = self.library.actions[dialog.result[0]]
        if dialog.result[1] == "copy":
            item = NamedAction.from_dict(src.to_dict())
            item.id = NamedAction().id
            item.name = f"{src.name} (копия)"
        else:
            item = src
        self.library.add_action(self.profile, item)
        self._refresh_action_menu()
        self._load_action(item)
        self._update_window_state()
        self.save_settings()

    # -------------------------------------- последовательности (библиотека)
    def _on_sequence_pick(self) -> None:
        if self.profile is None:
            return
        self._commit_current()
        seq_id = self._sequence_by_label.get(self.sequence_var.get())
        if seq_id and seq_id in self.library.sequences:
            self.profile.current_sequence = seq_id
            self._load_sequence(self.library.sequences[seq_id])
            self.save_settings()

    def sequence_new(self) -> None:
        if self.profile is None:
            return
        name = ask_name(self, "Новая последовательность", "Название:", f"Последовательность {len(self.profile.sequence_ids) + 1}")
        if not name:
            return
        self._commit_current()
        self.library.add_sequence(self.profile, NamedSequence(name=name))
        self._refresh_sequence_menu()
        self._load_sequence(self.library.sequences[self.profile.current_sequence])
        self._update_window_state()
        self.save_settings()

    def sequence_rename(self) -> None:
        s = self._current_sequence
        if s is None:
            return
        name = ask_name(self, "Переименовать", "Новое название:", s.name)
        if name:
            s.name = name
            self._refresh_sequence_menu()
            self.save_settings()

    def sequence_delete(self) -> None:
        s = self._current_sequence
        if s is None or self.profile is None:
            return
        others = [w for w in self.library.windows_of_sequence(s.id) if w != self.profile.identity.label]
        note = f"\n\nОна также привязана к: {', '.join(others)} — там останется." if others else ""
        if not messagebox.askyesno("Удалить последовательность", f"Удалить «{s.name}» для этого окна?{note}"):
            return
        self.library.unlink_sequence(self.profile, s.id)
        if not self.library.windows_of_sequence(s.id):
            self.library.delete_sequence(s.id)
        self._current_sequence = None
        self._ensure_profile_items()
        self._refresh_sequence_menu()
        self._load_sequence(self.library.sequences.get(self.profile.current_sequence or ""))
        self._update_window_state()
        self.save_settings()

    def sequence_from_library(self) -> None:
        if self.profile is None:
            return
        items = []
        for s in self.library.sequences.values():
            if s.id in self.profile.sequence_ids:
                continue
            windows = self.library.windows_of_sequence(s.id)
            items.append((s.id, f"{s.name} ({len(s.steps)} шагов)", ", ".join(windows) if windows else "без окна"))
        if not items:
            messagebox.showinfo("Последовательности", "В библиотеке нет последовательностей, не привязанных к этому окну.")
            return
        dialog = LibraryPickDialog(self, "Последовательности других окон", items)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        self._commit_current()
        src = self.library.sequences[dialog.result[0]]
        if dialog.result[1] == "copy":
            item = NamedSequence.from_dict(src.to_dict())
            item.id = NamedSequence(name="").id
            item.name = f"{src.name} (копия)"
        else:
            item = src
        self.library.add_sequence(self.profile, item)
        self._refresh_sequence_menu()
        self._load_sequence(item)
        self._update_window_state()
        self.save_settings()

    # --------------------------------------------------------- статус
    def _set_status(self, text: str, color: str | None = None) -> None:
        self.status_var.set(text)
        self.status_lbl.configure(text_color=color if color else ("#1f2937", "#e5e7eb"))

    def _set_state(self, state: str, detail: str = "") -> None:
        text, color = {
            tray.STATE_RUNNING: ("  Работает  ", C_RUN),
            tray.STATE_RECORDING: ("  Запись  ", C_REC),
        }.get(state, ("  Остановлен  ", C_IDLE))
        self.status_pill.configure(text=text, fg_color=color)
        self.tray.set_state(state, detail)

    # --------------------------------------------------------- запуск
    @property
    def running(self) -> bool:
        return self._runner is not None and self._runner.is_alive()

    def toggle_run(self) -> None:
        if self._runner is not None:
            self.stop()
        else:
            self.start()

    def _build_run_config(self) -> actions.RunConfig | None:
        hwnd = self.target_hwnd()
        if (hwnd is None or not user32.IsWindow(hwnd)) and self.profile is not None:
            self._try_rebind_window()
            hwnd = self.target_hwnd()
        if hwnd is None or not user32.IsWindow(hwnd):
            messagebox.showwarning("Окно", "Целевое окно не найдено. Запустите программу-цель или выберите другое окно.")
            return None

        if self.run_mode_seg.get() == "Последовательность":
            if not self._steps:
                messagebox.showwarning("Последовательность", "Добавьте хотя бы один шаг.")
                return None
            repeat = parse_int(self.repeat_var.get(), "Повторов")
            if repeat is None:
                return None
            steps = [Step.from_dict(s.to_dict()) for s in self._steps]
        else:
            step = self.action_editor.get_step()
            if step is None:
                return None
            imin = parse_float(self.interval_min_var.get(), "Интервал от", 0.001)
            imax = parse_float(self.interval_max_var.get(), "Интервал до", 0.001)
            repeat = parse_int(self.count_var.get(), "Повторов")
            if imin is None or imax is None or repeat is None:
                return None
            if imin > imax:
                messagebox.showerror("Ошибка", "«Интервал от» не может быть больше «Интервал до».")
                return None
            step.delay_min, step.delay_max = imin, imax
            steps = [step]

        if user32.IsIconic(hwnd):
            answer = messagebox.askyesnocancel(
                "Окно свёрнуто",
                "Выбранное окно свёрнуто. Большинство программ и игр не обрабатывают ввод, "
                "пока окно свёрнуто.\n\nДа — развернуть окно и запустить\nНет — запустить как есть\nОтмена — не запускать",
            )
            if answer is None:
                return None
            if answer:
                user32.ShowWindow(hwnd, winapi.SW_RESTORE)
            self._update_window_state()

        return actions.RunConfig(
            hwnd=hwnd, steps=steps, repeat=repeat,
            input_mode=self.input_mode_var.get(),
            activate_window=self.activate_var.get(),
            restore_cursor=self.restore_cursor_var.get(),
            fallback_child=self._focus_cache.get(hwnd, 0),
        )

    def start(self) -> None:
        if self._runner is not None or self._updating or self.recorder.active:
            return
        config = self._build_run_config()
        if config is None:
            return
        self.save_settings()
        self._runner = actions.Runner(
            config,
            on_progress=lambda it, idx, n, iconic: self.post_ui(self._on_progress, it, idx, n, iconic),
            on_finished=lambda n, reason: self.post_ui(self._on_finished, n, reason),
        )
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.update_btn.configure(state="disabled")
        self._set_state(tray.STATE_RUNNING)
        self._set_status("Работает…", C_RUN)
        self._runner.start()

    def stop(self) -> None:
        if self._runner is not None:
            self._runner.stop()
            self._set_status("Остановка…", C_WARN)

    def _on_progress(self, iteration: int, index: int, performed: int, iconic: bool) -> None:
        if self._runner is None:
            return
        total = len(self._runner.config.steps)
        text = f"Выполнено действий: {performed}"
        if total > 1:
            text += f"  ·  круг {iteration}, шаг {index + 1}/{total}"
        if iconic:
            text += "  ·  окно свёрнуто, ввод может не доходить"
        self._set_status(text, C_WARN if iconic else C_RUN)
        self.tray.set_state(tray.STATE_RUNNING, f"{performed} действий")

    def _on_finished(self, performed: int, reason: str) -> None:
        self._runner = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.update_btn.configure(state="normal")
        self._set_state(tray.STATE_IDLE)
        self._set_status(f"Остановлен. Выполнено действий: {performed}", C_MUTED[1])
        self._update_window_state()
        if reason:
            messagebox.showwarning("Автокликер", reason)

    # ------------------------------------------------- горячие клавиши
    def _register_hotkeys(self) -> None:
        failed = self.hotkey_manager.set_bindings(self._hotkeys)
        if failed:
            names = ", ".join(f"{HOTKEY_TITLES[n]} ({hk.label})" for n, hk in failed.items())
            self._set_status(f"Не удалось занять сочетания: {names} — они заняты другой программой", C_WARN)
        self._refresh_hotkey_labels()
        short = {"toggle": "Старт/Стоп", "capture": "Координаты", "record": "Запись"}
        active = [f"{short[n]}: {hk.label}" for n, hk in self._hotkeys.items() if hk]
        self.hint_var.set("  ·  ".join(active) if active else "Горячие клавиши отключены")

    def _refresh_hotkey_labels(self) -> None:
        for name, lbl in self.hotkey_labels.items():
            hk = self._hotkeys.get(name)
            lbl.configure(text=hk.label if hk else "— отключено —",
                          text_color=("#1f2937", "#e5e7eb") if hk else C_MUTED)

    def change_hotkey(self, name: str, clear: bool = False) -> None:
        if clear:
            self._hotkeys[name] = None
        else:
            dialog = HotkeyCaptureDialog(self)
            self.wait_window(dialog)
            if dialog.result is None:
                return
            for other, hk in self._hotkeys.items():
                if other != name and hk == dialog.result:
                    messagebox.showwarning("Горячие клавиши", f"Сочетание уже используется: {HOTKEY_TITLES[other]}.")
                    return
            self._hotkeys[name] = dialog.result
        self._register_hotkeys()
        self.save_settings()

    def _on_hotkey_thread(self, name: str) -> None:
        self.post_ui(self._on_hotkey, name)

    def _on_hotkey(self, name: str) -> None:
        if name == "toggle":
            self.toggle_run()
        elif name == "capture":
            self.capture_point()
        elif name == "record":
            self.toggle_record()

    # ------------------------------------------------ съём координат
    def set_capture_consumer(self, editor: StepEditor | None) -> None:
        self._capture_consumer = editor

    def capture_point_here(self) -> None:
        hk = self._hotkeys.get("capture")
        if hk:
            messagebox.showinfo(
                "Съём координат",
                f"Наведите курсор на нужную точку целевого окна и нажмите {hk.label}.\n"
                "Окно под курсором автоматически станет целевым.",
            )
        else:
            messagebox.showinfo("Съём координат", "Назначьте сочетание для съёма координат во вкладке «Горячие клавиши».")

    def capture_point(self) -> None:
        sx, sy = winapi.cursor_pos()
        hwnd = winapi.root_window_at(sx, sy)
        own = user32.GetAncestor(self.winfo_id(), winapi.GA_ROOT)
        if not hwnd or hwnd == own:
            self._set_status("Съём координат: наведите курсор на целевое окно, а не на автокликер", C_WARN)
            return
        if hwnd != self.target_hwnd():
            self.select_window(hwnd)
        cx, cy = winapi.screen_to_client(hwnd, sx, sy)
        cw, ch = winapi.client_size(hwnd)
        consumer = self._capture_consumer or self.action_editor
        pos = Position(cx, cy, cw, ch, relative=consumer.relative_var.get())
        consumer.apply_capture(pos)
        pct = pos.percent
        text = f"Координаты сняты: ({cx}, {cy})" + (f", {pct[0]:.1f}% × {pct[1]:.1f}% окна" if pct else "")
        self._set_status(text, C_RUN)
        self.tray.notify(text)
        self.save_settings()

    # ---------------------------------------------------------- запись
    def toggle_record(self) -> None:
        if self.recorder.active:
            steps = self.recorder.stop()
            self.record_btn.configure(text="● Запись")
            self._set_state(tray.STATE_IDLE)
            if steps:
                self._steps.extend(steps)
                self._set_status(f"Запись завершена: добавлено шагов — {len(steps)}", C_RUN)
            else:
                self._set_status("Запись завершена: действий в целевом окне не было", C_WARN)
            self._refresh_tree()
            self.save_settings()
            return
        if self._runner is not None:
            return
        hwnd = self.target_hwnd()
        if hwnd is None or not user32.IsWindow(hwnd):
            messagebox.showwarning("Запись", "Выберите целевое окно.")
            return
        ignore = {hk.vk for hk in self._hotkeys.values() if hk}
        if not self.recorder.start(hwnd, ignore):
            messagebox.showerror("Запись", "Не удалось установить перехват ввода.")
            return
        self.tabs.set("Последовательность")
        self.run_mode_seg.set("Последовательность")
        self.record_btn.configure(text="■ Стоп записи")
        self._set_state(tray.STATE_RECORDING)
        hk = self._hotkeys.get("record")
        self._set_status(
            "Запись… выполняйте действия в целевом окне" + (f", для остановки нажмите {hk.label}" if hk else ""), C_REC
        )

    def _on_record_event(self) -> None:
        if not self.recorder.active:
            return
        preview = self.recorder.preview()
        self._refresh_tree(extra=preview)
        self._set_status(f"Запись… записано шагов: {len(preview)}", C_REC)
        self.tray.set_state(tray.STATE_RECORDING, f"{len(preview)} шагов")

    # ------------------------------------------------ последовательность
    def _refresh_tree(self, extra: list[Step] | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, step in enumerate(self._steps, start=1):
            self.tree.insert("", "end", iid=str(i - 1),
                             values=(i, actions.KIND_LABELS.get(step.kind, step.kind), step.describe(), step.delay_text()))
        base = len(self._steps)
        for j, step in enumerate(extra or [], start=1):
            self.tree.insert("", "end", iid=f"rec{j}", tags=("rec",),
                             values=(base + j, "● " + actions.KIND_LABELS.get(step.kind, step.kind), step.describe(), step.delay_text()))
        if extra:
            self.tree.see(f"rec{len(extra)}")
        name = f"«{self._current_sequence.name}»  ·  " if self._current_sequence else ""
        self.seq_info_var.set(f"{name}шагов: {len(self._steps)}" + (f" + записывается {len(extra)}" if extra else ""))

    def _selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except ValueError:
            return None

    def seq_add(self) -> None:
        dialog = StepDialog(self)
        self.wait_window(dialog)
        if dialog.result is not None:
            idx = self._selected_index()
            if idx is None:
                self._steps.append(dialog.result)
                idx = len(self._steps) - 1
            else:
                self._steps.insert(idx + 1, dialog.result)
                idx += 1
            self._refresh_tree()
            self.tree.selection_set(str(idx))
            self.save_settings()

    def seq_edit(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        dialog = StepDialog(self, self._steps[idx])
        self.wait_window(dialog)
        if dialog.result is not None:
            self._steps[idx] = dialog.result
            self._refresh_tree()
            self.tree.selection_set(str(idx))
            self.save_settings()

    def seq_delete(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        del self._steps[idx]
        self._refresh_tree()
        if self._steps:
            self.tree.selection_set(str(min(idx, len(self._steps) - 1)))
        self.save_settings()

    def seq_move(self, delta: int) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        new = idx + delta
        if not 0 <= new < len(self._steps):
            return
        self._steps[idx], self._steps[new] = self._steps[new], self._steps[idx]
        self._refresh_tree()
        self.tree.selection_set(str(new))
        self.save_settings()

    def seq_save(self) -> None:
        path = filedialog.asksaveasfilename(title="Экспорт последовательности", defaultextension=".json",
                                            filetypes=[("Последовательность", "*.json")],
                                            initialfile=(self._current_sequence.name if self._current_sequence else "sequence") + ".json")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"name": self._current_sequence.name if self._current_sequence else "",
                           "steps": actions.sequence_to_dict(self._steps), "repeat": self.repeat_var.get()},
                          f, ensure_ascii=False, indent=2)
            self._set_status(f"Сохранено: {path}", C_RUN)
        except OSError as exc:
            messagebox.showerror("Экспорт", str(exc))

    def seq_load(self) -> None:
        path = filedialog.askopenfilename(title="Импорт последовательности", filetypes=[("Последовательность", "*.json")])
        if not path or self.profile is None:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            steps = actions.sequence_from_dict(data.get("steps"))
            name = str(data.get("name") or Path(path).stem)
            seq = NamedSequence(name=name, steps=steps, repeat=int(data.get("repeat", 0) or 0))
            self._commit_current()
            self.library.add_sequence(self.profile, seq)
            self._refresh_sequence_menu()
            self._load_sequence(seq)
            self._update_window_state()
            self.save_settings()
            self._set_status(f"Импортировано: {name} ({len(steps)} шагов)", C_RUN)
        except (OSError, ValueError, AttributeError) as exc:
            messagebox.showerror("Импорт", f"Не удалось прочитать файл: {exc}")

    # ------------------------------------------------------------ трей
    def _on_unmap(self, event) -> None:
        if event.widget is self and self.tray_var.get() and self.state() == "iconic":
            self.after(50, self.withdraw)

    def show_window(self) -> None:
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    # ------------------------------------------------------- обновление
    def check_updates(self) -> None:
        if self._runner is not None or self._updating:
            return
        self._updating = True
        self.update_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self._set_status("Проверка обновлений…", C_MUTED[1])
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self) -> None:
        try:
            result = updater.check_and_update(__version__)
        except updater.UpdateError as exc:
            self.post_ui(self._on_update_failed, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.post_ui(self._on_update_failed, f"{type(exc).__name__}: {exc}")
        else:
            self.post_ui(self._on_update_done, result)

    def _on_update_failed(self, message: str) -> None:
        self._updating = False
        self.update_btn.configure(state="normal")
        self.start_btn.configure(state="normal")
        self._set_status("Обновление не выполнено", C_ERR)
        messagebox.showerror("Обновление", message)

    def _on_update_done(self, result: updater.UpdateResult) -> None:
        self._updating = False
        self.update_btn.configure(state="normal")
        self.start_btn.configure(state="normal")
        if not result.updated:
            self._set_status("Обновлений нет")
            messagebox.showinfo("Обновление", result.message)
            return
        self._set_status("Обновление установлено", C_RUN)
        if result.restart_cmd and messagebox.askyesno("Обновление", f"{result.message}\n\nПерезапустить программу сейчас?"):
            self._restart(result.restart_cmd)
        else:
            messagebox.showinfo("Обновление", "Изменения вступят в силу после перезапуска программы.")

    def _restart(self, cmd: list[str]) -> None:
        self.save_settings()
        self._shutdown()
        try:
            subprocess.Popen(cmd, cwd=str(updater.app_dir()), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            messagebox.showerror("Обновление", f"Не удалось перезапустить: {exc}")
            return
        self.destroy()
        sys.exit(0)

    # ----------------------------------------------------------- выход
    def _shutdown(self) -> None:
        self._closing = True
        for timer_id in self._timers.values():
            try:
                self.after_cancel(timer_id)
            except Exception:  # noqa: BLE001
                pass
        if self._runner is not None:
            self._runner.stop()
            self._runner.join(timeout=1.5)
        if self.recorder.active:
            self.recorder.stop()
        self.hotkey_manager.stop()
        self.tray.stop()

    def quit_app(self) -> None:
        self.save_settings()
        self._shutdown()
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
