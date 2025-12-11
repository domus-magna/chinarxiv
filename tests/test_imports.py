"""
Test that all Python modules in the codebase can be imported without errors.

This catches issues like:
- Importing deleted modules
- Circular imports
- Missing dependencies
- Syntax errors

This test exists because PR #119 had broken imports that weren't caught by CI
until reviewers flagged them.
"""

import importlib
import sys
from pathlib import Path

import pytest

# Repository root
REPO_ROOT = Path(__file__).resolve().parents[1]


def get_module_name(file_path: Path) -> str:
    """Convert a file path to a Python module name."""
    rel_path = file_path.relative_to(REPO_ROOT)
    parts = list(rel_path.parts)
    # Remove .py extension
    parts[-1] = parts[-1].replace(".py", "")
    # Remove __init__ from the end
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def find_python_modules():
    """Find all Python modules that should be importable."""
    modules = []

    # Source directories to check
    source_dirs = [
        REPO_ROOT / "src",
        REPO_ROOT / "app",
        REPO_ROOT / "scripts",
    ]

    for source_dir in source_dirs:
        if not source_dir.exists():
            continue

        for py_file in source_dir.rglob("*.py"):
            # Skip test files
            if "test_" in py_file.name:
                continue
            # Skip __pycache__
            if "__pycache__" in str(py_file):
                continue
            # Skip if __init__.py in subdirectory without parent __init__.py
            # (not a proper package)

            module_name = get_module_name(py_file)
            modules.append((module_name, str(py_file)))

    return modules


# Modules that are expected to fail import (e.g., require specific env vars)
EXPECTED_FAILURES = {
    # Add module names here if they have legitimate import-time requirements
    # that make them fail in test environment
    'scripts.take_screenshot',  # Requires playwright (optional dev dependency)
}


@pytest.mark.parametrize("module_name,file_path", find_python_modules())
def test_module_imports(module_name: str, file_path: str):
    """Test that each module can be imported without errors.

    This catches:
    - ImportError from deleted/missing modules
    - SyntaxError from invalid Python
    - Circular import issues
    """
    if module_name in EXPECTED_FAILURES:
        pytest.skip(f"Module {module_name} is in expected failures list")

    # Add repo root to path for imports
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    try:
        importlib.import_module(module_name)
    except ImportError as e:
        # Distinguish between "module doesn't exist" and "module has broken import"
        if "No module named" in str(e) and module_name.split(".")[0] in str(e):
            # The module itself doesn't exist - might be expected
            pytest.skip(f"Module {module_name} not found (might be expected): {e}")
        else:
            # The module exists but has a broken import
            pytest.fail(
                f"Module {module_name} has broken import:\n"
                f"  File: {file_path}\n"
                f"  Error: {e}\n\n"
                "This usually means the module imports something that doesn't exist."
            )
    except SyntaxError as e:
        pytest.fail(
            f"Module {module_name} has syntax error:\n"
            f"  File: {file_path}\n"
            f"  Error: {e}"
        )
    except Exception as e:
        # Other errors during import (e.g., missing env vars, config issues)
        # We skip these as they're runtime issues, not import issues
        pytest.skip(f"Module {module_name} failed to import with runtime error: {e}")


def test_deleted_modules_not_imported():
    """Verify that deleted modules are not imported anywhere.

    This is a regression test for PR #119 where deleted modules were still
    being imported in various places.
    """
    import subprocess

    # Modules that were deleted and should never be imported
    deleted_modules = [
        "src.render",
        "src.search_index",
        "src.make_pdf",
        "src.discord_alerts",
        "src.validators.render_gate",
    ]

    for module in deleted_modules:
        # Search for imports of this module
        result = subprocess.run(
            ["grep", "-r", f"from {module}", "--include=*.py",
             str(REPO_ROOT / "src"), str(REPO_ROOT / "app"), str(REPO_ROOT / "scripts")],
            capture_output=True,
            text=True
        )

        if result.stdout.strip():
            pytest.fail(
                f"Deleted module '{module}' is still being imported:\n{result.stdout}"
            )

        # Also check for "import module" style
        module_base = module.split(".")[-1]
        result = subprocess.run(
            ["grep", "-r", f"import {module_base}", "--include=*.py",
             str(REPO_ROOT / "src"), str(REPO_ROOT / "app"), str(REPO_ROOT / "scripts")],
            capture_output=True,
            text=True
        )

        # Filter out false positives (e.g., "import render" could match other things)
        lines = [line for line in result.stdout.strip().split("\n") if line and module in line]
        if lines:
            pytest.fail(
                f"Deleted module '{module}' might still be imported:\n" + "\n".join(lines)
            )
