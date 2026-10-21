"""
Значок в системном трее (pystray) с индикацией состояния.

Колбэки меню приходят из потока pystray — интерфейс переносит их в главный поток.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_RECORDING = "recording"

_COLORS = {
    STATE_IDLE: ("#64748b", "#e2e8f0"),
    STATE_RUNNING: ("#22c55e", "#f0fdf4"),
    STATE_RECORDING: ("#ef4444", "#fef2f2"),
}
_TITLES = {
    STATE_IDLE: "Автокликер — остановлен",
    STATE_RUNNING: "Автокликер — работает",
    STATE_RECORDING: "Автокликер — запись",
}


def make_icon_image(state: str, size: int = 64) -> Image.Image:
    fill, dot = _COLORS.get(state, _COLORS[STATE_IDLE])
    scale = 4
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = big // 10
    d.ellipse((pad, pad, big - pad, big - pad), fill=fill)
    # «Курсор»-точка в центре
    r = big // 7
    cx = cy = big // 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=dot)
    return img.resize((size, size), Image.LANCZOS)


class TrayIcon:
    def __init__(
        self,
        on_show: Callable[[], None],
        on_toggle: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        self._on_show = on_show
        self._on_toggle = on_toggle
        self._on_exit = on_exit
        self.state = STATE_IDLE
        self._lock = threading.Lock()
        self._icon: pystray.Icon | None = None

    def start(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Показать окно", lambda: self._on_show(), default=True),
            pystray.MenuItem(
                lambda item: "Стоп" if self.state == STATE_RUNNING else "Старт",
                lambda: self._on_toggle(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda: self._on_exit()),
        )
        self._icon = pystray.Icon(
            "autoclicker", make_icon_image(self.state), _TITLES[self.state], menu
        )
        self._icon.run_detached()

    def set_state(self, state: str, detail: str = "") -> None:
        with self._lock:
            self.state = state
            if self._icon is None:
                return
            try:
                self._icon.icon = make_icon_image(state)
                title = _TITLES.get(state, _TITLES[STATE_IDLE])
                if detail:
                    title = f"{title}: {detail}"
                self._icon.title = title[:127]
                self._icon.update_menu()
            except Exception:  # noqa: BLE001 — трей не должен ронять программу
                pass

    def notify(self, text: str) -> None:
        if self._icon is not None:
            try:
                self._icon.notify(text, "Автокликер")
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self._icon = None
