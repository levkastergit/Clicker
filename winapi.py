"""
Обёртки WinAPI (user32) с явными сигнатурами и таблицы клавиш.

Без явных argtypes ctypes передаёт HWND/LPARAM как 32-битный int,
что на 64-битной Windows может обрезать значения.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.windll.user32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
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

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

SW_RESTORE = 9

MOUSE_ACTIONS = {
    "Левая кнопка мыши": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON),
    "Правая кнопка мыши": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
    "Средняя кнопка мыши": (WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON),
}


def enable_dpi_awareness() -> None:
    """Чёткий интерфейс на экранах с масштабированием (не критично при ошибке)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


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


def last_error() -> int:
    return ctypes.get_last_error() or ctypes.GetLastError()


# --------------------------------------------------------------------------
# Клавиши
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class KeySpec:
    """Описание клавиши: имя для списка, виртуальный код и символ (если есть)."""

    name: str
    vk: int
    char: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name}  (VK 0x{self.vk:02X})"


_SPECIAL = {
    "Пробел": (0x20, " "),
    "Enter": (0x0D, "\r"),
    "Escape": (0x1B, None),
    "Tab": (0x09, "\t"),
    "Backspace": (0x08, None),
    "Shift": (0x10, None),
    "Ctrl": (0x11, None),
    "Alt": (0x12, None),
    "Insert": (0x2D, None),
    "Delete": (0x2E, None),
    "Home": (0x24, None),
    "End": (0x23, None),
    "Page Up": (0x21, None),
    "Page Down": (0x22, None),
    "Стрелка влево": (0x25, None),
    "Стрелка вверх": (0x26, None),
    "Стрелка вправо": (0x27, None),
    "Стрелка вниз": (0x28, None),
}

# Стандартная раскладка ЙЦУКЕН: кириллическая буква -> латинская клавиша той же позиции.
# Для OEM-клавиш указан виртуальный код напрямую.
_CYRILLIC_TO_VK = {
    "Й": ord("Q"), "Ц": ord("W"), "У": ord("E"), "К": ord("R"), "Е": ord("T"),
    "Н": ord("Y"), "Г": ord("U"), "Ш": ord("I"), "Щ": ord("O"), "З": ord("P"),
    "Х": 0xDB, "Ъ": 0xDD,
    "Ф": ord("A"), "Ы": ord("S"), "В": ord("D"), "А": ord("F"), "П": ord("G"),
    "Р": ord("H"), "О": ord("J"), "Л": ord("K"), "Д": ord("L"), "Ж": 0xBA, "Э": 0xDE,
    "Я": ord("Z"), "Ч": ord("X"), "С": ord("C"), "М": ord("V"), "И": ord("B"),
    "Т": ord("N"), "Ь": ord("M"), "Б": 0xBC, "Ю": 0xBE, "Ё": 0xC0,
}


def _build_key_groups() -> dict[str, list[KeySpec]]:
    groups: dict[str, list[KeySpec]] = {}
    groups["Специальные"] = [KeySpec(n, vk, ch) for n, (vk, ch) in _SPECIAL.items()]
    groups["F1–F12"] = [KeySpec(f"F{i}", 0x6F + i) for i in range(1, 13)]
    groups["Цифры"] = [KeySpec(d, ord(d), d) for d in "0123456789"]
    groups["Латиница"] = [KeySpec(c, ord(c), c.lower()) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    groups["Кириллица (ЙЦУКЕН)"] = [
        KeySpec(c, vk, c.lower()) for c, vk in _CYRILLIC_TO_VK.items()
    ]
    return groups


KEY_GROUPS = _build_key_groups()
KEY_GROUP_NAMES = list(KEY_GROUPS.keys())


def key_by_label(label: str) -> KeySpec | None:
    for keys in KEY_GROUPS.values():
        for key in keys:
            if key.label == label:
                return key
    return None


def key_lparams(vk: int) -> tuple[int, int]:
    """lParam для WM_KEYDOWN и WM_KEYUP (счётчик повторов 1, скан-код из VK)."""
    scan = user32.MapVirtualKeyW(vk, 0)
    down = 1 | (scan << 16)
    up = down | (1 << 30) | (1 << 31)
    return down, up
