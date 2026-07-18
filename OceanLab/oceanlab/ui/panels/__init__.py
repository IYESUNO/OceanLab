"""Focused Tk panel builders."""

from .acquisition import build_acquisition_workspace
from .chrome import build_footer, build_header
from .workspaces import build_absorbance_workspace, build_pl_workspace, build_processing_workspace

__all__ = [
    "build_absorbance_workspace",
    "build_acquisition_workspace",
    "build_footer",
    "build_header",
    "build_pl_workspace",
    "build_processing_workspace",
]
