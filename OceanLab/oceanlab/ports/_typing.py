from __future__ import annotations

try:
    from typing import Protocol
except ImportError:  # Python 3.7 compatibility for the Windows 7 build.
    class Protocol:
        pass


__all__ = ["Protocol"]
