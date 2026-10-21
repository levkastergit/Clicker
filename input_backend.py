"""
Отправка кликов и клавиш в окно двумя способами.

MODE_POST  — PostMessage прямо в окно: работает в фоне, окно может быть не активно,
             курсор не двигается. Программа-получатель видит «синтетические» сообщения,
             игры с защитой от автокликеров их часто игнорируют.
MODE_SEND  — SendInput: ввод проходит через системную очередь ввода, как с реальной
             мыши и клавиатуры (клавиши — скан-кодами, мышь — движением курсора).
             Окно должно быть активным, курсор реально перемещается.

Важно: SendInput — максимум, что доступно без драйвера. Низкоуровневые хуки всё же
могут отличить такой ввод по флагу LLKHF_INJECTED; обойти это без собственного
драйвера ввода невозможно.
"""

from __future__ import annotations

import time

import winapi
from winapi import KeySpec, user32

MODE_POST = "post"
MODE_SEND = "sendinput"
MODE_LABELS = {
    MODE_POST: "Фоновый (PostMessage в окно)",
    MODE_SEND: "Реальный ввод (SendInput, как с мыши/клавиатуры)",
}

PRESS_DURATION = 0.02   # пауза между нажатием и отпусканием, сек
MULTI_CLICK_GAP = 0.06  # пауза между кликами двойного/тройного клика, сек


# --------------------------------------------------------------------------
# Мышь
# --------------------------------------------------------------------------
def _abs_coords(sx: int, sy: int) -> tuple[int, int]:
    """Экранные координаты -> нормализованные 0..65535 по виртуальному экрану."""
    vx, vy, vw, vh = winapi.virtual_screen()
    vw = max(vw, 1)
    vh = max(vh, 1)
    nx = int((sx - vx) * 65535 / vw) + 1
    ny = int((sy - vy) * 65535 / vh) + 1
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def move_cursor(sx: int, sy: int) -> bool:
    nx, ny = _abs_coords(sx, sy)
    flags = winapi.MOUSEEVENTF_MOVE | winapi.MOUSEEVENTF_ABSOLUTE | winapi.MOUSEEVENTF_VIRTUALDESK
    return winapi.send_inputs([winapi.mouse_input(nx, ny, flags)])


def click(
    hwnd: int,
    button: str,
    x: int | None,
    y: int | None,
    clicks: int = 1,
    mode: str = MODE_POST,
) -> tuple[bool, int]:
    """
    Клик кнопкой `button` в клиентских координатах (x, y). None — текущая позиция курсора.
    Возвращает (успех, окно-получатель): в фоновом режиме это дочернее окно под точкой.
    """
    _, down_msg, up_msg, mk, down_flag, up_flag = winapi.MOUSE_BUTTONS[button]
    clicks = max(1, clicks)

    if mode == MODE_SEND:
        if x is not None and y is not None:
            sx, sy = winapi.client_to_screen(hwnd, x, y)
            if not move_cursor(sx, sy):
                return False, hwnd
            time.sleep(0.01)
        for i in range(clicks):
            if i:
                time.sleep(MULTI_CLICK_GAP)
            if not winapi.send_inputs([winapi.mouse_input(0, 0, down_flag)]):
                return False, hwnd
            time.sleep(PRESS_DURATION)
            if not winapi.send_inputs([winapi.mouse_input(0, 0, up_flag)]):
                return False, hwnd
        return True, hwnd

    # PostMessage: координаты обязательны; если их нет — берём позицию курсора.
    if x is None or y is None:
        cx, cy = winapi.cursor_pos()
        x, y = winapi.screen_to_client(hwnd, cx, cy)
    # Сообщение адресуем дочернему окну под точкой: у многих программ
    # верхнее окно само клики не обрабатывает.
    target, tx, ty = winapi.child_at_client_point(hwnd, x, y)
    lparam = winapi.make_lparam(tx, ty)
    ok = True
    for i in range(clicks):
        if i:
            time.sleep(MULTI_CLICK_GAP)
        # Второе нажатие двойного клика Windows доставляет как *DBLCLK (down_msg + 2).
        msg_down = down_msg + 2 if i == 1 else down_msg
        ok &= bool(user32.PostMessageW(target, msg_down, mk, lparam))
        time.sleep(PRESS_DURATION)
        ok &= bool(user32.PostMessageW(target, up_msg, 0, lparam))
    return ok, target


