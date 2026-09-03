from __future__ import annotations

import threading

from monkeycode.skills.types import ActiveEntry


class ActiveSkills:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[ActiveEntry] = []
        self._index: dict[str, int] = {}

    def activate(self, name: str, body: str) -> None:
        with self._lock:
            if name in self._index:
                self._entries[self._index[name]] = ActiveEntry(name=name, body=body)
                return
            self._index[name] = len(self._entries)
            self._entries.append(ActiveEntry(name=name, body=body))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._index.clear()

    def snapshot(self) -> list[ActiveEntry]:
        with self._lock:
            return list(self._entries)

    def names(self) -> list[str]:
        with self._lock:
            return [entry.name for entry in self._entries]
