from __future__ import annotations

import ast
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "oceanlab"


def imports_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_domain_does_not_depend_on_outer_layers(self) -> None:
        forbidden = ("application", "adapters", "ports", "ui", "oceanlab.application", "oceanlab.adapters", "oceanlab.ports", "oceanlab.ui", "tkinter", "oceandirect")
        for path in (PACKAGE_ROOT / "domain").glob("*.py"):
            imports = imports_in(path)
            self.assertFalse(any(name.startswith(forbidden) for name in imports), path)

    def test_application_does_not_import_adapters_or_ui(self) -> None:
        for path in (PACKAGE_ROOT / "application").glob("*.py"):
            imports = imports_in(path)
            self.assertFalse(any(name.startswith(("adapters", "ui", "oceanlab.adapters", "oceanlab.ui", "tkinter", "oceandirect")) for name in imports), path)

    def test_adapters_do_not_import_ui(self) -> None:
        for path in (PACKAGE_ROOT / "adapters").glob("*.py"):
            self.assertFalse(any(name.startswith(("ui", "oceanlab.ui")) for name in imports_in(path)), path)

    def test_ui_does_not_import_hardware_adapters(self) -> None:
        for path in (PACKAGE_ROOT / "ui").rglob("*.py"):
            imports = imports_in(path)
            self.assertFalse(any(name.startswith(("adapters", "oceanlab.adapters", "oceandirect")) for name in imports), path)

    def test_oceandirect_import_is_isolated(self) -> None:
        offenders = []
        for path in PACKAGE_ROOT.rglob("*.py"):
            if path.name == "oceandirect.py":
                continue
            if "oceandirect.OceanDirectAPI" in path.read_text(encoding="utf-8"):
                offenders.append(path)
        self.assertEqual(offenders, [])

    def test_tkinter_is_limited_to_ui_and_startup(self) -> None:
        offenders = []
        allowed = {PACKAGE_ROOT / "bootstrap.py", PACKAGE_ROOT / "cli.py"}
        for path in PACKAGE_ROOT.rglob("*.py"):
            if path in allowed or "ui" in path.relative_to(PACKAGE_ROOT).parts:
                continue
            if any(name.startswith("tkinter") for name in imports_in(path)):
                offenders.append(path)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
