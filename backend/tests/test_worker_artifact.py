from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys


def _load_builder():
    builder_path = Path(__file__).parents[1] / "scripts" / "build_extraction_worker.py"
    spec = importlib.util.spec_from_file_location("build_extraction_worker", builder_path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load worker builder")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


def test_built_worker_imports_only_from_artifact(tmp_path: Path) -> None:
    builder = _load_builder()
    artifact = tmp_path / "worker-artifact"
    python_platform = (
        "x86_64-pc-windows-msvc"
        if sys.platform == "win32"
        else builder.DEFAULT_PYTHON_PLATFORM
    )
    archive, _, _ = builder.build(
        artifact,
        force=True,
        python_platform=python_platform,
    )

    interfaces = artifact / "guancha_api" / "providers" / "interfaces.py"
    assert interfaces.is_file()
    assert archive.is_file()

    import_script = r'''
import sys
from pathlib import Path

artifact = Path(sys.argv[1]).resolve()
repository = Path(sys.argv[2]).resolve()

def is_under(path, root):
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True

sys.path = [str(artifact)] + [
    entry for entry in sys.path
    if entry and not is_under(Path(entry), repository)
]
for name in list(sys.modules):
    if name == "index" or name.startswith("guancha_api"):
        del sys.modules[name]

import index
import guancha_api
from guancha_api.functions import extraction_worker

assert is_under(Path(index.__file__), artifact)
assert is_under(Path(guancha_api.__file__), artifact)
assert is_under(Path(extraction_worker.__file__), artifact)
for name, module in sys.modules.items():
    if name.startswith("guancha_api") and getattr(module, "__file__", None):
        assert is_under(Path(module.__file__), artifact), (name, module.__file__)
'''
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(artifact)
    completed = subprocess.run(
        [sys.executable, "-c", import_script, str(artifact), str(Path(__file__).parents[1])],
        cwd=artifact,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
