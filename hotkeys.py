"""
Глобальные горячие клавиши через RegisterHotKey.

RegisterHotKey привязан к потоку с циклом сообщений, поэтому менеджер держит
отдельный поток. При изменении набора клавиш поток перезапускается.
Обратные вызовы приходят из этого потока — интерфейс должен переносить их
в главный поток (root.after).
"""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from ctypes import wintypes

import winapi
from winapi import Hotkey, kernel32, user32


class HotkeyManager:
    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._bindings: dict[str, Hotkey] = {}
        self.failed: dict[str, Hotkey] = {}

    # ------------------------------------------------------------------
    def set_bindings(self, bindings: dict[str, Hotkey | None]) -> dict[str, Hotkey]:
        """Перерегистрирует клавиши. Возвращает те, что не удалось занять."""
        self.stop()
        self._bindings = {name: hk for name, hk in bindings.items() if hk is not None}
        self.failed = {}
        if self._bindings:
            self._start()
        return dict(self.failed)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, winapi.WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = 0

    # ------------------------------------------------------------------
    def _start(self) -> None:
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        ids: dict[int, str] = {}
        msg = wintypes.MSG()
        # Создаём очередь сообщений потока до того, как кто-то пошлёт WM_QUIT.
        user32.PeekMessageW(ctypes.byref(msg), None, winapi.WM_USER, winapi.WM_USER, 0)

        for index, (name, hk) in enumerate(self._bindings.items(), start=1):
            if user32.RegisterHotKey(None, index, hk.modifiers | winapi.MOD_NOREPEAT, hk.vk):
                ids[index] = name
            else:
                self.failed[name] = hk
        self._ready.set()

        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == winapi.WM_HOTKEY:
                    name = ids.get(int(msg.wParam))
                    if name:
                        try:
                            self._callback(name)
                        except Exception:  # noqa: BLE001 — поток не должен умирать
                            pass
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for index in ids:
                user32.UnregisterHotKey(None, index)


user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
]
user32.PeekMessageW.restype = wintypes.BOOL


# Модификаторы Tk (event.state) -> MOD_* для RegisterHotKey (Windows).
_TK_SHIFT = 0x0001
_TK_CONTROL = 0x0004
_TK_ALT = 0x20000
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}


def hotkey_from_tk_event(keycode: int, state: int) -> Hotkey | None:
    """Строит Hotkey из события Tk. Для одиночного модификатора возвращает None."""
    vk = int(keycode)
    if vk in _MODIFIER_VKS:
        return None
    mods = 0
    if state & _TK_CONTROL:
        mods |= winapi.MOD_CONTROL
    if state & _TK_SHIFT:
        mods |= winapi.MOD_SHIFT
    if state & _TK_ALT:
        mods |= winapi.MOD_ALT
    return Hotkey(mods, vk)
