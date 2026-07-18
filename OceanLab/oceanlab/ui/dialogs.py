from __future__ import annotations


def center_dialog(parent, dialog, width: int | None = None, height: int | None = None) -> None:
    """Size and center a Tk dialog without embedding geometry policy in the app controller."""
    dialog.update_idletasks()
    actual_width = width or dialog.winfo_reqwidth()
    actual_height = height or dialog.winfo_reqheight()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - actual_width) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - actual_height) // 2)
    size = f"{actual_width}x{actual_height}" if width is not None and height is not None else ""
    dialog.geometry(f"{size}+{max(0, x)}+{max(0, y)}")


def close_modal(dialog) -> None:
    try:
        dialog.grab_release()
    except Exception:
        pass
    dialog.destroy()
