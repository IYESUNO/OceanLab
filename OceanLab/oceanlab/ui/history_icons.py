"""Compact Lucide controls with DPI-aware, antialiased bundled artwork.

Artwork and license: assets/history_icons. Only Tk and the standard library
are needed at runtime; alpha masks allow arbitrary user-selected colors.
"""
from __future__ import annotations

import base64
import json
import struct
import tkinter as tk
import zlib

from ..config import PROJECT_DIR


_MASKS = None


def _chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)


def _icon(master, name, requested_size, color):
    global _MASKS
    if _MASKS is None:
        _MASKS = json.loads((PROJECT_DIR / 'assets' / 'history_icons' / 'masks.json').read_text(encoding='utf-8'))
    size = min((int(value) for value in _MASKS[name]), key=lambda value: abs(value - requested_size))
    root = master._root()
    if not hasattr(root, '_history_icon_images'):
        root._history_icon_images = {}
    key = (name, size, color)
    if key not in root._history_icon_images:
        alpha = zlib.decompress(base64.b64decode(_MASKS[name][str(size)]))
        rgb = bytes(round(channel / 257) for channel in master.winfo_rgb(color))
        rows = b''.join(b'\x00' + b''.join(rgb + bytes((opacity,)) for opacity in alpha[y * size:(y + 1) * size])
                        for y in range(size))
        png = (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 6, 0, 0, 0))
               + _chunk(b'IDAT', zlib.compress(rows)) + _chunk(b'IEND', b''))
        root._history_icon_images[key] = tk.PhotoImage(master=root, data=base64.b64encode(png))
    return root._history_icon_images[key]


class HistoryIconButton(tk.Canvas):
    """24-DIP hit target, 18-DIP icon, mouse/keyboard and live variable state."""

    def __init__(self, master, *, icon, command, variable=None, value=True, toggle=False,
                 foreground='#475569', state='normal', background='#ffffff', **kwargs):
        self.icon = icon
        self.command = command
        self.variable = variable
        self.value = value
        self.toggle = toggle
        self.color = foreground
        self.base_background = background
        self.disabled = state == 'disabled'
        self.hovered = False
        self.focused = False
        self.pressed = False
        self.scale = max(1.0, min(2.5, float(master.winfo_fpixels('1i')) / 96.0))
        self.side = round(24 * self.scale)
        super().__init__(master, width=self.side, height=self.side, background=background,
                         highlightthickness=0, borderwidth=0, takefocus=True, cursor='hand2', **kwargs)
        self._back = self.create_polygon(0, 0, 0, 0, 0, 0, fill=background, outline='', smooth=True)
        self._image = self.create_image(self.side / 2, self.side / 2)
        self._focus = self.create_rectangle(1, 1, self.side - 1, self.side - 1, outline='', dash=(2, 2))
        self._trace = variable.trace_add('write', self._paint) if variable is not None else None
        self.bind('<Enter>', lambda _event: self._interaction(hovered=True))
        self.bind('<Leave>', lambda _event: self._interaction(hovered=False, pressed=False))
        self.bind('<FocusIn>', lambda _event: self._interaction(focused=True))
        self.bind('<FocusOut>', lambda _event: self._interaction(focused=False, pressed=False))
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Return>', self._keyboard)
        self.bind('<space>', self._keyboard)
        self.bind('<Destroy>', self._dispose, add='+')
        self._paint()

    def _interaction(self, **states):
        for key, value in states.items():
            setattr(self, key, value)
        self._paint()

    def _press(self, _event):
        if not self.disabled:
            self.focus_set()
            self._interaction(pressed=True)

    def _release(self, event):
        activate = self.pressed and 0 <= event.x < self.side and 0 <= event.y < self.side
        self._interaction(pressed=False)
        if activate:
            self.invoke()

    def _keyboard(self, _event):
        self.invoke()
        return 'break'

    def invoke(self):
        if self.disabled:
            return
        if self.variable is not None:
            self.variable.set(not self.variable.get() if self.toggle else self.value)
        self.command()

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if 'background' in kwargs:
            self.base_background = kwargs['background']
        for key in ('command', 'foreground', 'disabledforeground', 'state'):
            if key not in kwargs:
                continue
            value = kwargs.pop(key)
            if key == 'command':
                self.command = value
            elif key == 'foreground':
                self.color = value
            elif key == 'state':
                self.disabled = value == 'disabled'
        result = super().configure(**kwargs) if kwargs else None
        if hasattr(self, '_image'):
            self._paint()
        return result

    config = configure

    def cget(self, key):
        if key == 'state':
            return 'disabled' if self.disabled else 'normal'
        if key == 'foreground':
            return self.color
        return super().cget(key)

    def _paint(self, *_args):
        selected = self.variable is not None and self.variable.get() == self.value
        active = self.icon == 'crosshair' and selected
        background = '#e8f1fc' if active else self.base_background
        if self.hovered and not self.disabled:
            background = '#feecec' if self.icon == 'trash' else '#e5edf6'
        if self.pressed:
            background = '#d5e5f7'
        color = self.color
        name = self.icon
        if name == 'eye' and not selected:
            name, color = 'eye-off', '#94a3b8'
        elif name == 'crosshair':
            color = '#1565c0' if active else '#94a3b8'
        elif name == 'trash' and self.hovered:
            color = '#b91c1c'
        # A Turbo-controlled palette still shows its actual curve color.
        if self.disabled and name != 'palette':
            color = '#94a3b8'
        self.image = _icon(self, name, round(18 * self.scale), color)
        self.itemconfigure(self._image, image=self.image)
        inset, corner, edge = self.scale, 4 * self.scale, self.side - self.scale
        self.coords(self._back, inset + corner, inset, edge - corner, inset, edge, inset,
                    edge, inset + corner, edge, edge - corner, edge, edge, edge - corner, edge,
                    inset + corner, edge, inset, edge, inset, edge - corner, inset, inset + corner, inset, inset)
        self.itemconfigure(self._back, fill=background)
        self.itemconfigure(self._focus, outline='#1565c0' if self.focused else '')
        super().configure(cursor='arrow' if self.disabled else 'hand2')

    def _dispose(self, event):
        if event.widget is self and self._trace is not None:
            self.variable.trace_remove('write', self._trace)
            self._trace = None
