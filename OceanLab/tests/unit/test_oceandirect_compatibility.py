from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from oceanlab.adapters.oceandirect import load_oceandirect


class OceanDirectCompatibilityTest(unittest.TestCase):
    def test_sdk_annotations_are_postponed_for_legacy_python(self) -> None:
        module_prefix = "oceandirect"
        previous_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == module_prefix or name.startswith(f"{module_prefix}.")
        }
        previous_path = list(sys.path)

        try:
            for name in previous_modules:
                sys.modules.pop(name, None)

            with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp_dir:
                sdk_home = Path(temp_dir)
                package_dir = sdk_home / "Python" / "oceandirect"
                package_dir.mkdir(parents=True)
                (package_dir / "__init__.py").write_text("", encoding="utf-8")
                (package_dir / "OceanDirectAPI.py").write_text(
                    "\n".join(
                        (
                            "class OceanDirectError(Exception):",
                            "    pass",
                            "",
                            "class OceanDirectAPI:",
                            "    def values(self) -> list[int]:",
                            "        return [1]",
                        )
                    ),
                    encoding="utf-8",
                )

                api_type, error_type = load_oceandirect(sdk_home)

                self.assertEqual(api_type().values(), [1])
                self.assertTrue(issubclass(error_type, Exception))
                self.assertEqual(api_type.values.__annotations__["return"], "list[int]")
        finally:
            for name in list(sys.modules):
                if name == module_prefix or name.startswith(f"{module_prefix}."):
                    sys.modules.pop(name, None)
            sys.modules.update(previous_modules)
            sys.path[:] = previous_path


if __name__ == "__main__":
    unittest.main()
