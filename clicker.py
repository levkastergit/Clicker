"""
Автокликер для Windows: выбор окна, кнопки мыши/клавиши, интервал, старт/стоп.
"""

from __future__ import annotations

import ctypes
import random
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox, ttk

import updater

__version__ = "1.1.1"

user32 = ctypes.windll.user32

# Явные сигнатуры WinAPI: без них ctypes передаёт HWND/LPARAM как 32-битный int,
# что на 64-битной Windows может обрезать значения.
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostMessageW.restype = wintypes.BOOL
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

MOUSE_ACTIONS = {
    "Левая кнопка мыши": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON),
    "Правая кнопка мыши": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
    "Средняя кнопка мыши": (WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON),
}

VK_MAP: dict[str, int] = {
    "Пробел": 0x20,
    "Enter": 0x0D,
    "Escape": 0x1B,
    "Tab": 0x09,
    "Backspace": 0x08,
}
VK_MAP.update({f"F{i}": 0x6F + i for i in range(1, 13)})
VK_MAP.update({ch: ord(ch) for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})
KEY_CHOICES = list(VK_MAP.keys())

# Пауза между нажатием и отпусканием кнопки/клавиши, сек.
PRESS_DURATION = 0.02


def enum_windows() -> list[tuple[int, str]]:
    """Список видимых окон с непустым заголовком, отсортированный по названию."""
    result: list[tuple[int, str]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            result.append((hwnd, title))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    result.sort(key=lambda item: item[1].lower())
    return result


def make_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


class AutoClickerApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"Автокликер {__version__}")
        self.root.resizable(False, False)
        self.root.minsize(520, 500)

        self._windows: list[tuple[int, str]] = []
        self._running = False
        self._updating = False
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._build_ui()
        self.refresh_windows()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        hint_color = "#555"
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Окно:").grid(row=0, column=0, sticky=tk.W, **pad)
        win_row = ttk.Frame(frame)
        win_row.grid(row=0, column=1, columnspan=2, sticky=tk.EW, **pad)
        self.window_var = tk.StringVar()
        self.window_combo = ttk.Combobox(
            win_row, textvariable=self.window_var, state="readonly", width=42
        )
        self.window_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(win_row, text="Обновить список", command=self.refresh_windows).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        ttk.Label(frame, text="Тип действия:").grid(row=1, column=0, sticky=tk.W, **pad)
        self.action_type_var = tk.StringVar(value="mouse")
        type_frame = ttk.Frame(frame)
        type_frame.grid(row=1, column=1, sticky=tk.W, **pad)
        ttk.Radiobutton(
            type_frame,
            text="Мышь",
            value="mouse",
            variable=self.action_type_var,
            command=self._on_action_type_change,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            type_frame,
            text="Клавиша",
            value="key",
            variable=self.action_type_var,
            command=self._on_action_type_change,
        ).pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(frame, text="Кнопка / клавиша:").grid(row=2, column=0, sticky=tk.W, **pad)
        self.target_var = tk.StringVar(value="Левая кнопка мыши")
        self.target_combo = ttk.Combobox(
            frame,
            textvariable=self.target_var,
            values=list(MOUSE_ACTIONS.keys()),
            state="readonly",
            width=28,
        )
        self.target_combo.grid(row=2, column=1, sticky=tk.W, **pad)

        ttk.Label(frame, text="Интервал (сек):").grid(row=3, column=0, sticky=tk.W, **pad)
        interval_frame = ttk.Frame(frame)
        interval_frame.grid(row=3, column=1, sticky=tk.W, **pad)
        self.interval_min_var = tk.StringVar(value="0.4")
        self.interval_max_var = tk.StringVar(value="0.6")
        ttk.Label(interval_frame, text="от").pack(side=tk.LEFT)
        ttk.Entry(interval_frame, textvariable=self.interval_min_var, width=6).pack(
            side=tk.LEFT, padx=(4, 8)
        )
        ttk.Label(interval_frame, text="до").pack(side=tk.LEFT)
        ttk.Entry(interval_frame, textvariable=self.interval_max_var, width=6).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Label(
            interval_frame,
            text="  (случайная пауза в этом диапазоне)",
            foreground=hint_color,
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frame, text="Позиция клика (X, Y):").grid(
            row=4, column=0, sticky=tk.W, **pad
        )
        pos_frame = ttk.Frame(frame)
        pos_frame.grid(row=4, column=1, sticky=tk.W, **pad)
        self.click_x_var = tk.StringVar(value="50")
        self.click_y_var = tk.StringVar(value="50")
        ttk.Entry(pos_frame, textvariable=self.click_x_var, width=6).pack(side=tk.LEFT)
        ttk.Label(pos_frame, text=",").pack(side=tk.LEFT, padx=4)
        ttk.Entry(pos_frame, textvariable=self.click_y_var, width=6).pack(side=tk.LEFT)
        ttk.Label(
            pos_frame,
            text="  (относительно клиентской области окна)",
            foreground=hint_color,
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frame, text="Разброс позиции (±):").grid(
            row=5, column=0, sticky=tk.W, **pad
        )
        spread_frame = ttk.Frame(frame)
        spread_frame.grid(row=5, column=1, sticky=tk.W, **pad)
        self.spread_x_var = tk.StringVar(value="3")
        self.spread_y_var = tk.StringVar(value="3")
        ttk.Label(spread_frame, text="X").pack(side=tk.LEFT)
        ttk.Entry(spread_frame, textvariable=self.spread_x_var, width=5).pack(
            side=tk.LEFT, padx=(4, 12)
        )
        ttk.Label(spread_frame, text="Y").pack(side=tk.LEFT)
        ttk.Entry(spread_frame, textvariable=self.spread_y_var, width=5).pack(
            side=tk.LEFT, padx=(4, 0)
        )
        ttk.Label(
            spread_frame,
            text="  пикс. (0 = без разброса)",
            foreground=hint_color,
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frame, text="Количество (0 = бесконечно):").grid(
            row=6, column=0, sticky=tk.W, **pad
        )
        self.count_var = tk.StringVar(value="0")
        ttk.Entry(frame, textvariable=self.count_var, width=10).grid(
            row=6, column=1, sticky=tk.W, **pad
        )

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=7, column=0, columnspan=3, pady=16)
        self.start_btn = ttk.Button(btn_frame, text="Старт", command=self.start)
        self.start_btn.pack(side=tk.LEFT, padx=6)
        self.stop_btn = ttk.Button(
            btn_frame, text="Стоп", command=self.stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        self.update_btn = ttk.Button(
            btn_frame, text="Обновить программу", command=self.check_updates
        )
        self.update_btn.pack(side=tk.LEFT, padx=(18, 6))

        self.status_var = tk.StringVar(value="Готов")
        ttk.Label(frame, textvariable=self.status_var, foreground="#006400").grid(
            row=8, column=0, columnspan=3, sticky=tk.W, padx=10, pady=(0, 8)
        )

        hint = (
            "Подсказка: выберите окно, кнопку или клавишу. Интервал и позиция клика "
            "случайны в указанных диапазонах. Для точного клика поставьте разброс 0. "
            "Нажмите «Стоп» или закройте окно, чтобы остановить кликер. "
            "«Обновить программу» скачивает новую версию с GitHub."
        )
        ttk.Label(frame, text=hint, wraplength=480, foreground="#444").grid(
            row=9, column=0, columnspan=3, sticky=tk.W, padx=10
        )

        frame.columnconfigure(1, weight=1)

    def _on_action_type_change(self) -> None:
        if self.action_type_var.get() == "mouse":
            self.target_combo.configure(values=list(MOUSE_ACTIONS.keys()))
            self.target_var.set("Левая кнопка мыши")
        else:
            self.target_combo.configure(values=KEY_CHOICES)
            self.target_var.set("Пробел")

    def refresh_windows(self) -> None:
        self._windows = enum_windows()
        titles = [title for _, title in self._windows]
        self.window_combo.configure(values=titles)
        if titles:
            self.window_combo.current(0)
            self.status_var.set(f"Найдено окон: {len(titles)}")
        else:
            self.window_var.set("")
            self.status_var.set("Окна не найдены")

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
            messagebox.showwarning(
                "Окно", "Выбранное окно больше не существует. Обновите список."
            )
            self.refresh_windows()
            return

        interval_min = self._parse_float(self.interval_min_var.get(), "Минимальный интервал")
        interval_max = self._parse_float(self.interval_max_var.get(), "Максимальный интервал")
        if interval_min is None or interval_max is None:
            return
        if interval_min > interval_max:
            messagebox.showerror(
                "Ошибка", "Минимальный интервал не может быть больше максимального."
            )
            return

        max_count = self._parse_int(self.count_var.get(), "Количество")
        if max_count is None:
            return

        action_type = self.action_type_var.get()
        target = self.target_var.get()

        click_x = click_y = 0
        spread_x = spread_y = 0
        if action_type == "mouse":
            if target not in MOUSE_ACTIONS:
                messagebox.showwarning("Действие", "Выберите кнопку мыши.")
                return
            click_x = self._parse_int(self.click_x_var.get(), "X")
            click_y = self._parse_int(self.click_y_var.get(), "Y")
            spread_x = self._parse_int(self.spread_x_var.get(), "Разброс X")
            spread_y = self._parse_int(self.spread_y_var.get(), "Разброс Y")
            if None in (click_x, click_y, spread_x, spread_y):
                return
        elif target not in VK_MAP:
            messagebox.showwarning("Действие", "Выберите клавишу.")
            return

        self._stop_event.clear()
        self._running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.update_btn.configure(state=tk.DISABLED)
        self.status_var.set("Работает...")

        self._worker = threading.Thread(
            target=self._click_loop,
            args=(
                hwnd,
                interval_min,
                interval_max,
                max_count,
                action_type,
                target,
                click_x,
                click_y,
                spread_x,
                spread_y,
            ),
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        self.status_var.set("Остановка...")

    @staticmethod
    def _random_click_position(
        x: int, y: int, spread_x: int, spread_y: int
    ) -> tuple[int, int]:
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
        target: str,
        click_x: int,
        click_y: int,
        spread_x: int,
        spread_y: int,
    ) -> None:
        performed = 0
        window_closed = False
        try:
            while not self._stop_event.is_set():
                if max_count > 0 and performed >= max_count:
                    break
                if not user32.IsWindow(hwnd):
                    window_closed = True
                    break

                actual_x, actual_y = click_x, click_y
                if action_type == "mouse":
                    actual_x, actual_y = self._random_click_position(
                        click_x, click_y, spread_x, spread_y
                    )

                self._perform_action(hwnd, action_type, target, actual_x, actual_y)
                performed += 1
                self.root.after(
                    0, lambda n=performed: self.status_var.set(f"Выполнено действий: {n}")
                )

                pause = random.uniform(interval_min, interval_max)
                if self._stop_event.wait(pause):
                    break
        finally:
            self.root.after(0, self._on_worker_finished, performed, window_closed)

    @staticmethod
    def _perform_action(
        hwnd: int, action_type: str, target: str, click_x: int, click_y: int
    ) -> None:
        if action_type == "mouse":
            down_msg, up_msg, wparam_mk = MOUSE_ACTIONS[target]
            lparam = make_lparam(click_x, click_y)
            user32.PostMessageW(hwnd, down_msg, wparam_mk, lparam)
            time.sleep(PRESS_DURATION)
            user32.PostMessageW(hwnd, up_msg, 0, lparam)
        else:
            vk = VK_MAP[target]
            scan = user32.MapVirtualKeyW(vk, 0)
            lparam_down = 1 | (scan << 16)
            lparam_up = lparam_down | (1 << 30) | (1 << 31)
            user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam_down)
            time.sleep(PRESS_DURATION)
            user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam_up)

    def _on_worker_finished(self, performed: int, window_closed: bool) -> None:
        self._running = False
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.update_btn.configure(state=tk.NORMAL)
        self.status_var.set(f"Остановлен. Выполнено действий: {performed}")
        if window_closed:
            messagebox.showwarning("Окно", "Окно закрыто — кликер остановлен.")

    # ------------------------------------------------------------- update
    def check_updates(self) -> None:
        if self._running or self._updating:
            return
        self._updating = True
        self.update_btn.configure(state=tk.DISABLED)
        self.start_btn.configure(state=tk.DISABLED)
        self.status_var.set("Проверка обновлений...")
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
        self.status_var.set("Обновление не выполнено")
        messagebox.showerror("Обновление", message)

    def _on_update_done(self, result: updater.UpdateResult) -> None:
        self._finish_update_state()
        if not result.updated:
            self.status_var.set("Обновлений нет")
            messagebox.showinfo("Обновление", result.message)
            return

        self.status_var.set("Обновление установлено")
        if result.restart_cmd and messagebox.askyesno(
            "Обновление", f"{result.message}\n\nПерезапустить программу сейчас?"
        ):
            self._restart(result.restart_cmd)
        else:
            messagebox.showinfo(
                "Обновление", "Изменения вступят в силу после перезапуска программы."
            )

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
