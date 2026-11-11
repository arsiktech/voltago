"""The test suite must never open the private community store in data/.

Only server.py imports community.py, so any test module that imports either one
must repoint the store for its whole life. The check reads each module's source, so
it does not depend on the order in which unittest discover runs the modules.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import community  # noqa: E402
from tests import _support  # noqa: E402

STORE_IMPORTERS = {"server", "community"}


def setUpModule():
    _support.isolate_community_store()


def _imports_store(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] in STORE_IMPORTERS for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in STORE_IMPORTERS:
            return True
    return False


def _module_setup_isolates_store(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "setUpModule":
            return any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "isolate_community_store"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "_support"
                for call in ast.walk(node)
            )
    return False


class CommunityStoreIsolationTests(unittest.TestCase):
    def test_every_module_that_reaches_the_store_isolates_it(self) -> None:
        checked, missing = [], []
        for path in sorted(Path(__file__).resolve().parent.glob("test*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if not _imports_store(tree):
                continue
            checked.append(path.name)
            if not _module_setup_isolates_store(tree):
                missing.append(path.name)
        self.assertGreater(len(checked), 40, "the scan no longer finds the server test modules")
        self.assertEqual(missing, [], "add `def setUpModule(): _support.isolate_community_store()` to these modules")

    def test_store_points_outside_the_private_data_directory_while_tests_run(self) -> None:
        private = _support.PRIVATE_DATA.resolve()
        for path in (community._DB_PATH, community._PHOTOS_DIR):
            resolved = Path(path).resolve()
            self.assertFalse(resolved == private or private in resolved.parents, resolved)


if __name__ == "__main__":
    unittest.main()
