"""
Обёртки WinAPI (user32) с явными сигнатурами и таблицы клавиш.

Без явных argtypes ctypes передаёт HWND/LPARAM как 32-битный int,
что на 64-битной Windows может обрезать значения.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
ULONG_PTR = ctypes.c_size_t

# --- сигнатуры -------------------------------------------------------------
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
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ScreenToClient.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = wintypes.LPARAM
user32.GetKeyNameTextW.argtypes = [wintypes.LONG, wintypes.LPWSTR, ctypes.c_int]
user32.GetKeyNameTextW.restype = ctypes.c_int
user32.GetDoubleClickTime.argtypes = []
user32.GetDoubleClickTime.restype = wintypes.UINT
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.RealChildWindowFromPoint.argtypes = [wintypes.HWND, wintypes.POINT]
user32.RealChildWindowFromPoint.restype = wintypes.HWND
user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.IsChild.restype = wintypes.BOOL


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL

SW_SHOWNOACTIVATE = 4
SW_SHOWMINNOACTIVE = 7
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
WM_MOUSEMOVE = 0x0200


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


class WindowKeeper:
    """
    Держит свёрнутое целевое окно «не свёрнутым» на время работы кликера.

    policy:
      "background" — развернуть без активации и положить под другие окна;
      "offscreen"  — развернуть без активации и вынести за край экрана;
      иначе         — ничего не делать.
    restore() возвращает окно в исходное состояние.
    """

    def __init__(self, hwnd: int, policy: str) -> None:
        self.hwnd = hwnd
        self.policy = policy
        self._was_iconic = False
        self._rect: tuple[int, int, int, int] | None = None
        self._moved = False

    @property
    def active(self) -> bool:
        return self.policy in ("background", "offscreen")

    def ensure_visible(self) -> bool:
        """Если окно свёрнуто — разворачивает его в фоне. Возвращает True, если что-то сделал."""
        if not self.active or not user32.IsWindow(self.hwnd) or not user32.IsIconic(self.hwnd):
            return False
        self._was_iconic = True
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        if self._rect is None:
            self._rect = window_rect(self.hwnd)
        self._apply_position()
        return True

    def _apply_position(self) -> None:
        if self.policy == "offscreen" and self._rect is not None:
            left, top, right, bottom = self._rect
            vx, vy, vw, vh = virtual_screen()
            # Окно целиком за левым краем виртуального экрана: для системы оно развёрнуто и видимо.
            user32.SetWindowPos(self.hwnd, HWND_BOTTOM, vx - (right - left) - 50, top, 0, 0,
                                SWP_NOSIZE | SWP_NOACTIVATE)
            self._moved = True
        else:
            user32.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def push_back(self, previous_foreground: int) -> None:
        """После кратковременной активации окна снова прячет его и возвращает прежнее активное окно."""
        if not self.active or not self._was_iconic or not user32.IsWindow(self.hwnd):
            return
        self._apply_position()
        if previous_foreground and previous_foreground != self.hwnd and user32.IsWindow(previous_foreground):
            user32.SetForegroundWindow(previous_foreground)

    def restore(self) -> None:
        if not user32.IsWindow(self.hwnd):
            return
        if self._moved and self._rect is not None:
            user32.SetWindowPos(self.hwnd, None, self._rect[0], self._rect[1], 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            self._moved = False
        if self._was_iconic and not user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, SW_SHOWMINNOACTIVE)
        self._was_iconic = False


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_process_name(hwnd: int) -> str:
    """Имя exe-файла процесса окна (например notepad.exe), пусто при отсутствии доступа."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)
    return ""

# --- константы -------------------------------------------------------------
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_USER = 0x0400

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

SW_RESTORE = 9
GA_ROOT = 2

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

VK_MENU = 0x12
VK_SHIFT = 0x10
VK_CONTROL = 0x11

# Клавиши, для которых SendInput требует флаг EXTENDEDKEY.
EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # PgUp PgDn End Home стрелки
    0x2D, 0x2E, 0x5B, 0x5C, 0x6F, 0x90, 0x2C,  # Insert Delete LWin RWin NumDivide NumLock PrintScreen
    0xA3, 0xA5,  # RControl RMenu
}