# --------------------------------------------------------------------------
# Клавиатура
# --------------------------------------------------------------------------
def _scan_inputs(vk: int) -> tuple[winapi.INPUT, winapi.INPUT]:
    scan = user32.MapVirtualKeyW(vk, 0)
    flags = winapi.KEYEVENTF_SCANCODE
    if vk in winapi.EXTENDED_VKS:
        flags |= winapi.KEYEVENTF_EXTENDEDKEY
    down = winapi.key_input(0, scan, flags)
    up = winapi.key_input(0, scan, flags | winapi.KEYEVENTF_KEYUP)
    return down, up


def _unicode_inputs(ch: str) -> list[winapi.INPUT]:
    inputs = []
    # Символы вне BMP передаются парой суррогатов (UTF-16).
    raw = ch.encode("utf-16-le")
    for code in [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]:
        inputs.append(winapi.key_input(0, code, winapi.KEYEVENTF_UNICODE))
        inputs.append(winapi.key_input(0, code, winapi.KEYEVENTF_UNICODE | winapi.KEYEVENTF_KEYUP))
    return inputs


def press_key(
    hwnd: int, key: KeySpec, key_mode: str = "key", mode: str = MODE_POST, fallback_child: int = 0
) -> bool:
    """
    Нажатие клавиши. key_mode: "key" — код клавиши, "char" — символ независимо от раскладки.
    fallback_child — куда слать в фоновом режиме, если у окна нет фокуса (окно последнего клика).
    """
    if mode == MODE_SEND:
        if key_mode == "char" and key.char:
            return winapi.send_inputs(_unicode_inputs(key.char))
        down, up = _scan_inputs(key.vk)
        if not winapi.send_inputs([down]):
            return False
        time.sleep(PRESS_DURATION)
        return winapi.send_inputs([up])

    # Клавиши адресуем окну с фокусом внутри целевого окна (поле ввода, холст игры).
    target = winapi.focused_child(hwnd, fallback_child)
    lparam_down, lparam_up = winapi.key_lparams(key.vk)
    if key_mode == "char" and key.char:
        return bool(user32.PostMessageW(target, winapi.WM_CHAR, ord(key.char[0]), lparam_down))
    ok = bool(user32.PostMessageW(target, winapi.WM_KEYDOWN, key.vk, lparam_down))
    time.sleep(PRESS_DURATION)
    ok &= bool(user32.PostMessageW(target, winapi.WM_KEYUP, key.vk, lparam_up))
    return ok


def type_text(
    hwnd: int, text: str, mode: str = MODE_POST, char_delay: float = 0.01, fallback_child: int = 0
) -> bool:
    """Ввод строки символами (не зависит от раскладки)."""
    ok = True
    target = winapi.focused_child(hwnd, fallback_child) if mode == MODE_POST else hwnd
    for ch in text:
        if mode == MODE_SEND:
            ok &= winapi.send_inputs(_unicode_inputs(ch))
        else:
            lparam, _ = winapi.key_lparams(0)
            ok &= bool(user32.PostMessageW(target, winapi.WM_CHAR, ord(ch), lparam))
        if not ok:
            return False
        time.sleep(char_delay)
    return ok


def prepare_window(hwnd: int, mode: str, activate: bool) -> None:
    """
    Активирует окно перед запуском. Для реального ввода это обязательно (ввод идёт
    в активное окно). Для фонового режима полезно: приложение восстанавливает фокус
    на своём поле ввода, и клавиши адресуются именно ему.
    """
    if not activate:
        return
    winapi.bring_to_foreground(hwnd)
    # Ждём, пока окно действительно станет активным, иначе первые нажатия уйдут не туда.
    deadline = time.monotonic() + 0.7
    while time.monotonic() < deadline and user32.GetForegroundWindow() != hwnd:
        time.sleep(0.02)
    # После активации приложение восстанавливает фокус на своём поле ввода не сразу.
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and winapi.focused_child(hwnd) == hwnd:
        time.sleep(0.02)
    time.sleep(0.2)
