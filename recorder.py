"""
Запись действий пользователя (клики, зажимы, клавиши) в последовательность шагов.

Использует низкоуровневые хуки WH_MOUSE_LL / WH_KEYBOARD_LL в отдельном потоке
с циклом сообщений. Программно сгенерированный ввод (флаг INJECTED) не записывается.

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

_MOUSE_DOWN = {winapi.WM_LBUTTONDOWN: "left", winapi.WM_RBUTTONDOWN: "right", winapi.WM_MBUTTONDOWN: "middle"}
_MOUSE_UP = {winapi.WM_LBUTTONUP: "left", winapi.WM_RBUTTONUP: "right", winapi.WM_MBUTTONUP: "middle"}
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


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
        self.target_hwnd = 0
        self.target_pid = 0
        self.ignore_vks: set[int] = set()
        self.active = False
        self.skipped = 0

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
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        self.active = bool(self._hooks)
        return self.active

    def preview(self) -> list[Step]:
        """Шаги по уже записанным событиям (для живого отображения во время записи)."""
        with self._lock:
            presses = [_Press(**vars(p)) for p in self._presses]
        return self._build_steps(presses)

    def stop(self) -> list[Step]:
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

        mouse_proc = HOOKPROC(self._mouse_hook)
        kbd_proc = HOOKPROC(self._keyboard_hook)
        self._procs = [mouse_proc, kbd_proc]  # защита от сборщика мусора
        hmod = kernel32.GetModuleHandleW(None)
        self._hooks = []
        for hook_id, proc in ((WH_MOUSE_LL, mouse_proc), (WH_KEYBOARD_LL, kbd_proc)):
            handle = user32.SetWindowsHookExW(hook_id, proc, hmod, 0)
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

    def _mouse_hook(self, code: int, wparam: int, lparam: int) -> int:
        if code == HC_ACTION and (wparam in _MOUSE_DOWN or wparam in _MOUSE_UP):
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not info.flags & LLMHF_INJECTED:
                now = time.monotonic()
                if wparam in _MOUSE_DOWN:
                    button = _MOUSE_DOWN[wparam]
                    if self._accepts_point(info.pt.x, info.pt.y):
                        press = _Press("mouse", now, button=button, x=info.pt.x, y=info.pt.y)
                        with self._lock:
                            self._presses.append(press)
                            self._open_mouse[button] = press
                        self._emit("mouse")
                    else:
                        self.skipped += 1
                        self._emit("skipped")
                else:
                    button = _MOUSE_UP[wparam]
                    with self._lock:
                        press = self._open_mouse.pop(button, None)
                        if press is not None:
                            press.up = now
                    if press is not None:
                        self._emit("mouse_up")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _keyboard_hook(self, code: int, wparam: int, lparam: int) -> int:
        if code == HC_ACTION:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(info.vkCode)
            if not info.flags & LLKHF_INJECTED and vk not in self.ignore_vks:
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
