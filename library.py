"""
Библиотека именованных действий и последовательностей и профили окон.

Окно идентифицируется не по HWND (он меняется при каждом запуске программы-цели),
а по имени процесса, классу окна и заголовку. При повторном запуске цели профиль
находится заново: сначала по точному заголовку, затем по началу заголовка,
затем только по процессу и классу.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import winapi
from actions import Step, sequence_from_dict, sequence_to_dict


# --------------------------------------------------------------------------
# Идентификация окна
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class WindowIdentity:
    process: str      # notepad.exe
    class_name: str   # Notepad
    title: str        # заголовок на момент выбора

    @property
    def key(self) -> str:
        return f"{self.process.lower()}|{self.class_name}"

    @property
    def label(self) -> str:
        proc = self.process or "неизвестный процесс"
        return f"{proc} · {self.title}" if self.title else proc

    def to_dict(self) -> dict:
        return {"process": self.process, "class_name": self.class_name, "title": self.title}

    @staticmethod
    def from_dict(d: dict) -> "WindowIdentity":
        return WindowIdentity(str(d.get("process", "")), str(d.get("class_name", "")), str(d.get("title", "")))

    @staticmethod
    def of(hwnd: int) -> "WindowIdentity":
        return WindowIdentity(winapi.window_process_name(hwnd), winapi.window_class(hwnd), winapi.window_title(hwnd))


def match_window(identity: WindowIdentity, candidates: list[tuple[int, WindowIdentity]]) -> int | None:
    """Находит HWND окна, соответствующего сохранённой идентичности."""
    same = [(h, i) for h, i in candidates if i.key == identity.key]
    if not same:
        return None
    for h, i in same:
        if i.title == identity.title:
            return h
    title = identity.title.lower()
    # Заголовок часто меняется только частично: «Документ - Блокнот» -> «*Документ - Блокнот».
    for h, i in same:
        t = i.title.lower()
        if title and (t.startswith(title) or t.endswith(title) or title in t or t in title):
            return h
    return same[0][0]


# --------------------------------------------------------------------------
# Элементы библиотеки
# --------------------------------------------------------------------------
def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class NamedAction:
    name: str
    step: Step = field(default_factory=Step)
    interval_min: float = 0.4
    interval_max: float = 0.6
    count: int = 0
    id: str = field(default_factory=_new_id)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "step": self.step.to_dict(),
                "interval_min": self.interval_min, "interval_max": self.interval_max, "count": self.count}

    @staticmethod
    def from_dict(d: dict) -> "NamedAction":
        return NamedAction(
            name=str(d.get("name", "Действие")), step=Step.from_dict(d.get("step") or {}),
            interval_min=float(d.get("interval_min", 0.4)), interval_max=float(d.get("interval_max", 0.6)),
            count=int(d.get("count", 0)), id=str(d.get("id") or _new_id()),
        )


@dataclass
class NamedSequence:
    name: str
    steps: list[Step] = field(default_factory=list)
    repeat: int = 0
    id: str = field(default_factory=_new_id)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "steps": sequence_to_dict(self.steps), "repeat": self.repeat}

    @staticmethod
    def from_dict(d: dict) -> "NamedSequence":
        return NamedSequence(
            name=str(d.get("name", "Последовательность")), steps=sequence_from_dict(d.get("steps")),
            repeat=int(d.get("repeat", 0)), id=str(d.get("id") or _new_id()),
        )


@dataclass
class Profile:
    identity: WindowIdentity
    action_ids: list[str] = field(default_factory=list)
    sequence_ids: list[str] = field(default_factory=list)
    current_action: str | None = None
    current_sequence: str | None = None
    run_mode: str = "single"

    def to_dict(self) -> dict:
        return {"identity": self.identity.to_dict(), "action_ids": list(self.action_ids),
                "sequence_ids": list(self.sequence_ids), "current_action": self.current_action,
                "current_sequence": self.current_sequence, "run_mode": self.run_mode}

    @staticmethod
    def from_dict(d: dict) -> "Profile":
        return Profile(
            identity=WindowIdentity.from_dict(d.get("identity") or {}),
            action_ids=[str(x) for x in d.get("action_ids", [])],
            sequence_ids=[str(x) for x in d.get("sequence_ids", [])],
            current_action=d.get("current_action"), current_sequence=d.get("current_sequence"),
            run_mode=str(d.get("run_mode", "single")),
        )


class Library:
    def __init__(self) -> None:
        self.actions: dict[str, NamedAction] = {}
        self.sequences: dict[str, NamedSequence] = {}
        self.profiles: dict[str, Profile] = {}

    # ----------------------------------------------------------- хранение
    def to_dict(self) -> dict:
        return {
            "actions": [a.to_dict() for a in self.actions.values()],
            "sequences": [s.to_dict() for s in self.sequences.values()],
            "profiles": {key: p.to_dict() for key, p in self.profiles.items()},
        }

    @staticmethod
    def from_dict(d: dict | None) -> "Library":
        lib = Library()
        d = d or {}
        for item in d.get("actions", []):
            try:
                a = NamedAction.from_dict(item)
                lib.actions[a.id] = a
            except (TypeError, ValueError, KeyError):
                continue
        for item in d.get("sequences", []):
            try:
                s = NamedSequence.from_dict(item)
                lib.sequences[s.id] = s
            except (TypeError, ValueError, KeyError):
                continue
        for key, item in (d.get("profiles") or {}).items():
            try:
                lib.profiles[key] = Profile.from_dict(item)
            except (TypeError, ValueError, KeyError):
                continue
        lib._prune()
        return lib

    def _prune(self) -> None:
        for p in self.profiles.values():
            p.action_ids = [i for i in p.action_ids if i in self.actions]
            p.sequence_ids = [i for i in p.sequence_ids if i in self.sequences]
            if p.current_action not in p.action_ids:
                p.current_action = p.action_ids[0] if p.action_ids else None
            if p.current_sequence not in p.sequence_ids:
                p.current_sequence = p.sequence_ids[0] if p.sequence_ids else None

    # ----------------------------------------------------------- профили
    def profile_for(self, identity: WindowIdentity) -> Profile:
        profile = self.profiles.get(identity.key)
        if profile is None:
            profile = Profile(identity=identity)
            self.profiles[identity.key] = profile
        elif identity.title and not profile.identity.title:
            profile.identity = identity
        return profile

    def windows_of_action(self, action_id: str) -> list[str]:
        return [p.identity.label for p in self.profiles.values() if action_id in p.action_ids]

    def windows_of_sequence(self, seq_id: str) -> list[str]:
        return [p.identity.label for p in self.profiles.values() if seq_id in p.sequence_ids]

    # ----------------------------------------------------------- действия
    def add_action(self, profile: Profile, action: NamedAction) -> NamedAction:
        self.actions[action.id] = action
        if action.id not in profile.action_ids:
            profile.action_ids.append(action.id)
        profile.current_action = action.id
        return action

    def unlink_action(self, profile: Profile, action_id: str) -> None:
        if action_id in profile.action_ids:
            profile.action_ids.remove(action_id)
        if profile.current_action == action_id:
            profile.current_action = profile.action_ids[0] if profile.action_ids else None

    def delete_action(self, action_id: str) -> None:
        self.actions.pop(action_id, None)
        for p in self.profiles.values():
            self.unlink_action(p, action_id)

    # ------------------------------------------------- последовательности
    def add_sequence(self, profile: Profile, seq: NamedSequence) -> NamedSequence:
        self.sequences[seq.id] = seq
        if seq.id not in profile.sequence_ids:
            profile.sequence_ids.append(seq.id)
        profile.current_sequence = seq.id
        return seq

    def unlink_sequence(self, profile: Profile, seq_id: str) -> None:
        if seq_id in profile.sequence_ids:
            profile.sequence_ids.remove(seq_id)
        if profile.current_sequence == seq_id:
            profile.current_sequence = profile.sequence_ids[0] if profile.sequence_ids else None

    def delete_sequence(self, seq_id: str) -> None:
        self.sequences.pop(seq_id, None)
        for p in self.profiles.values():
            self.unlink_sequence(p, seq_id)

    # ----------------------------------------------------------- миграция
    def migrate_v2(self, settings: dict) -> None:
        """Переносит одиночное действие и последовательность из настроек 2.0.0 в библиотеку."""
        single = settings.get("single") or {}
        if single.get("step"):
            try:
                a = NamedAction(
                    name="Действие 1", step=Step.from_dict(single["step"]),
                    interval_min=float(str(single.get("interval_min", "0.4")).replace(",", ".")),
                    interval_max=float(str(single.get("interval_max", "0.6")).replace(",", ".")),
                    count=int(single.get("count", 0) or 0),
                )
                self.actions[a.id] = a
            except (TypeError, ValueError, KeyError):
                pass
        seq = settings.get("sequence") or {}
        steps = sequence_from_dict(seq.get("steps"))
        if steps:
            try:
                s = NamedSequence(name="Последовательность 1", steps=steps, repeat=int(seq.get("repeat", 0) or 0))
                self.sequences[s.id] = s
            except (TypeError, ValueError):
                pass
