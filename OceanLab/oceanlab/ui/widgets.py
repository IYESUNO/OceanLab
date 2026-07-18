from __future__ import annotations

import tkinter as tk
from typing import Callable


class LargeCheckbutton(tk.Frame):
    """A DPI-stable checkbutton with a large, easy-to-click indicator."""

    BOX_SIZE = 32

    def __init__(
        self,
        master,
        *,
        text: str,
        variable,
        command: Callable[[], None] | None = None,
        state: str = "normal",
        background: str = "#ffffff",
    ) -> None:
        super().__init__(
            master,
            background=background,
            cursor="hand2",
            padx=2,
            pady=1,
            takefocus=True,
        )
        self._text = text
        self._variable = variable
        self._command = command
        self._state = state
        self._background = background

        self.box = tk.Canvas(
            self,
            width=self.BOX_SIZE,
            height=self.BOX_SIZE,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
        )
        self.box.pack(side="left")
        self.box_outline = self.box.create_rectangle(
            4,
            4,
            28,
            28,
            fill="#ffffff",
            outline="#64748b",
            width=2,
        )
        self.box_check = self.box.create_line(
            9,
            16,
            14,
            22,
            24,
            10,
            fill="#ffffff",
            width=3,
            capstyle="round",
            joinstyle="round",
        )
        self.text_label = tk.Label(
            self,
            text=text,
            font=("Microsoft YaHei UI", 10),
            background=background,
            foreground="#172033",
            cursor="hand2",
            padx=2,
        )
        self.text_label.pack(side="left")

        for widget in (self, self.box, self.text_label):
            widget.bind("<Button-1>", self._on_click)
        self.bind("<space>", self._on_click)
        self.bind("<Return>", self._on_click)
        self._trace_name = self._variable.trace_add("write", self._on_variable_changed)
        self.render()

    def _on_click(self, _event=None):
        if self._state == "disabled":
            return "break"
        self._variable.set(not bool(self._variable.get()))
        if self._command is not None:
            self._command()
        return "break"

    def _on_variable_changed(self, *_args) -> None:
        self.render()

    def invoke(self) -> None:
        self._on_click()

    def render(self) -> None:
        selected = bool(self._variable.get())
        disabled = self._state == "disabled"
        if disabled:
            fill = "#e5e7eb" if selected else "#f3f4f6"
            outline = "#cbd5e1"
            foreground = "#94a3b8"
            cursor = ""
        else:
            fill = "#0f6cbd" if selected else "#ffffff"
            outline = "#0f6cbd" if selected else "#64748b"
            foreground = "#172033"
            cursor = "hand2"
        self.box.itemconfigure(self.box_outline, fill=fill, outline=outline)
        self.box.itemconfigure(
            self.box_check,
            state="normal" if selected else "hidden",
            fill="#ffffff" if not disabled else "#94a3b8",
        )
        super().configure(cursor=cursor)
        self.box.configure(cursor=cursor)
        self.text_label.configure(cursor=cursor, foreground=foreground)

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if "state" in kwargs:
            state = str(kwargs.pop("state"))
            if state not in {"normal", "disabled"}:
                raise tk.TclError(f'bad state "{state}": must be normal or disabled')
            self._state = state
        if "text" in kwargs:
            self._text = str(kwargs.pop("text"))
            self.text_label.configure(text=self._text)
        result = super().configure(**kwargs)
        if hasattr(self, "box"):
            self.render()
        return result

    config = configure

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)

    def destroy(self) -> None:
        try:
            self._variable.trace_remove("write", self._trace_name)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()
