#!/usr/bin/env python3
"""
Real E2E Test Runner

This script runs the real end-to-end tests with external dependencies.
It provides options for different test categories and cost control.
"""

import os
import sys
import argparse
import subprocess
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def check_requirements():
    """Check if requirements are met for real E2E tests"""
    print("🔍 Checking requirements for real E2E tests...")

    # Check API key
    if not os.getenv("OPENROUTER_API_KEY"):
        print("❌ OPENROUTER_API_KEY environment variable not set")
        print("   Set it with: export OPENROUTER_API_KEY='your-key-here'")
        return False

    print("✅ OpenRouter API key found")

    # Check internet connectivity
    try:
        import requests
        response = requests.get("https://api.openrouter.ai/v1/models", timeout=5)
        if response.status_code == 200:
            print("✅ Internet connectivity confirmed")
        else:
            print("⚠️  Internet connectivity issues")
            return False
    except Exception as e:
        print(f"⚠️  Internet connectivity check failed: {e}")
        print("   This may be due to network restrictions or temporary issues")
        print("   Tests will still run, but may fail if connectivity is required")
        # Don't fail on connectivity check - let tests handle it

    return True


def run_quality_tests(limit: int | None = None):
    """Run quality tests with DeepSeek V3.2-Exp"""
    print("\n🎯 Running Quality Tests (DeepSeek V3.2-Exp)")
    print("   Cost: ~$0.026 for 20 papers")
    print("   Purpose: Translation quality validation")

    cmd = [
        "python", "-m", "pytest",
        "tests/test_e2e_real.py::TestRealAPIIntegration::test_real_openrouter_translation_quality",
        "tests/test_e2e_real.py::TestRealAPIIntegration::test_real_paper_translation",
        "-v", "-s", "--tb=short"
    ]

    env = os.environ.copy()
    env["RUN_REAL_E2E"] = "1"
    if limit is not None:
        env["RUN_REAL_E2E_LIMIT"] = str(limit)
    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, env=env)
    return result.returncode == 0


def run_batch_tests(limit: int | None = None):
    """Run batch tests with free models"""
    print("\n🚀 Running Batch Tests (Free Models)")
    print("   Cost: $0.00 for 500+ papers")
    print("   Purpose: Scale and performance validation")

    cmd = [
        "python", "-m", "pytest",
        "tests/test_e2e_real.py::TestRealAPIIntegration::test_real_openrouter_translation_batch_free",
        "tests/test_e2e_real.py::TestRealPerformanceAndScale::test_real_batch_processing_free_models",
        "-v", "-s", "--tb=short"
    ]

    env = os.environ.copy()
    env["RUN_REAL_E2E"] = "1"
    if limit is not None:
        env["RUN_REAL_E2E_LIMIT"] = str(limit)
    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, env=env)
    return result.returncode == 0


def run_pipeline_tests(limit: int | None = None):
    """Run complete pipeline tests"""
    print("\n🔄 Running Pipeline Tests")
    print("   Cost: ~$0.026 for 2 papers")
    print("   Purpose: End-to-end workflow validation")

    cmd = [
        "python", "-m", "pytest",
        "tests/test_e2e_real.py::TestRealPipelineIntegration::test_real_end_to_end_pipeline",
        "-v", "-s", "--tb=short"
    ]

    env = os.environ.copy()
    env["RUN_REAL_E2E"] = "1"
    if limit is not None:
        env["RUN_REAL_E2E_LIMIT"] = str(limit)
    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, env=env)
    return result.returncode == 0


def run_all_tests(limit: int | None = None):
    """Run all real E2E tests"""
    print("\n🌟 Running All Real E2E Tests")
    print("   Total Cost: ~$0.026")
    print("   Purpose: Comprehensive validation")

    cmd = [
        "python", "-m", "pytest",
        "tests/test_e2e_real.py",
        "-v", "-s", "--tb=short"
    ]

    env = os.environ.copy()
    env["RUN_REAL_E2E"] = "1"
    if limit is not None:
        env["RUN_REAL_E2E_LIMIT"] = str(limit)
    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, env=env)
    return result.returncode == 0


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Run real E2E tests")
    parser.add_argument(
        "--category",
        choices=["quality", "batch", "pipeline", "all"],
        default="all",
        help="Test category to run"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of papers to process (overrides category defaults)"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check requirements, don't run tests"
    )

    args = parser.parse_args()

    print("🧪 Real E2E Test Runner")
    print("=" * 50)

    # Check requirements
    if not check_requirements():
        print("\n❌ Requirements not met. Exiting.")
        return 1

    if args.check_only:
        print("\n✅ All requirements met!")
        return 0

    # Determine limit (category defaults if not provided)
    limit = args.limit
    if limit is None:
        if args.category == "quality":
            limit = 20
        elif args.category == "batch":
            limit = 500
        elif args.category == "pipeline":
            limit = 2
        else:
            limit = 20

    # Run tests based on category
    start_time = time.time()

    if args.category == "quality":
        success = run_quality_tests(limit=limit)
    elif args.category == "batch":
        success = run_batch_tests(limit=limit)
    elif args.category == "pipeline":
        success = run_pipeline_tests(limit=limit)
    else:  # all
        success = run_all_tests(limit=limit)

    end_time = time.time()
    duration = end_time - start_time

    print(f"\n⏱️  Test duration: {duration:.2f} seconds")

    if success:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
