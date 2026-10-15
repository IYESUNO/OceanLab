"""Compact, read-only names for historical spectrum lists."""
from __future__ import annotations

from tkinter import font as tkfont, ttk


class EllipsisLabel(ttk.Label):
    """Keep the source text intact while fitting one line to actual width."""

    def __init__(self, master, *, textvariable, **kwargs):
        self.source = textvariable
        super().__init__(master, width=1, anchor="w", **kwargs)
        self._trace = self.source.trace_add("write", self.refresh)
        self.bind("<Configure>", self.refresh, add="+")
        self.bind("<Destroy>", self._dispose, add="+")
        self.refresh()

    def refresh(self, *_args):
        if not self.winfo_exists():
            return
        text = self.source.get().replace("\n", " · ")
        available = max(0, self.winfo_width() - 4)
        specification = ttk.Style(self).lookup(self.cget("style") or "TLabel", "font") or "TkDefaultFont"
        font = tkfont.Font(self, font=specification)
        if font.measure(text) <= available:
            display = text
        elif font.measure("…") > available:
            display = ""
        else:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if font.measure(text[:middle] + "…") <= available:
                    low = middle
                else:
                    high = middle - 1
            display = text[:low] + "…"
        self.configure(text=display)

    def _dispose(self, event):
        if event.widget is self:
            self.source.trace_remove("write", self._trace)
