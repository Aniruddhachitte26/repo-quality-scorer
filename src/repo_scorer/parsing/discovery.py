"""Find the Python files in a cloned repository."""

import os
from pathlib import Path

# Directories that never contain the project's own source code.
SKIP_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env", ".env",
    ".tox", ".nox", ".eggs",
    "build", "dist", "node_modules", "site-packages",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}

TEST_DIR_NAMES = {"test", "tests", "testing"}


def iter_python_files(root: Path) -> list[Path]:
    """Return every .py file under root, skipping vendored/generated directories."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Pruning dirnames in place stops os.walk from descending into them.
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        ]
        files.extend(Path(dirpath) / f for f in filenames if f.endswith(".py"))
    return sorted(files)


def is_test_path(rel_path: Path) -> bool:
    """True if a repo-relative path looks like test code."""
    name = rel_path.name
    if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
        return True
    return any(part in TEST_DIR_NAMES for part in rel_path.parts[:-1])
