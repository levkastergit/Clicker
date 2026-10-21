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
from winapi import Hotkey, KeySpec, user32

__version__ = "2.0.0"

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


# ==========================================================================
# Главное окно
# ==========================================================================
class App(ctk.CTk):
    def __init__(self) -> None:
        winapi.enable_dpi_awareness()
        super().__init__()
        self.title(f"Автокликер {__version__}")
        self.minsize(820, 640)

        self._settings = settings.load()
        # Tk не потокобезопасен: рабочие потоки кладут вызовы в очередь,
        # главный поток выполняет их по порядку.
        self._ui_queue: queue.Queue[tuple[Callable, tuple]] = queue.Queue()
        self._windows: list[tuple[int, str]] = []
        self._window_by_label: dict[str, int] = {}
        self._runner: actions.Runner | None = None
        self._updating = False
        self._capture_consumer: StepEditor | None = None
        self._steps: list[Step] = actions.sequence_from_dict(self._settings.get("sequence", {}).get("steps"))
        self._hotkeys: dict[str, Hotkey | None] = {
            name: Hotkey.from_dict(self._settings.get("hotkeys", {}).get(name)) or DEFAULT_HOTKEYS[name]
            for name in DEFAULT_HOTKEYS
        }
        if "hotkeys" in self._settings:
            for name in DEFAULT_HOTKEYS:
                if name in self._settings["hotkeys"] and self._settings["hotkeys"][name] is None:
                    self._hotkeys[name] = None

        ctk.set_appearance_mode(self._settings.get("appearance", "dark"))
        ctk.set_default_color_theme("dark-blue")
        self._style_treeview()

        self._build_ui()
        self._apply_settings()
        self._set_icon()

        self.hotkey_manager = hotkeys.HotkeyManager(self._on_hotkey_thread)
        self._register_hotkeys()
        self.recorder = recorder.Recorder()
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
        self._closing = False
        # Запоминаем окно фокуса цели, пока она активна: у неактивного окна фокус сброшен.
        self._focus_cache: dict[int, int] = {}
        self.after(30, self._drain_ui_queue)
        self.after(1000, self._poll_target_focus)

    def _poll_target_focus(self) -> None:
        if self._closing:
            return
        hwnd = self.target_hwnd()
        if hwnd and user32.IsWindow(hwnd):
            focus = winapi.focused_child(hwnd)
            if focus and focus != hwnd:
                self._focus_cache[hwnd] = focus
        self.after(1000, self._poll_target_focus)

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
            except Exception:  # noqa: BLE001 — ошибка одного вызова не должна остановить очередь
                import traceback
                traceback.print_exc()
        if not self._closing:
            self.after(30, self._drain_ui_queue)

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
        sel = "#1f6aa5"
        style.configure("Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=30, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=head, foreground=fg, relief="flat",
                        font=("Segoe UI Semibold", 10), padding=6)
        style.map("Treeview", background=[("selected", sel)], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", head)])

    def _build_ui(self) -> None:
        root = ctk.CTkFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=18, pady=(14, 10))

        # --- шапка
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

        # --- целевое окно
        card = ctk.CTkFrame(root, fg_color=C_CARD, corner_radius=12)
        card.pack(fill="x", pady=(0, 10))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(row, text="Целевое окно", font=ctk.CTkFont(weight="bold"), width=110, anchor="w").pack(side="left")
        self.window_var = ctk.StringVar()
        self.window_menu = ctk.CTkOptionMenu(row, values=[""], variable=self.window_var, width=430,
                                             dynamic_resizing=False, command=lambda _v: self._update_window_state())
        self.window_menu.pack(side="left")
        ctk.CTkButton(row, text="Обновить список", width=130, command=self.refresh_windows).pack(side="left", padx=(8, 0))
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(row2, text="Что запускать", width=110, anchor="w").pack(side="left")
        self.run_mode_seg = ctk.CTkSegmentedButton(row2, values=["Одно действие", "Последовательность"])
        self.run_mode_seg.set("Одно действие")
        self.run_mode_seg.pack(side="left")
        self.window_state_var = ctk.StringVar()
        self.window_state_lbl = ctk.CTkLabel(row2, textvariable=self.window_state_var, text_color=C_MUTED, anchor="w")
        self.window_state_lbl.pack(side="left", padx=(16, 0))

        # --- вкладки
        self.tabs = ctk.CTkTabview(root, corner_radius=12)
        self.tabs.pack(fill="both", expand=True)
        self._build_action_tab(self.tabs.add("Действие"))
        self._build_sequence_tab(self.tabs.add("Последовательность"))
        self._build_hotkeys_tab(self.tabs.add("Горячие клавиши"))
        self._build_settings_tab(self.tabs.add("Настройки"))

        # --- строка статуса
        bar = ctk.CTkFrame(root, fg_color="transparent")
        bar.pack(fill="x", pady=(8, 0))
        self.status_var = ctk.StringVar(value="Готов")
        self.status_lbl = ctk.CTkLabel(bar, textvariable=self.status_var, anchor="w")
        self.status_lbl.pack(side="left")
        self.hint_var = ctk.StringVar()
        ctk.CTkLabel(bar, textvariable=self.hint_var, text_color=C_MUTED).pack(side="right")

    def _build_action_tab(self, tab) -> None:
        self.action_editor = StepEditor(tab, self, show_pause=False)
        self.action_editor.pack(fill="x", padx=6, pady=(6, 10))

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
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(6, 6))
        ctk.CTkButton(top, text="＋ Добавить", width=110, command=self.seq_add).pack(side="left")
        ctk.CTkButton(top, text="Изменить", width=90, command=self.seq_edit).pack(side="left", padx=(6, 0))
        ctk.CTkButton(top, text="Удалить", width=90, fg_color=C_IDLE, command=self.seq_delete).pack(side="left", padx=(6, 0))
        ctk.CTkButton(top, text="▲", width=36, fg_color=C_IDLE, command=lambda: self.seq_move(-1)).pack(side="left", padx=(12, 0))
        ctk.CTkButton(top, text="▼", width=36, fg_color=C_IDLE, command=lambda: self.seq_move(1)).pack(side="left", padx=(4, 0))
        self.record_btn = ctk.CTkButton(top, text="● Запись", width=100, fg_color=C_REC, hover_color="#a2443e",
                                        command=self.toggle_record)
        self.record_btn.pack(side="left", padx=(12, 0))
        ctk.CTkButton(top, text="Открыть…", width=90, fg_color=C_IDLE, command=self.seq_load).pack(side="right")
        ctk.CTkButton(top, text="Сохранить…", width=100, fg_color=C_IDLE, command=self.seq_save).pack(side="right", padx=(0, 6))

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
            tab, wraplength=700, justify="left", anchor="w", text_color=C_MUTED,
            text="Съём координат: откройте целевое окно, наведите курсор на нужную точку и нажмите сочетание. "
                 "Окно под курсором станет целевым, координаты запишутся во вкладку «Действие» или в открытый "
                 "диалог шага. Запись: нажмите сочетание, выполните клики и нажатия в целевом окне, нажмите ещё раз — "
                 "шаги добавятся в последовательность.",
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
        ctk.CTkLabel(card, wraplength=700, justify="left", anchor="w", text_color=C_MUTED,
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
        self.appearance_var = ctk.StringVar(value="dark")
        ctk.CTkSegmentedButton(row, values=["Тёмная", "Светлая", "Системная"],
                               command=self._on_appearance).pack(side="left")
        self.appearance_seg = row.winfo_children()[-1]
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

    # ------------------------------------------------------- настройки
    def _apply_settings(self) -> None:
        s = self._settings
        self.run_mode_seg.set("Последовательность" if s.get("run_mode") == "sequence" else "Одно действие")
        single = s.get("single") or {}
        if single.get("step"):
            try:
                self.action_editor.set_step(Step.from_dict(single["step"]))
            except (TypeError, ValueError, KeyError):
                pass
        self.interval_min_var.set(str(single.get("interval_min", "0.4")))
        self.interval_max_var.set(str(single.get("interval_max", "0.6")))
        self.count_var.set(str(single.get("count", "0")))
        self.repeat_var.set(str((s.get("sequence") or {}).get("repeat", "0")))
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
                # Восстанавливаем только размер: позиция может оказаться за пределами экрана.
                self.geometry(geo.split("+", 1)[0])
            except Exception:  # noqa: BLE001
                pass

    def _collect_settings(self) -> dict:
        step = self.action_editor.get_step() if self._quiet_step() else None
        return {
            "version": 2,
            "window_title": self.window_var.get(),
            "run_mode": "sequence" if self.run_mode_seg.get() == "Последовательность" else "single",
            "single": {
                "step": step.to_dict() if step else (self._settings.get("single") or {}).get("step"),
                "interval_min": self.interval_min_var.get(),
                "interval_max": self.interval_max_var.get(),
                "count": self.count_var.get(),
            },
            "sequence": {"steps": actions.sequence_to_dict(self._steps), "repeat": self.repeat_var.get()},
            "hotkeys": {name: (hk.to_dict() if hk else None) for name, hk in self._hotkeys.items()},
            "input_mode": self.input_mode_var.get(),
            "activate_window": self.activate_var.get(),
            "restore_cursor": self.restore_cursor_var.get(),
            "appearance": self._appearance_code(),
            "minimize_to_tray": self.tray_var.get(),
            "geometry": self.geometry(),
        }

    def _quiet_step(self) -> bool:
        """Можно ли собрать шаг без диалогов об ошибках (для автосохранения)."""
        e = self.action_editor
        if e._kind() == KIND_MOUSE and e.pos_mode_var.get() == "fixed":
            for v in (e.x_var, e.y_var, e.spread_x_var, e.spread_y_var):
                try:
                    int(v.get().strip())
                except ValueError:
                    return False
        if e._kind() == KIND_KEY and e._key is None:
            return False
        if e._kind() == KIND_TEXT and not e.text_var.get():
            return False
        return True

    def save_settings(self) -> None:
        try:
            self._settings = self._collect_settings()
            settings.save(self._settings)
        except Exception:  # noqa: BLE001 — сохранение не должно мешать работе
            pass

    def _appearance_code(self) -> str:
        return {"Тёмная": "dark", "Светлая": "light", "Системная": "system"}.get(self.appearance_seg.get(), "dark")

    def _on_appearance(self, _value: str) -> None:
        ctk.set_appearance_mode(self._appearance_code())
        self.after(100, self._style_treeview)

    # ----------------------------------------------------------- окна
    def refresh_windows(self, select_saved: bool = False) -> None:
        own = user32.GetAncestor(self.winfo_id(), winapi.GA_ROOT)
        self._windows = [(h, t) for h, t in winapi.enum_windows() if h != own]
        labels: list[str] = []
        self._window_by_label = {}
        seen: dict[str, int] = {}
        for hwnd, title in self._windows:
            seen[title] = seen.get(title, 0) + 1
            label = title if seen[title] == 1 else f"{title} ({seen[title]})"
            labels.append(label)
            self._window_by_label[label] = hwnd
        if not labels:
            labels = ["— окна не найдены —"]
        self.window_menu.configure(values=labels)
        current = self.window_var.get()
        wanted = self._settings.get("window_title") if select_saved else current
        if wanted in self._window_by_label:
            self.window_var.set(wanted)
        else:
            self.window_var.set(labels[0])
        self._update_window_state()
        self._set_status(f"Найдено окон: {len(self._windows)}")

    def target_hwnd(self) -> int | None:
        return self._window_by_label.get(self.window_var.get())

    def select_window(self, hwnd: int) -> None:
        for label, h in self._window_by_label.items():
            if h == hwnd:
                self.window_var.set(label)
                self._update_window_state()
                return
        self.refresh_windows()
        for label, h in self._window_by_label.items():
            if h == hwnd:
                self.window_var.set(label)
                self._update_window_state()
                return

    def _update_window_state(self, *_args) -> None:
        hwnd = self.target_hwnd()
        if hwnd is None:
            self.window_state_var.set("")
            return
        if not user32.IsWindow(hwnd):
            self.window_state_var.set("окно закрыто — обновите список")
            self.window_state_lbl.configure(text_color=C_ERR)
        elif user32.IsIconic(hwnd):
            self.window_state_var.set("окно свёрнуто — клики могут не обрабатываться")
            self.window_state_lbl.configure(text_color=C_WARN)
        else:
            w, h = winapi.client_size(hwnd)
            self.window_state_var.set(f"окно открыто, {w}×{h}")
            self.window_state_lbl.configure(text_color=C_MUTED)

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
        if self.running:
            self.stop()
        else:
            self.start()

    def _build_run_config(self) -> actions.RunConfig | None:
        hwnd = self.target_hwnd()
        if hwnd is None or not user32.IsWindow(hwnd):
            messagebox.showwarning("Окно", "Выберите существующее окно из списка.")
            self.refresh_windows()
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
        # _runner обнуляется только в _on_finished, поэтому повторный старт до
        # обработки завершения невозможен.
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
        active = [hk.label for hk in self._hotkeys.values() if hk]
        self.hint_var.set("  ·  ".join(
            f"{HOTKEY_TITLES[n].split(' ')[0] if n != 'toggle' else 'Старт/Стоп'}: {hk.label}"
            for n, hk in self._hotkeys.items() if hk
        ) if active else "Горячие клавиши отключены")

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

    # ---------------------------------------------------------- запись
    def toggle_record(self) -> None:
        if self.recorder.active:
            steps = self.recorder.stop()
            self.record_btn.configure(text="● Запись")
            self._set_state(tray.STATE_IDLE)
            if steps:
                self._steps.extend(steps)
                self._refresh_tree()
                self.tabs.set("Последовательность")
                self.run_mode_seg.set("Последовательность")
                self._set_status(f"Запись завершена: добавлено шагов — {len(steps)}", C_RUN)
            else:
                self._set_status("Запись завершена: действий в целевом окне не было", C_WARN)
            self.save_settings()
            return
        if self.running:
            return
        hwnd = self.target_hwnd()
        if hwnd is None or not user32.IsWindow(hwnd):
            messagebox.showwarning("Запись", "Выберите целевое окно.")
            return
        ignore = {hk.vk for hk in self._hotkeys.values() if hk}
        if not self.recorder.start(hwnd, ignore):
            messagebox.showerror("Запись", "Не удалось установить перехват ввода.")
            return
        self.record_btn.configure(text="■ Стоп записи")
        self._set_state(tray.STATE_RECORDING)
        hk = self._hotkeys.get("record")
        self._set_status(
            "Запись… выполняйте действия в целевом окне" + (f", для остановки нажмите {hk.label}" if hk else ""), C_REC
        )

    # ------------------------------------------------ последовательность
    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, step in enumerate(self._steps, start=1):
            self.tree.insert("", "end", iid=str(i - 1),
                             values=(i, actions.KIND_LABELS.get(step.kind, step.kind), step.describe(), step.delay_text()))
        self.seq_info_var.set(f"Шагов: {len(self._steps)}")

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
        path = filedialog.asksaveasfilename(title="Сохранить последовательность", defaultextension=".json",
                                            filetypes=[("Последовательность", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"steps": actions.sequence_to_dict(self._steps), "repeat": self.repeat_var.get()},
                          f, ensure_ascii=False, indent=2)
            self._set_status(f"Сохранено: {path}", C_RUN)
        except OSError as exc:
            messagebox.showerror("Сохранение", str(exc))

    def seq_load(self) -> None:
        path = filedialog.askopenfilename(title="Открыть последовательность", filetypes=[("Последовательность", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._steps = actions.sequence_from_dict(data.get("steps"))
            self.repeat_var.set(str(data.get("repeat", "0")))
            self._refresh_tree()
            self.save_settings()
            self._set_status(f"Открыто: {path}", C_RUN)
        except (OSError, ValueError, AttributeError) as exc:
            messagebox.showerror("Открытие", f"Не удалось прочитать файл: {exc}")

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
        if self.running or self._updating:
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