MOUSE_BUTTONS = {
    "left": ("Левая", WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": ("Правая", WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": ("Средняя", WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON, MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}
MOUSE_BUTTON_LABELS = {key: val[0] for key, val in MOUSE_BUTTONS.items()}


# --- структуры SendInput ---------------------------------------------------
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def send_inputs(inputs: list[INPUT]) -> bool:
    if not inputs:
        return True
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    return sent == len(inputs)


def mouse_input(dx: int = 0, dy: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    return inp


def key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return inp


# --- вспомогательные функции -----------------------------------------------
def enable_dpi_awareness() -> None:
    """Все координаты в физических пикселях, чёткий интерфейс при масштабировании."""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4 (Windows 10 1703+)
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
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
        title = window_title(hwnd)
        if title:
            result.append((hwnd, title))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    result.sort(key=lambda item: item[1].lower())
    return result


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def make_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def last_error() -> int:
    return ctypes.get_last_error()


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def root_window_at(x: int, y: int) -> int:
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return 0
    return user32.GetAncestor(hwnd, GA_ROOT) or hwnd


def client_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return 0, 0
    return rect.right - rect.left, rect.bottom - rect.top


def screen_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = wintypes.POINT(x, y)
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = wintypes.POINT(x, y)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def virtual_screen() -> tuple[int, int, int, int]:
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.SwitchToThisWindow.restype = None


def bring_to_foreground(hwnd: int) -> bool:
    """
    Активирует окно. Windows разрешает SetForegroundWindow только активному процессу,
    поэтому по очереди пробуем обходные приёмы: нажатие Alt, присоединение к очереди
    ввода активного потока (AttachThreadInput), SwitchToThisWindow.
    """
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    def done() -> bool:
        return user32.GetForegroundWindow() == hwnd

    if done():
        return True
    user32.SetForegroundWindow(hwnd)
    if done():
        return True
    # Нажатие Alt снимает ограничение на SetForegroundWindow для текущего процесса.
    send_inputs([key_input(VK_MENU, 0, 0), key_input(VK_MENU, 0, KEYEVENTF_KEYUP)])
    user32.SetForegroundWindow(hwnd)
    if done():
        return True
    # Присоединяемся к очереди ввода активного потока и активируем окно от его имени.
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    our_tid = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != our_tid and user32.AttachThreadInput(our_tid, fg_tid, True))
    try:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(our_tid, fg_tid, False)
    if done():
        return True
    user32.SwitchToThisWindow(hwnd, True)
    deadline = __import__("time").monotonic() + 0.3
    while not done() and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.02)
    return done()


def focused_child(hwnd: int, fallback: int = 0) -> int:
    """
    Окно с фокусом ввода в потоке целевого окна.

    У неактивного приложения фокус сброшен (hwndFocus = NULL), тогда берём
    `fallback` (например, дочернее окно последнего клика), иначе само окно.
    """
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    if tid:
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
            focus = info.hwndFocus
            if focus == hwnd or user32.IsChild(hwnd, focus):
                return focus
    if fallback and user32.IsWindow(fallback) and (fallback == hwnd or user32.IsChild(hwnd, fallback)):
        return fallback
    return hwnd


def child_at_client_point(hwnd: int, x: int, y: int) -> tuple[int, int, int]:
    """Самое вложенное дочернее окно под точкой и координаты точки в его клиентской области."""
    sx, sy = client_to_screen(hwnd, x, y)
    current = hwnd
    for _ in range(16):
        cx, cy = screen_to_client(current, sx, sy)
        child = user32.RealChildWindowFromPoint(current, wintypes.POINT(cx, cy))
        if not child or child == current:
            break
        current = child
    cx, cy = screen_to_client(current, sx, sy)
    return current, cx, cy


def key_name_from_vk(vk: int) -> str:
    scan = user32.MapVirtualKeyW(vk, 0)
    lparam = scan << 16
    if vk in EXTENDED_VKS:
        lparam |= 1 << 24
    buf = ctypes.create_unicode_buffer(64)
    if user32.GetKeyNameTextW(lparam, buf, 64) > 0 and buf.value:
        return buf.value
    return f"VK 0x{vk:02X}"


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

    def to_dict(self) -> dict:
        return {"name": self.name, "vk": self.vk, "char": self.char}

    @staticmethod
    def from_dict(data: dict) -> "KeySpec":
        return KeySpec(str(data.get("name", "?")), int(data.get("vk", 0)), data.get("char"))


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
    groups["Кириллица (ЙЦУКЕН)"] = [KeySpec(c, vk, c.lower()) for c, vk in _CYRILLIC_TO_VK.items()]
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
    if vk in EXTENDED_VKS:
        down |= 1 << 24
    up = down | (1 << 30) | (1 << 31)
    return down, up


# --------------------------------------------------------------------------
# Горячие клавиши
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Hotkey:
    modifiers: int  # MOD_* флаги
    vk: int

    @property
    def label(self) -> str:
        parts = []
        if self.modifiers & MOD_CONTROL:
            parts.append("Ctrl")
        if self.modifiers & MOD_ALT:
            parts.append("Alt")
        if self.modifiers & MOD_SHIFT:
            parts.append("Shift")
        if self.modifiers & MOD_WIN:
            parts.append("Win")
        parts.append(key_name_from_vk(self.vk))
        return "+".join(parts)

    def to_dict(self) -> dict:
        return {"modifiers": self.modifiers, "vk": self.vk}

    @staticmethod
    def from_dict(data: dict | None) -> "Hotkey | None":
        if not data:
            return None
        return Hotkey(int(data.get("modifiers", 0)), int(data.get("vk", 0)))
