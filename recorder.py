"""
Запись действий пользователя (клики и клавиши) в последовательность шагов.

Использует низкоуровневые хуки WH_MOUSE_LL / WH_KEYBOARD_LL в отдельном потоке
с циклом сообщений. Программно сгенерированный ввод (флаг INJECTED) не записывается.

Ограничения: модификаторы (Shift/Ctrl/Alt/Win) не записываются как отдельные шаги,
сочетания вроде Ctrl+C в модель шагов не входят.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

import winapi
from actions import KIND_KEY, KIND_MOUSE, Position, Step
from winapi import KeySpec, kernel32, user32

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
HC_ACTION = 0

_MOUSE_DOWN = {
    winapi.WM_LBUTTONDOWN: "left",
    winapi.WM_RBUTTONDOWN: "right",
    winapi.WM_MBUTTONDOWN: "middle",
}
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = wintypes.LPARAM
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class Recorder:
    """
    Записывает события и по остановке превращает их в список Step.

    target_hwnd — окно, относительно которого считаются клики. Клики в другие окна
    пропускаются. ignore_vks — клавиши, которые не записывать (например, горячие клавиши
    самой программы).
    """

    def __init__(self, on_event: Callable[[str], None] | None = None) -> None:
        self._on_event = on_event
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._events: list[tuple[float, str, dict]] = []
        self._hooks: list[int] = []
        self._procs: list = []
        self.target_hwnd = 0
        self.ignore_vks: set[int] = set()
        self.active = False

    # ------------------------------------------------------------------
    def start(self, target_hwnd: int, ignore_vks: set[int]) -> bool:
        self.stop()
        self.target_hwnd = target_hwnd
        self.ignore_vks = set(ignore_vks) | _MODIFIER_VKS
        self._events = []
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        self.active = bool(self._hooks)
        return self.active

    def preview(self) -> list[Step]:
        """Шаги по уже записанным событиям (для живого отображения во время записи)."""
        return self._build_steps(list(self._events))

    def stop(self) -> list[Step]:
        if self._thread and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, winapi.WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = 0
        self.active = False
        return self._build_steps(list(self._events))

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

    def _mouse_hook(self, code: int, wparam: int, lparam: int) -> int:
        if code == HC_ACTION and wparam in _MOUSE_DOWN:
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not info.flags & LLMHF_INJECTED:
                self._events.append((time.monotonic(), "mouse", {
                    "button": _MOUSE_DOWN[wparam], "x": info.pt.x, "y": info.pt.y,
                }))
                self._emit("mouse")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _keyboard_hook(self, code: int, wparam: int, lparam: int) -> int:
        if code == HC_ACTION and wparam in (winapi.WM_KEYDOWN, winapi.WM_SYSKEYDOWN):
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(info.vkCode)
            if not info.flags & LLKHF_INJECTED and vk not in self.ignore_vks:
                self._events.append((time.monotonic(), "key", {"vk": vk}))
                self._emit("key")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _emit(self, kind: str) -> None:
        if self._on_event:
            try:
                self._on_event(kind)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    def _build_steps(self, events: list[tuple[float, str, dict]]) -> list[Step]:
        steps: list[Step] = []
        dbl_time = user32.GetDoubleClickTime() / 1000.0
        for i, (ts, kind, data) in enumerate(events):
            next_ts = events[i + 1][0] if i + 1 < len(events) else ts + 0.5
            delay = max(0.05, round(next_ts - ts, 2))

            if kind == "mouse":
                hwnd = winapi.root_window_at(data["x"], data["y"])
                if self.target_hwnd and hwnd != self.target_hwnd:
                    continue
                cx, cy = winapi.screen_to_client(self.target_hwnd or hwnd, data["x"], data["y"])
                cw, ch = winapi.client_size(self.target_hwnd or hwnd)
                prev = steps[-1] if steps else None
                # Два клика той же кнопкой рядом и быстро — двойной клик.
                if (
                    prev is not None and prev.kind == KIND_MOUSE and prev.button == data["button"]
                    and prev.clicks < 3 and abs(prev.position.x - cx) <= 4 and abs(prev.position.y - cy) <= 4
                    and (ts - events[i - 1][0]) <= dbl_time
                ):
                    prev.clicks += 1
                    prev.delay_min = prev.delay_max = delay
                    continue
                steps.append(Step(
                    kind=KIND_MOUSE, button=data["button"], clicks=1,
                    position=Position(cx, cy, cw, ch, relative=True),
                    delay_min=delay, delay_max=delay,
                ))
            else:
                vk = data["vk"]
                name = winapi.key_name_from_vk(vk)
                char = chr(vk).lower() if (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A) else None
                if vk == 0x20:
                    char = " "
                steps.append(Step(
                    kind=KIND_KEY, key=KeySpec(name, vk, char), key_mode="key",
                    delay_min=delay, delay_max=delay,
                ))
        return steps
