"""
Запись действий пользователя (клики, зажимы, клавиши) в последовательность шагов.

Клавиатура — низкоуровневый хук WH_KEYBOARD_LL в отдельном потоке с циклом сообщений.
Мышь — опрос состояния кнопок GetAsyncKeyState каждые несколько миллисекунд: хук мыши
получает сотни событий движения в секунду, Windows молча снимает его при задержках
обработчика, а клики от драйверов и утилит (Logitech, Razer, X-Mouse, удалённый
рабочий стол) приходят с флагом INJECTED. Опросу всё это безразлично.
Во время записи кликер ничего не отправляет сам, поэтому фильтр INJECTED не нужен.

Нажатие и отпускание записываются парами: короткое нажатие становится кликом или
нажатием клавиши, долгое (дольше HOLD_THRESHOLD) — зажимом на измеренное время.
Автоповтор зажатой клавиши (повторные KEYDOWN) не создаёт лишних шагов.

Ограничения: модификаторы (Shift/Ctrl/Alt/Win) не записываются как отдельные шаги,
сочетания вроде Ctrl+C в модель шагов не входят.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

import winapi
from actions import KIND_KEY, KIND_MOUSE, Position, Step
from winapi import KeySpec, kernel32, user32

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
HC_ACTION = 0
HOLD_THRESHOLD = 0.35  # сек: дольше — считается зажимом

_MOUSE_VKS = {0x01: "left", 0x02: "right", 0x04: "middle"}  # VK_LBUTTON, VK_RBUTTON, VK_MBUTTON
MOUSE_POLL_INTERVAL = 0.006
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


@dataclass
class _Press:
    """Одно нажатие: мышь (button, x, y) или клавиша (vk), с временем нажатия и отпускания."""

    kind: str          # "mouse" | "key"
    down: float
    up: float | None = None
    button: str = ""
    x: int = 0
    y: int = 0
    vk: int = 0

    @property
    def held(self) -> float:
        return (self.up if self.up is not None else time.monotonic()) - self.down


class Recorder:
    """
    Записывает события и превращает их в список Step.

    target_hwnd — окно, относительно которого считаются клики. Клики в другие окна
    пропускаются (их число — в `skipped`). ignore_vks — клавиши, которые не записывать
    (горячие клавиши самой программы).
    """

    def __init__(self, on_event: Callable[[str], None] | None = None) -> None:
        self._on_event = on_event
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._presses: list[_Press] = []
        self._open_mouse: dict[str, _Press] = {}
        self._open_keys: dict[int, _Press] = {}
        self._hooks: list[int] = []
        self._procs: list = []
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()
        self.target_hwnd = 0
        self.target_pid = 0
        self.ignore_vks: set[int] = set()
        self.active = False
        self.skipped = 0
        # Диагностика: сколько сырых событий увидели
        self.mouse_seen = 0
        self.key_seen = 0

    # ------------------------------------------------------------------
    def start(self, target_hwnd: int, ignore_vks: set[int]) -> bool:
        self.stop()
        self.target_hwnd = target_hwnd
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(target_hwnd, ctypes.byref(pid))
        self.target_pid = pid.value
        self.ignore_vks = set(ignore_vks) | _MODIFIER_VKS
        self._presses = []
        self._open_mouse = {}
        self._open_keys = {}
        self.skipped = 0
        self.mouse_seen = 0
        self.key_seen = 0
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_mouse, name="recorder-mouse", daemon=True)
        self._poll_thread.start()
        self.active = True
        return self.active

    def preview(self) -> list[Step]:
        """Шаги по уже записанным событиям (для живого отображения во время записи)."""
        with self._lock:
            presses = [_Press(**vars(p)) for p in self._presses]
        return self._build_steps(presses)

    def stop(self) -> list[Step]:
        self._poll_stop.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1.0)
        self._poll_thread = None
        if self._thread and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, winapi.WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = 0
        self.active = False
        with self._lock:
            now = time.monotonic()
            for p in self._presses:
                if p.up is None:
                    p.up = now
            presses = [_Press(**vars(p)) for p in self._presses]
        return self._build_steps(presses)

    # ------------------------------------------------------------------
    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, winapi.WM_USER, winapi.WM_USER, 0)

        kbd_proc = HOOKPROC(self._keyboard_hook)
        self._procs = [kbd_proc]  # защита от сборщика мусора
        hmod = kernel32.GetModuleHandleW(None)
        self._hooks = []
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kbd_proc, hmod, 0)
        if handle:
            self._hooks.append(handle)
        self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for handle in self._hooks:
                user32.UnhookWindowsHookEx(handle)
            self._hooks = []

    def _accepts_point(self, x: int, y: int) -> bool:
        """Клик относится к целевому окну: окно под курсором — цель, тот же процесс или точка внутри цели."""
        if not self.target_hwnd:
            return True
        hwnd = winapi.root_window_at(x, y)
        if hwnd == self.target_hwnd:
            return True
        if hwnd:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and pid.value == self.target_pid:
                return True
        if user32.IsWindow(self.target_hwnd) and not user32.IsIconic(self.target_hwnd):
            left, top, right, bottom = winapi.window_rect(self.target_hwnd)
            if left <= x < right and top <= y < bottom:
                return True
        return False

    def _poll_mouse(self) -> None:
        """Опрос кнопок мыши: переход «отпущена -> нажата» = клик в позиции курсора."""
        down: dict[str, bool] = {name: False for name in _MOUSE_VKS.values()}
        # Сбрасываем бит «была нажата с прошлого вызова», чтобы не записать клик по кнопке «Запись».
        for vk in _MOUSE_VKS:
            user32.GetAsyncKeyState(vk)
        while not self._poll_stop.is_set():
            for vk, button in _MOUSE_VKS.items():
                state = user32.GetAsyncKeyState(vk)
                pressed = bool(state & 0x8000)
                if pressed and not down[button]:
                    down[button] = True
                    self._on_mouse_down(button)
                elif not pressed and down[button]:
                    down[button] = False
                    self._on_mouse_up(button)
                elif not pressed and (state & 0x0001):
                    # Нажали и отпустили между двумя опросами — очень короткий клик.
                    self._on_mouse_down(button)
                    self._on_mouse_up(button)
            self._poll_stop.wait(MOUSE_POLL_INTERVAL)
        # На выходе отпускаем всё, что осталось «нажатым».
        for button, is_down in down.items():
            if is_down:
                self._on_mouse_up(button)

    def _on_mouse_down(self, button: str) -> None:
        self.mouse_seen += 1
        x, y = winapi.cursor_pos()
        now = time.monotonic()
        if self._accepts_point(x, y):
            press = _Press("mouse", now, button=button, x=x, y=y)
            with self._lock:
                self._presses.append(press)
                self._open_mouse[button] = press
            self._emit("mouse")
        else:
            self.skipped += 1
            self._emit("skipped")

    def _on_mouse_up(self, button: str) -> None:
        now = time.monotonic()
        with self._lock:
            press = self._open_mouse.pop(button, None)
            if press is not None:
                press.up = now
        if press is not None:
            self._emit("mouse_up")

    def _keyboard_hook(self, code: int, wparam: int, lparam: int) -> int:
        if code == HC_ACTION:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(info.vkCode)
            if vk not in self.ignore_vks and vk not in _MOUSE_VKS:
                self.key_seen += 1
                now = time.monotonic()
                if wparam in (winapi.WM_KEYDOWN, winapi.WM_SYSKEYDOWN):
                    with self._lock:
                        if vk in self._open_keys:
                            press = None  # автоповтор зажатой клавиши
                        else:
                            press = _Press("key", now, vk=vk)
                            self._presses.append(press)
                            self._open_keys[vk] = press
                    if press is not None:
                        self._emit("key")
                elif wparam in (winapi.WM_KEYUP, winapi.WM_SYSKEYUP):
                    with self._lock:
                        press = self._open_keys.pop(vk, None)
                        if press is not None:
                            press.up = now
                    if press is not None:
                        self._emit("key_up")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _emit(self, kind: str) -> None:
        if self._on_event:
            try:
                self._on_event(kind)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def _build_steps(self, presses: list[_Press]) -> list[Step]:
        steps: list[Step] = []
        ends: list[float] = []  # момент окончания каждого шага (для паузы до следующего)
        dbl_time = user32.GetDoubleClickTime() / 1000.0
        hwnd = self.target_hwnd
        for p in presses:
            hold = p.up is not None and p.held >= HOLD_THRESHOLD
            end = p.up if (hold and p.up is not None) else p.down
            if p.kind == "mouse":
                ref = hwnd if (hwnd and user32.IsWindow(hwnd)) else winapi.root_window_at(p.x, p.y)
                cx, cy = winapi.screen_to_client(ref, p.x, p.y)
                cw, ch = winapi.client_size(ref)
                prev = steps[-1] if steps else None
                # Два коротких клика той же кнопкой рядом и быстро — двойной/тройной клик.
                if (
                    not hold and prev is not None and prev.kind == KIND_MOUSE and not prev.hold
                    and prev.button == p.button and prev.clicks < 3
                    and abs(prev.position.x - cx) <= 4 and abs(prev.position.y - cy) <= 4
                    and (p.down - ends[-1]) <= dbl_time
                ):
                    prev.clicks += 1
                    ends[-1] = end
                    continue
                steps.append(Step(
                    kind=KIND_MOUSE, button=p.button, clicks=1,
                    position=Position(cx, cy, cw, ch, relative=True),
                    hold=hold, hold_seconds=round(p.held, 2) if hold else 1.0,
                ))
            else:
                name = winapi.key_name_from_vk(p.vk)
                char = chr(p.vk).lower() if (0x30 <= p.vk <= 0x39 or 0x41 <= p.vk <= 0x5A) else None
                if p.vk == 0x20:
                    char = " "
                steps.append(Step(
                    kind=KIND_KEY, key=KeySpec(name, p.vk, char), key_mode="key",
                    hold=hold, hold_seconds=round(p.held, 2) if hold else 1.0,
                ))
            ends.append(end)

        # Пауза после шага — до начала следующего нажатия.
        for i, step in enumerate(steps):
            if i + 1 < len(steps):
                nxt = presses_start(presses, i + 1, steps)
                delay = max(0.05, round(nxt - ends[i], 2)) if nxt is not None else 0.5
            else:
                delay = 0.5
            step.delay_min = step.delay_max = delay
        return steps


def presses_start(presses: list[_Press], step_index: int, steps: list[Step]) -> float | None:
    """Время нажатия, с которого начинается шаг step_index (с учётом слитых двойных кликов)."""
    # Шаги и нажатия соответствуют 1:1, кроме слитых кликов; восстанавливаем соответствие.
    count = 0
    idx = 0
    while idx < len(presses) and count < step_index:
        p = presses[idx]
        clicks = steps[count].clicks if steps[count].kind == KIND_MOUSE and not steps[count].hold else 1
        idx += clicks if p.kind == "mouse" else 1
        count += 1
    return presses[idx].down if idx < len(presses) else None
