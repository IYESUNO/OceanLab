from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(frozen=True)
class ApplicationEvent:
    """Typed envelope; tuple compatibility keeps the existing Tk adapter incremental."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[Any]:
        yield self.kind
        yield self.payload

    def __getitem__(self, index: int) -> Any:
        return (self.kind, self.payload)[index]


@dataclass(frozen=True)
class AcquisitionCommand:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
