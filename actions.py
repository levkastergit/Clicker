"""
Модель действий (шаги, последовательность) и поток их выполнения.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import input_backend
import winapi
from winapi import KeySpec, user32

MAX_POST_FAILURES = 5


# --------------------------------------------------------------------------
# Позиция с привязкой к размеру окна
# --------------------------------------------------------------------------
@dataclass
class Position:
    """
    Точка в клиентских координатах окна.

    При съёме запоминается размер клиентской области. Если `relative` включено,
    при выполнении точка пересчитывается пропорционально текущему размеру окна,
    поэтому изменение масштаба или размера окна не сбивает координаты.
    """

    x: int = 50
    y: int = 50
    client_w: int = 0
    client_h: int = 0
    relative: bool = True

    def resolve(self, hwnd: int) -> tuple[int, int]:
        if not self.relative or self.client_w <= 0 or self.client_h <= 0:
            return self.x, self.y
        cur_w, cur_h = winapi.client_size(hwnd)
        if cur_w <= 0 or cur_h <= 0 or (cur_w == self.client_w and cur_h == self.client_h):
            return self.x, self.y
        return round(self.x * cur_w / self.client_w), round(self.y * cur_h / self.client_h)

    @property
    def percent(self) -> tuple[float, float] | None:
        if self.client_w <= 0 or self.client_h <= 0:
            return None
        return 100 * self.x / self.client_w, 100 * self.y / self.client_h

    def describe(self) -> str:
        pct = self.percent
        if self.relative and pct:
            return f"({self.x}, {self.y}) ≈ {pct[0]:.1f}% × {pct[1]:.1f}% окна"
        return f"({self.x}, {self.y}) пикс."

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "client_w": self.client_w, "client_h": self.client_h,
                "relative": self.relative}

    @staticmethod
    def from_dict(d: dict | None) -> "Position":
        d = d or {}
        return Position(int(d.get("x", 50)), int(d.get("y", 50)), int(d.get("client_w", 0)),
                        int(d.get("client_h", 0)), bool(d.get("relative", True)))


# --------------------------------------------------------------------------
# Шаг
# --------------------------------------------------------------------------
KIND_MOUSE = "mouse"
KIND_KEY = "key"
KIND_TEXT = "text"
KIND_PAUSE = "pause"
KIND_LABELS = {
    KIND_MOUSE: "Клик мышью",
    KIND_KEY: "Клавиша",
    KIND_TEXT: "Ввод текста",
    KIND_PAUSE: "Пауза",
}
CLICK_LABELS = {1: "одинарный", 2: "двойной", 3: "тройной"}


@dataclass
class Step:
    kind: str = KIND_MOUSE
    # мышь
    button: str = "left"
    clicks: int = 1
    use_cursor: bool = False          # кликать там, где сейчас курсор
    position: Position = field(default_factory=Position)
    spread_x: int = 0
    spread_y: int = 0
    # клавиша
    key: KeySpec | None = None
    key_mode: str = "key"             # "key" | "char"
    # текст
    text: str = ""
    # пауза после шага, сек (случайная в диапазоне)
    delay_min: float = 0.4
    delay_max: float = 0.6

    def describe(self) -> str:
        if self.kind == KIND_MOUSE:
            where = "в позиции курсора" if self.use_cursor else self.position.describe()
            spread = f", разброс ±{self.spread_x}/{self.spread_y}" if (self.spread_x or self.spread_y) else ""
            return (f"{winapi.MOUSE_BUTTON_LABELS[self.button]} кнопка, "
                    f"{CLICK_LABELS.get(self.clicks, self.clicks)} клик {where}{spread}")
        if self.kind == KIND_KEY:
            if self.key is None:
                return "клавиша не выбрана"
            how = "символ" if self.key_mode == "char" else "нажатие"
            return f"{self.key.name} ({how})"
        if self.kind == KIND_TEXT:
            short = self.text if len(self.text) <= 30 else self.text[:27] + "…"
            return f"«{short}»"
        return "ожидание"

    def delay_text(self) -> str:
        if abs(self.delay_min - self.delay_max) < 1e-9:
            return f"{self.delay_min:g} с"
        return f"{self.delay_min:g}–{self.delay_max:g} с"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "button": self.button, "clicks": self.clicks,
            "use_cursor": self.use_cursor, "position": self.position.to_dict(),
            "spread_x": self.spread_x, "spread_y": self.spread_y,
            "key": self.key.to_dict() if self.key else None, "key_mode": self.key_mode,
            "text": self.text, "delay_min": self.delay_min, "delay_max": self.delay_max,
        }

    @staticmethod
    def from_dict(d: dict) -> "Step":
        return Step(
            kind=str(d.get("kind", KIND_MOUSE)),
            button=str(d.get("button", "left")),
            clicks=int(d.get("clicks", 1)),
            use_cursor=bool(d.get("use_cursor", False)),
            position=Position.from_dict(d.get("position")),
            spread_x=int(d.get("spread_x", 0)),
            spread_y=int(d.get("spread_y", 0)),
            key=KeySpec.from_dict(d["key"]) if d.get("key") else None,
            key_mode=str(d.get("key_mode", "key")),
            text=str(d.get("text", "")),
            delay_min=float(d.get("delay_min", 0.4)),
            delay_max=float(d.get("delay_max", 0.6)),
        )


# --------------------------------------------------------------------------
# Выполнение
# --------------------------------------------------------------------------
@dataclass
class RunConfig:
    hwnd: int
    steps: list[Step]
    repeat: int = 0                       # 0 = бесконечно
    input_mode: str = input_backend.MODE_POST
    activate_window: bool = True          # активировать окно перед запуском
    restore_cursor: bool = True           # вернуть курсор после SendInput-клика
    fallback_child: int = 0               # окно фокуса цели, замеченное ранее (для фонового режима)


class Runner(threading.Thread):
    """Выполняет шаги по кругу `repeat` раз. Сообщает о прогрессе через колбэки."""

    def __init__(
        self,
        config: RunConfig,
        on_progress: Callable[[int, int, int, bool], None],
        on_finished: Callable[[int, str], None],
    ) -> None:
        super().__init__(name="runner", daemon=True)
        self.config = config
        self._on_progress = on_progress
        self._on_finished = on_finished
        self._stop_event = threading.Event()
        # Окно-получатель клавиш в фоновом режиме, если у цели нет фокуса:
        # последнее кликнутое дочернее окно либо фокус, замеченный до запуска.
        self._last_child = config.fallback_child

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    # ------------------------------------------------------------------
    def run(self) -> None:
        cfg = self.config
        performed = 0
        iteration = 0
        failures = 0
        reason = ""
        origin = winapi.cursor_pos()
        try:
            if not cfg.steps:
                reason = "Нет ни одного действия."
                return
            input_backend.prepare_window(cfg.hwnd, cfg.input_mode, cfg.activate_window)
            while not self._stop_event.is_set():
                if cfg.repeat > 0 and iteration >= cfg.repeat:
                    break
                iteration += 1
                for index, step in enumerate(cfg.steps):
                    if self._stop_event.is_set():
                        break
                    if not user32.IsWindow(cfg.hwnd):
                        reason = "Окно закрыто — кликер остановлен."
                        return
                    ok = self._execute(step)
                    if ok:
                        failures = 0
                        if step.kind != KIND_PAUSE:
                            performed += 1
                    else:
                        failures += 1
                        if failures >= MAX_POST_FAILURES:
                            reason = (f"Окно не принимает ввод ({MAX_POST_FAILURES} ошибок подряд, "
                                      f"код {winapi.last_error()}). Кликер остановлен.")
                            return
                    self._on_progress(iteration, index, performed, bool(user32.IsIconic(cfg.hwnd)))
                    pause = random.uniform(step.delay_min, step.delay_max)
                    if self._stop_event.wait(pause):
                        break
        finally:
            if cfg.input_mode == input_backend.MODE_SEND and cfg.restore_cursor:
                user32.SetCursorPos(*origin)
            self._on_finished(performed, reason)

    def _execute(self, step: Step) -> bool:
        cfg = self.config
        if step.kind == KIND_MOUSE:
            if step.use_cursor:
                x = y = None
            else:
                x, y = step.position.resolve(cfg.hwnd)
                if step.spread_x > 0:
                    x += random.randint(-step.spread_x, step.spread_x)
                if step.spread_y > 0:
                    y += random.randint(-step.spread_y, step.spread_y)
                x, y = max(0, x), max(0, y)
            ok, target = input_backend.click(cfg.hwnd, step.button, x, y, step.clicks, cfg.input_mode)
            if ok and target:
                self._last_child = target
            return ok
        if step.kind == KIND_KEY:
            if step.key is None:
                return False
            return input_backend.press_key(cfg.hwnd, step.key, step.key_mode, cfg.input_mode, self._last_child)
        if step.kind == KIND_TEXT:
            return input_backend.type_text(cfg.hwnd, step.text, cfg.input_mode, fallback_child=self._last_child)
        return True


def sequence_to_dict(steps: list[Step]) -> list[dict]:
    return [s.to_dict() for s in steps]


def sequence_from_dict(items: list[dict] | None) -> list[Step]:
    result = []
    for item in items or []:
        try:
            result.append(Step.from_dict(item))
        except (TypeError, ValueError, KeyError):
            continue
    return result
