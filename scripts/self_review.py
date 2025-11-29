#!/usr/bin/env python3
"""
AI-Agent Self-Review Hook

This script performs automated code analysis for AI agents.
Instead of arbitrary waits, it runs actual checks and provides actionable feedback.

Usage: python scripts/self_review.py [--staged-only]

Returns:
- Exit 0: All checks pass
- Exit 1: Issues found (output describes what to fix)
"""

import subprocess
import sys
import re
from pathlib import Path


def run_cmd(cmd: list[str], check: bool = False) -> tuple[int, str]:
    """Run command and return (exit_code, output)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return 1, "Command timed out"
    except FileNotFoundError:
        return 0, ""  # Tool not installed, skip check


def get_changed_files(staged_only: bool = False) -> list[str]:
    """Get list of changed Python files."""
    if staged_only:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
    else:
        cmd = ["git", "diff", "--name-only", "HEAD~1..HEAD"]

    _, output = run_cmd(cmd)
    return [f for f in output.strip().split("\n") if f.endswith(".py") and f]


def check_syntax_errors(files: list[str]) -> list[str]:
    """Check for Python syntax errors."""
    issues = []
    for f in files:
        if not Path(f).exists():
            continue
        code, output = run_cmd(["python", "-m", "py_compile", f])
        if code != 0:
            issues.append(f"SYNTAX ERROR in {f}: {output.strip()}")
    return issues


def check_undefined_names(files: list[str]) -> list[str]:
    """Use pyflakes to find undefined names and unused imports."""
    issues = []
    for f in files:
        if not Path(f).exists():
            continue
        code, output = run_cmd(["python", "-m", "pyflakes", f])
        if output.strip():
            for line in output.strip().split("\n"):
                if "undefined name" in line or "imported but unused" in line:
                    issues.append(f"PYFLAKES: {line}")
    return issues


def check_obvious_bugs(files: list[str]) -> list[str]:
    """Scan for common bug patterns."""
    issues = []
    patterns = [
        (r"except:\s*$", "Bare except clause - catches SystemExit/KeyboardInterrupt"),
        (r"==\s*None|None\s*==", "Use 'is None' instead of '== None'"),
        (r"!=\s*None|None\s*!=", "Use 'is not None' instead of '!= None'"),
        (r"print\(f?['\"]DEBUG", "Debug print statement left in code"),
        (r"TODO.*HACK|HACK.*TODO|FIXME", "Unresolved TODO/HACK/FIXME marker"),
        (r"password\s*=\s*['\"][^'\"]+['\"]", "Hardcoded password detected"),
        (r"api_key\s*=\s*['\"][^'\"]+['\"]", "Hardcoded API key detected"),
    ]

    for f in files:
        if not Path(f).exists():
            continue
        try:
            content = Path(f).read_text()
            for i, line in enumerate(content.split("\n"), 1):
                for pattern, msg in patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        issues.append(f"BUG PATTERN in {f}:{i}: {msg}")
        except Exception:
            pass
    return issues


def check_indentation_errors(files: list[str]) -> list[str]:
    """Check for indentation issues (tabs vs spaces, inconsistent)."""
    issues = []
    for f in files:
        if not Path(f).exists():
            continue
        try:
            content = Path(f).read_text()
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if "\t" in line and line.startswith(" "):
                    issues.append(f"INDENT: {f}:{i}: Mixed tabs and spaces")
        except Exception:
            pass
    return issues


def check_resource_leaks() -> list[str]:
    """Check recent changes for potential resource leaks."""
    issues = []
    code, diff = run_cmd(["git", "diff", "HEAD~1..HEAD"])

    # Look for file/socket opens without context managers
    risky_patterns = [
        (r"\+.*open\([^)]+\)(?!\s*as\b)", "file open() without 'with' context manager"),
        (r"\+.*\.open\([^)]+\).*\n[^+]*(?!\.close\(\))", "possible unclosed resource"),
    ]

    for pattern, msg in risky_patterns:
        if re.search(pattern, diff, re.MULTILINE):
            issues.append(f"RESOURCE LEAK risk: {msg}")

    return issues


def check_complexity_added() -> list[str]:
    """Analyze if recent changes add unnecessary complexity."""
    issues = []
    code, diff = run_cmd(["git", "diff", "--stat", "HEAD~1..HEAD"])

    # Check for signs of overengineering
    lines_added = 0
    for line in diff.split("\n"):
        match = re.search(r"(\d+) insertion", line)
        if match:
            lines_added += int(match.group(1))

    if lines_added > 500:
        issues.append(f"COMPLEXITY: Large change ({lines_added} lines added) - consider breaking into smaller commits")

    # Check for new abstractions
    code, diff_content = run_cmd(["git", "diff", "HEAD~1..HEAD"])
    new_classes = len(re.findall(r"^\+class\s+\w+", diff_content, re.MULTILINE))
    new_funcs = len(re.findall(r"^\+\s*def\s+\w+", diff_content, re.MULTILINE))

    if new_classes > 3:
        issues.append(f"COMPLEXITY: {new_classes} new classes added - is this overengineered?")

    return issues


def check_test_coverage(files: list[str]) -> list[str]:
    """Check if tests exist for changed modules."""
    issues = []
    src_files = [f for f in files if "test" not in f.lower() and f.startswith("src/")]

    for f in src_files:
        # Look for corresponding test file
        test_path = f.replace("src/", "tests/").replace(".py", "_test.py")
        alt_test_path = f.replace("src/", "tests/test_")

        if not Path(test_path).exists() and not Path(alt_test_path).exists():
            issues.append(f"COVERAGE: No test file found for {f}")

    return issues


def main():
    staged_only = "--staged-only" in sys.argv

    print("AI AGENT SELF-REVIEW")
    print("=" * 50)
    print()

    files = get_changed_files(staged_only)
    if not files:
        print("No Python files changed.")
        print("RESULT: PASS")
        return 0

    print(f"Analyzing {len(files)} changed file(s)...")
    print()

    all_issues = []

    # Run checks
    checks = [
        ("Syntax Errors", check_syntax_errors(files)),
        ("Undefined Names", check_undefined_names(files)),
        ("Obvious Bugs", check_obvious_bugs(files)),
        ("Indentation", check_indentation_errors(files)),
        ("Resource Leaks", check_resource_leaks()),
        ("Complexity", check_complexity_added()),
    ]

    for name, issues in checks:
        if issues:
            print(f"[FAIL] {name}:")
            for issue in issues:
                print(f"  - {issue}")
            all_issues.extend(issues)
        else:
            print(f"[PASS] {name}")

    print()
    print("=" * 50)

    if all_issues:
        print(f"RESULT: FAIL ({len(all_issues)} issue(s) found)")
        print()
        print("ACTION REQUIRED:")
        print("Fix the issues above before proceeding.")
        print("Re-run this script after fixes to verify.")
        return 1
    else:
        print("RESULT: PASS")
        print()
        print("All automated checks passed.")
        print("Proceed with commit/push.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
