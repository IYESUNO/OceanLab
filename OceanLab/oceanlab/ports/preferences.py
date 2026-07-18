from __future__ import annotations

from typing import Any, Protocol


class PreferencesRepository(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, preferences: dict[str, Any]) -> None: ...
