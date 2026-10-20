"""
Автокликер для Windows: выбор окна, кнопки мыши/клавиши, интервал, старт/стоп.
"""

from __future__ import annotations

import random
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import updater
import winapi
from winapi import MOUSE_ACTIONS, KeySpec, user32

__version__ = "1.2.0"

# Пауза между нажатием и отпусканием кнопки/клавиши, сек.
PRESS_DURATION = 0.02
# Сколько подряд неудачных PostMessage считать потерей окна.
MAX_POST_FAILURES = 5

HINT_COLOR = "#6b6b6b"
OK_COLOR = "#1a7f37"
WARN_COLOR = "#b45309"
ERR_COLOR = "#b91c1c"


class KeyCaptureDialog(tk.Toplevel):
    """Окно «нажмите клавишу»: запоминает VK-код и символ в текущей раскладке."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.result: KeySpec | None = None
        self.title("Захват клавиши")
        self.resizable(False, False)
        self.transient(parent)

        body = ttk.Frame(self, padding=20)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Нажмите нужную клавишу", style="Title.TLabel").pack()
        ttk.Label(
            body,
            text="Переключите раскладку заранее: символ запоминается\n"
            "в той раскладке, которая активна в момент нажатия.",
            style="Hint.TLabel",
            justify=tk.CENTER,
        ).pack(pady=(6, 12))
        self.preview_var = tk.StringVar(value="…")
        ttk.Label(body, textvariable=self.preview_var, style="Big.TLabel").pack(pady=(0, 12))
        ttk.Button(body, text="Отмена", command=self.destroy).pack()

        self.bind("<KeyPress>", self._on_key)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(px, 0)}+{max(py, 0)}")
        self.grab_set()
        self.focus_force()

    def _on_key(self, event: tk.Event) -> str:
        vk = int(event.keycode)
        char = event.char if event.char and event.char.isprintable() else None
        if char == " ":
            name = "Пробел"
        elif char:
            name = char
        else:
            name = event.keysym
        self.result = KeySpec(name, vk, char)
        self.preview_var.set(self.result.label)
        self.after(150, self.destroy)
        return "break"


class AutoClickerApp:
    def __init__(self) -> None:
        winapi.enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.title(f"Автокликер {__version__}")
        self.root.resizable(False, False)

        self._windows: list[tuple[int, str]] = []
        self._running = False
        self._updating = False
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._key: KeySpec | None = None

        self._setup_style()
        self._build_ui()
        # Ширина окна рассчитывается по самому широкому режиму (клавиша),
        # чтобы окно не «прыгало» при переключении Мышь/Клавиша.
        self.root.update_idletasks()
        self.root.minsize(self.root.winfo_reqwidth(), 0)
        self._on_action_type_change()
        self.refresh_windows()

    # --------------------------------------------------------------- style
    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        else:
            style.theme_use("clam")

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family="Segoe UI", size=10)
            except tk.TclError:
                pass

        style.configure("TLabelframe", padding=(12, 8, 12, 12))
        style.configure("TLabelframe.Label", font=("Segoe UI Semibold", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 12))
        style.configure("Big.TLabel", font=("Segoe UI", 16))
        style.configure("Hint.TLabel", foreground=HINT_COLOR, font=("Segoe UI", 9))
        style.configure("Status.TLabel", font=("Segoe UI", 10))
        style.configure("Version.TLabel", foreground=HINT_COLOR, font=("Segoe UI", 9))
        style.configure("Start.TButton", font=("Segoe UI Semibold", 10), padding=(18, 6))
        style.configure("Stop.TButton", font=("Segoe UI Semibold", 10), padding=(18, 6))
        style.configure("TButton", padding=(10, 4))
        style.configure("TEntry", padding=3)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(16, 14, 16, 10))
        outer.pack(fill=tk.BOTH, expand=True)

        self._build_window_section(outer)
        self._build_action_section(outer)
        self._build_params_section(outer)
        self._build_controls(outer)
        self._build_status_bar(outer)

    def _build_window_section(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Целевое окно")
        box.pack(fill=tk.X, pady=(0, 10))

        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(box, textvariable=self.window_var, state="readonly")
        self.window_combo.grid(row=0, column=0, sticky=tk.EW)
        self.window_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_window_state())
        ttk.Button(box, text="Обновить список", command=self.refresh_windows).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.window_state_var = tk.StringVar()
        self.window_state_lbl = ttk.Label(box, textvariable=self.window_state_var, style="Hint.TLabel")
        self.window_state_lbl.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        box.columnconfigure(0, weight=1)

    def _build_action_section(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Действие")
        box.pack(fill=tk.X, pady=(0, 10))

        self.action_type_var = tk.StringVar(value="mouse")
        type_row = ttk.Frame(box)
        type_row.grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(
            type_row, text="Клик мышью", value="mouse",
            variable=self.action_type_var, command=self._on_action_type_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            type_row, text="Нажатие клавиши", value="key",
            variable=self.action_type_var, command=self._on_action_type_change,
        ).pack(side=tk.LEFT, padx=(16, 0))

        # --- мышь
        self.mouse_frame = ttk.Frame(box)
        self.mouse_frame.grid(row=1, column=0, sticky=tk.EW, pady=(8, 0))
        ttk.Label(self.mouse_frame, text="Кнопка:").grid(row=0, column=0, sticky=tk.W)
        self.mouse_var = tk.StringVar(value="Левая кнопка мыши")
        ttk.Combobox(
            self.mouse_frame, textvariable=self.mouse_var,
            values=list(MOUSE_ACTIONS.keys()), state="readonly", width=24,
        ).grid(row=0, column=1, sticky=tk.W, padx=(8, 0))

        # --- клавиша
        self.key_frame = ttk.Frame(box)
        self.key_frame.grid(row=1, column=0, sticky=tk.EW, pady=(8, 0))

        ttk.Label(self.key_frame, text="Группа:").grid(row=0, column=0, sticky=tk.W)
        self.key_group_var = tk.StringVar(value=winapi.KEY_GROUP_NAMES[0])
        group_combo = ttk.Combobox(
            self.key_frame, textvariable=self.key_group_var,
            values=winapi.KEY_GROUP_NAMES, state="readonly", width=22,
        )
        group_combo.grid(row=0, column=1, sticky=tk.W, padx=(8, 0))
        group_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_key_group_change())

        ttk.Label(self.key_frame, text="Клавиша:").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.key_var = tk.StringVar()
        self.key_combo = ttk.Combobox(self.key_frame, textvariable=self.key_var, state="readonly", width=22)
        self.key_combo.grid(row=1, column=1, sticky=tk.W, padx=(8, 0), pady=(6, 0))
        self.key_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_key_selected())
        ttk.Button(self.key_frame, text="Нажать клавишу…", command=self._capture_key).grid(
            row=1, column=2, sticky=tk.W, padx=(8, 0), pady=(6, 0)
        )

        ttk.Label(self.key_frame, text="Отправлять как:").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        self.key_mode_var = tk.StringVar(value="key")
        mode_row = ttk.Frame(self.key_frame)
        mode_row.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=(8, 0), pady=(6, 0))
        ttk.Radiobutton(mode_row, text="Нажатие клавиши", value="key", variable=self.key_mode_var).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_row, text="Символ (WM_CHAR)", value="char", variable=self.key_mode_var).pack(
            side=tk.LEFT, padx=(12, 0)
        )

        self.key_info_var = tk.StringVar()
        ttk.Label(self.key_frame, textvariable=self.key_info_var, style="Hint.TLabel", wraplength=500).grid(
            row=3, column=0, columnspan=3, sticky=tk.W, pady=(6, 0)
        )
        self._on_key_group_change()

    def _build_params_section(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Параметры")
        box.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(box, text="Интервал, сек:").grid(row=0, column=0, sticky=tk.W)
        row = ttk.Frame(box)
        row.grid(row=0, column=1, sticky=tk.W, padx=(8, 0))
        self.interval_min_var = tk.StringVar(value="0.4")
        self.interval_max_var = tk.StringVar(value="0.6")
        ttk.Label(row, text="от").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.interval_min_var, width=7).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(row, text="до").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.interval_max_var, width=7).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(row, text="случайная пауза в диапазоне", style="Hint.TLabel").pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(box, text="Позиция клика:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=1, column=1, sticky=tk.W, padx=(8, 0), pady=(8, 0))
        self.click_x_var = tk.StringVar(value="50")
        self.click_y_var = tk.StringVar(value="50")
        ttk.Label(row, text="X").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.click_x_var, width=7).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(row, text="Y").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.click_y_var, width=7).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(row, text="от левого верхнего угла окна", style="Hint.TLabel").pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(box, text="Разброс, ± пикс:").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=2, column=1, sticky=tk.W, padx=(8, 0), pady=(8, 0))
        self.spread_x_var = tk.StringVar(value="3")
        self.spread_y_var = tk.StringVar(value="3")
        ttk.Label(row, text="X").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.spread_x_var, width=7).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(row, text="Y").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.spread_y_var, width=7).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(row, text="0 = точно в указанную точку", style="Hint.TLabel").pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(box, text="Количество:").grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=3, column=1, sticky=tk.W, padx=(8, 0), pady=(8, 0))
        self.count_var = tk.StringVar(value="0")
        ttk.Entry(row, textvariable=self.count_var, width=7).pack(side=tk.LEFT)
        ttk.Label(row, text="0 = бесконечно", style="Hint.TLabel").pack(side=tk.LEFT, padx=(10, 0))

    def _build_controls(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(4, 8))
        self.start_btn = ttk.Button(row, text="▶  Старт", style="Start.TButton", command=self.start)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(row, text="■  Стоп", style="Stop.TButton", command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.update_btn = ttk.Button(row, text="Обновить программу", command=self.check_updates)
        self.update_btn.pack(side=tk.RIGHT)

    def _build_status_bar(self, parent: ttk.Frame) -> None:
        ttk.Separator(parent).pack(fill=tk.X)
        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(6, 0))
        self.status_var = tk.StringVar(value="Готов")
        self.status_lbl = ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel", foreground=OK_COLOR)
        self.status_lbl.pack(side=tk.LEFT)
        ttk.Label(bar, text=f"v{__version__}", style="Version.TLabel").pack(side=tk.RIGHT)

    # ------------------------------------------------------------- helpers
    def _set_status(self, text: str, color: str = OK_COLOR) -> None:
        self.status_var.set(text)
        self.status_lbl.configure(foreground=color)

    def _on_action_type_change(self) -> None:
        if self.action_type_var.get() == "mouse":
            self.key_frame.grid_remove()
            self.mouse_frame.grid()
        else:
            self.mouse_frame.grid_remove()
            self.key_frame.grid()

    def _on_key_group_change(self) -> None:
        keys = winapi.KEY_GROUPS[self.key_group_var.get()]
        self.key_combo.configure(values=[k.label for k in keys])
        self.key_combo.current(0)
        self._on_key_selected()

    def _on_key_selected(self) -> None:
        self._key = winapi.key_by_label(self.key_var.get())
        self._refresh_key_info()

    def _capture_key(self) -> None:
        dialog = KeyCaptureDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result is not None:
            self._key = dialog.result
            self.key_var.set("")
            self._refresh_key_info(captured=True)

    def _refresh_key_info(self, captured: bool = False) -> None:
        key = self._key
        if key is None:
            self.key_info_var.set("Клавиша не выбрана.")
            return
        source = "Захвачено с клавиатуры" if captured else "Выбрано"
        char_info = f"символ «{key.char}»" if key.char and key.char.strip() else "без символа"
        self.key_info_var.set(
            f"{source}: {key.label}, {char_info}. "
            "Режим «Нажатие клавиши» отправляет VK-код, и символ определяет раскладка целевого окна. "
            "Режим «Символ» отправляет сам символ независимо от раскладки (подходит для текстовых полей)."
        )

    def refresh_windows(self) -> None:
        self._windows = winapi.enum_windows()
        titles = [title for _, title in self._windows]
        self.window_combo.configure(values=titles)
        if titles:
            self.window_combo.current(0)
            self._set_status(f"Найдено окон: {len(titles)}")
        else:
            self.window_var.set("")
            self._set_status("Окна не найдены", WARN_COLOR)
        self._update_window_state()

    def _update_window_state(self) -> None:
        hwnd = self._resolve_hwnd()
        if hwnd is None:
            self.window_state_var.set("")
            return
        if not user32.IsWindow(hwnd):
            self.window_state_var.set("Окно больше не существует — обновите список.")
            self.window_state_lbl.configure(foreground=ERR_COLOR)
        elif user32.IsIconic(hwnd):
            self.window_state_var.set(
                "Окно свёрнуто. Многие программы не обрабатывают клики в свёрнутом окне."
            )
            self.window_state_lbl.configure(foreground=WARN_COLOR)
        else:
            self.window_state_var.set(f"Окно открыто, HWND 0x{hwnd:X}.")
            self.window_state_lbl.configure(foreground=HINT_COLOR)

    def _resolve_hwnd(self) -> int | None:
        # Берём по индексу, а не по заголовку: у нескольких окон может быть
        # одинаковое название.
        index = self.window_combo.current()
        if 0 <= index < len(self._windows):
            return self._windows[index][0]
        return None

    # --------------------------------------------------------- validation
    def _parse_float(self, value: str, name: str, minimum: float = 0.01) -> float | None:
        try:
            parsed = float(value.strip().replace(",", "."))
        except ValueError:
            messagebox.showerror("Ошибка", f"Некорректное значение: {name}")
            return None
        if parsed < minimum:
            messagebox.showerror("Ошибка", f"{name} должно быть не меньше {minimum}")
            return None
        return parsed

    def _parse_int(self, value: str, name: str, minimum: int = 0) -> int | None:
        try:
            parsed = int(value.strip())
        except ValueError:
            messagebox.showerror("Ошибка", f"Некорректное значение: {name}")
            return None
        if parsed < minimum:
            messagebox.showerror("Ошибка", f"{name} должно быть не меньше {minimum}")
            return None
        return parsed

    # ------------------------------------------------------------ control
    def start(self) -> None:
        if self._running or self._updating:
            return

        hwnd = self._resolve_hwnd()
        if hwnd is None:
            messagebox.showwarning("Окно", "Выберите окно из списка.")
            return
        if not user32.IsWindow(hwnd):
            messagebox.showwarning("Окно", "Выбранное окно больше не существует. Обновите список.")
            self.refresh_windows()
            return

        if user32.IsIconic(hwnd):
            answer = messagebox.askyesnocancel(
                "Окно свёрнуто",
                "Выбранное окно свёрнуто. Большинство программ и игр не обрабатывают "
                "клики и клавиши, пока окно свёрнуто.\n\n"
                "Да — развернуть окно и запустить\n"
                "Нет — запустить как есть\n"
                "Отмена — не запускать",
            )
            if answer is None:
                return
            if answer:
                user32.ShowWindow(hwnd, winapi.SW_RESTORE)
                time.sleep(0.3)
            self._update_window_state()

        interval_min = self._parse_float(self.interval_min_var.get(), "Минимальный интервал")
        interval_max = self._parse_float(self.interval_max_var.get(), "Максимальный интервал")
        if interval_min is None or interval_max is None:
            return
        if interval_min > interval_max:
            messagebox.showerror("Ошибка", "Минимальный интервал не может быть больше максимального.")
            return

        max_count = self._parse_int(self.count_var.get(), "Количество")
        if max_count is None:
            return

        action_type = self.action_type_var.get()
        click_x = click_y = spread_x = spread_y = 0
        mouse_target = self.mouse_var.get()
        key: KeySpec | None = None
        key_mode = self.key_mode_var.get()

        if action_type == "mouse":
            if mouse_target not in MOUSE_ACTIONS:
                messagebox.showwarning("Действие", "Выберите кнопку мыши.")
                return
            click_x = self._parse_int(self.click_x_var.get(), "X")
            click_y = self._parse_int(self.click_y_var.get(), "Y")
            spread_x = self._parse_int(self.spread_x_var.get(), "Разброс X")
            spread_y = self._parse_int(self.spread_y_var.get(), "Разброс Y")
            if None in (click_x, click_y, spread_x, spread_y):
                return
        else:
            key = self._key
            if key is None:
                messagebox.showwarning("Действие", "Выберите клавишу или нажмите «Нажать клавишу…».")
                return
            if key_mode == "char" and not key.char:
                messagebox.showwarning(
                    "Действие",
                    f"У клавиши {key.name} нет печатного символа. "
                    "Выберите режим «Нажатие клавиши».",
                )
                return

        self._stop_event.clear()
        self._running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.update_btn.configure(state=tk.DISABLED)
        self._set_status("Работает…")

        self._worker = threading.Thread(
            target=self._click_loop,
            kwargs=dict(
                hwnd=hwnd,
                interval_min=interval_min,
                interval_max=interval_max,
                max_count=max_count,
                action_type=action_type,
                mouse_target=mouse_target,
                key=key,
                key_mode=key_mode,
                click_x=click_x,
                click_y=click_y,
                spread_x=spread_x,
                spread_y=spread_y,
            ),
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        self._set_status("Остановка…", WARN_COLOR)

    @staticmethod
    def _random_click_position(x: int, y: int, spread_x: int, spread_y: int) -> tuple[int, int]:
        if spread_x > 0:
            x += random.randint(-spread_x, spread_x)
        if spread_y > 0:
            y += random.randint(-spread_y, spread_y)
        return max(0, x), max(0, y)

    def _click_loop(
        self,
        hwnd: int,
        interval_min: float,
        interval_max: float,
        max_count: int,
        action_type: str,
        mouse_target: str,
        key: KeySpec | None,
        key_mode: str,
        click_x: int,
        click_y: int,
        spread_x: int,
        spread_y: int,
    ) -> None:
        performed = 0
        reason = ""
        failures = 0
        try:
            while not self._stop_event.is_set():
                if max_count > 0 and performed >= max_count:
                    break
                if not user32.IsWindow(hwnd):
                    reason = "Окно закрыто — кликер остановлен."
                    break

                if action_type == "mouse":
                    x, y = self._random_click_position(click_x, click_y, spread_x, spread_y)
                    ok = self._send_mouse(hwnd, mouse_target, x, y)
                else:
                    assert key is not None
                    ok = self._send_key(hwnd, key, key_mode)

                if ok:
                    failures = 0
                    performed += 1
                else:
                    failures += 1
                    if failures >= MAX_POST_FAILURES:
                        reason = (
                            f"Окно не принимает сообщения ({MAX_POST_FAILURES} ошибок подряд, "
                            f"код {winapi.last_error()}). Кликер остановлен."
                        )
                        break

                iconic = bool(user32.IsIconic(hwnd))
                self.root.after(0, self._report_progress, performed, iconic)

                pause = random.uniform(interval_min, interval_max)
                if self._stop_event.wait(pause):
                    break
        finally:
            self.root.after(0, self._on_worker_finished, performed, reason)

    @staticmethod
    def _send_mouse(hwnd: int, target: str, x: int, y: int) -> bool:
        down_msg, up_msg, wparam_mk = MOUSE_ACTIONS[target]
        lparam = winapi.make_lparam(x, y)
        ok = user32.PostMessageW(hwnd, down_msg, wparam_mk, lparam)
        time.sleep(PRESS_DURATION)
        ok2 = user32.PostMessageW(hwnd, up_msg, 0, lparam)
        return bool(ok and ok2)

    @staticmethod
    def _send_key(hwnd: int, key: KeySpec, mode: str) -> bool:
        lparam_down, lparam_up = winapi.key_lparams(key.vk)
        if mode == "char" and key.char:
            return bool(user32.PostMessageW(hwnd, winapi.WM_CHAR, ord(key.char), lparam_down))
        ok = user32.PostMessageW(hwnd, winapi.WM_KEYDOWN, key.vk, lparam_down)
        time.sleep(PRESS_DURATION)
        ok2 = user32.PostMessageW(hwnd, winapi.WM_KEYUP, key.vk, lparam_up)
        return bool(ok and ok2)

    def _report_progress(self, performed: int, iconic: bool) -> None:
        if not self._running:
            return
        if iconic:
            self._set_status(
                f"Выполнено действий: {performed}  ·  окно свёрнуто, клики могут не доходить",
                WARN_COLOR,
            )
        else:
            self._set_status(f"Выполнено действий: {performed}")

    def _on_worker_finished(self, performed: int, reason: str) -> None:
        self._running = False
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.update_btn.configure(state=tk.NORMAL)
        self._set_status(f"Остановлен. Выполнено действий: {performed}", HINT_COLOR)
        self._update_window_state()
        if reason:
            messagebox.showwarning("Окно", reason)

    # ------------------------------------------------------------- update
    def check_updates(self) -> None:
        if self._running or self._updating:
            return
        self._updating = True
        self.update_btn.configure(state=tk.DISABLED)
        self.start_btn.configure(state=tk.DISABLED)
        self._set_status("Проверка обновлений…", HINT_COLOR)
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self) -> None:
        try:
            result = updater.check_and_update(__version__)
        except updater.UpdateError as exc:
            self.root.after(0, self._on_update_failed, str(exc))
        except Exception as exc:  # noqa: BLE001 — показываем любую ошибку пользователю
            self.root.after(0, self._on_update_failed, f"{type(exc).__name__}: {exc}")
        else:
            self.root.after(0, self._on_update_done, result)

    def _finish_update_state(self) -> None:
        self._updating = False
        self.update_btn.configure(state=tk.NORMAL)
        self.start_btn.configure(state=tk.NORMAL)

    def _on_update_failed(self, message: str) -> None:
        self._finish_update_state()
        self._set_status("Обновление не выполнено", ERR_COLOR)
        messagebox.showerror("Обновление", message)

    def _on_update_done(self, result: updater.UpdateResult) -> None:
        self._finish_update_state()
        if not result.updated:
            self._set_status("Обновлений нет")
            messagebox.showinfo("Обновление", result.message)
            return

        self._set_status("Обновление установлено")
        if result.restart_cmd and messagebox.askyesno(
            "Обновление", f"{result.message}\n\nПерезапустить программу сейчас?"
        ):
            self._restart(result.restart_cmd)
        else:
            messagebox.showinfo("Обновление", "Изменения вступят в силу после перезапуска программы.")

    def _restart(self, cmd: list[str]) -> None:
        self.stop()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.Popen(cmd, cwd=str(updater.app_dir()), creationflags=creationflags)
        except OSError as exc:
            messagebox.showerror("Обновление", f"Не удалось перезапустить: {exc}")
            return
        self.root.destroy()
        sys.exit(0)

    # ---------------------------------------------------------------- run
    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        self.root.destroy()


def main() -> None:
    app = AutoClickerApp()
    app.run()


if __name__ == "__main__":
    main()
