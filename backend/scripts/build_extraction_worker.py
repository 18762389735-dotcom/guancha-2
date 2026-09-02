"""Build a self-contained Python 3.11 SCF Event extraction worker package."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


REQUIREMENTS = (
    "psycopg[binary]>=3.2,<4.0",
    "pydantic>=2.0,<3.0",
    "cos-python-sdk-v5>=1.9.44,<2.0",
    "Pillow>=10.0,<12.0",
    "openai>=2.0,<3.0",
)

SOURCE_FILES = (
    "__init__.py",
    "auth/__init__.py",
    "auth/interfaces.py",
    "auth/models.py",
    "application/__init__.py",
    "application/job_runner.py",
    "repositories/__init__.py",
    "repositories/interfaces.py",
    "repositories/postgres.py",
    "schemas/__init__.py",
    "schemas/contracts.py",
    "infrastructure/__init__.py",
    "infrastructure/storage/__init__.py",
    "infrastructure/storage/cos.py",
    "infrastructure/storage/factory.py",
    "infrastructure/storage/interfaces.py",
    "infrastructure/storage/memory.py",
    "infrastructure/temporary_images.py",
    "providers/__init__.py",
    "providers/execution.py",
    "providers/fake.py",
    "providers/mimo.py",
    "providers/openai.py",
    "functions/__init__.py",
    "functions/extraction_worker.py",
)


def _copy_source(source_root: Path, output: Path) -> None:
    for relative in SOURCE_FILES:
        source = source_root / relative
        target = output / "guancha_api" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _zip_directory(directory: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(directory))


DEFAULT_PYTHON_PLATFORM = "x86_64-manylinux2014"


def _install_dependencies(output: Path, *, python_platform: str) -> None:
    if shutil.which("uv"):
        command = [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-compile",
            "--target",
            str(output),
            "--python-version",
            "3.11",
            "--python-platform",
            python_platform,
            *REQUIREMENTS,
        ]
    else:
        if sys.platform != "linux" or python_platform != DEFAULT_PYTHON_PLATFORM:
            raise RuntimeError(
                "cross-platform worker builds require uv; use a Linux Python 3.11 build host"
            )
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-compile",
            "--target",
            str(output),
            *REQUIREMENTS,
        ]
    subprocess.run(command, check=True)


def build(
    output: Path,
    *,
    force: bool = False,
    python_platform: str = DEFAULT_PYTHON_PLATFORM,
) -> tuple[Path, int, int]:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("The SCF artifact must be built with Python 3.11")
    project_root = Path(__file__).resolve().parents[2]
    source_root = project_root / "backend" / "src" / "guancha_api"
    entrypoint = project_root / "functions" / "guancha-extraction-worker" / "index.py"
    if output.exists():
        if not force:
            raise FileExistsError(f"output exists; rerun with --force: {output}")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    _install_dependencies(output, python_platform=python_platform)
    _copy_source(source_root, output)
    shutil.copy2(entrypoint, output / "index.py")
    archive = output.with_suffix(".zip")
    if archive.exists():
        archive.unlink()
    _zip_directory(output, archive)
    total_bytes = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    file_count = sum(1 for path in output.rglob("*") if path.is_file())
    return archive, total_bytes, file_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/guancha-extraction-worker"),
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--python-platform",
        default=DEFAULT_PYTHON_PLATFORM,
        help="target platform for dependency wheels (default: x86_64-manylinux2014)",
    )
    args = parser.parse_args()
    archive, total_bytes, file_count = build(
        args.output,
        force=args.force,
        python_platform=args.python_platform,
    )
    print(f"artifact_directory={args.output}")
    print(f"artifact_zip={archive}")
    print(f"artifact_files={file_count}")
    print(f"artifact_uncompressed_bytes={total_bytes}")
    print(f"artifact_zip_bytes={archive.stat().st_size}")


if __name__ == "__main__":
    main()
