"""A standalone install must never accidentally import the execution platform."""

import ast
from pathlib import Path


def test_runtime_has_no_platform_or_plugin_imports():
    root = Path(__file__).parents[1] / "baseline_hub"
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                assert not (name == "app" or name.startswith(("app.", "autotune_"))), path


def test_user_route_adapters_do_not_import_each_other():
    root = Path(__file__).parents[1] / "baseline_hub"
    for module, other in (("api", "ui_api"), ("ui_api", "api")):
        tree = ast.parse((root / f"{module}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != f"baseline_hub.{other}", (
                    "User routes must call shared business operations, not another route adapter"
                )


def test_platform_startup_does_not_import_legacy_execution():
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-c", """
import sys
from baseline_hub.config import Settings
from baseline_hub.main import create_app
app = create_app(Settings(database_url="sqlite://"))
for name in ("catalog_api", "reports", "worker", "execution", "board", "scenarios"):
    assert "baseline_hub." + name not in sys.modules, name
paths = app.openapi()["paths"]
for path in (
    "/api/auth/users", "/api/auth/password", "/api/auth/api-keys",
    "/api/submissions", "/api/reports",
):
    assert path not in paths, path
assert "/api/health" in paths
assert "/api/auth/login" in paths
assert "/api/catalog-export/publication-previews" in paths
"""],
        check=True,
    )
