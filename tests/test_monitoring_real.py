"""
Real E2E Test Monitoring utilities (no tests in this module).

Note: This module is intentionally excluded from active test collection
to avoid "no tests collected" edge cases in some environments.
"""

import json
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

import pytest

pytestmark = pytest.mark.skip("Utility module only; no tests to run")


class RealE2ETestMonitor:
    """Monitor real E2E tests with cost and performance tracking"""

    def __init__(self):
        self.start_time = time.time()
        self.test_results = []
        self.cost_tracking = {
            "total_cost": 0.0,
            "papers_processed": 0,
            "api_calls": 0,
            "model_usage": {},
        }
        self.performance_metrics = {
            "papers_per_minute": 0.0,
            "average_response_time": 0.0,
            "error_rate": 0.0,
        }

    def start_test(self, test_name: str, expected_cost: float = 0.0):
        """Start monitoring a test"""
        test_info = {
            "name": test_name,
            "start_time": time.time(),
            "expected_cost": expected_cost,
            "status": "running",
        }
        self.test_results.append(test_info)
        print(f"🧪 Starting test: {test_name}")

    def end_test(self, test_name: str, success: bool, actual_cost: float = 0.0):
        """End monitoring a test"""
        for test in self.test_results:
            if test["name"] == test_name:
                test["end_time"] = time.time()
                test["duration"] = test["end_time"] - test["start_time"]
                test["success"] = success
                test["actual_cost"] = actual_cost
                test["status"] = "completed"
                break

        status_emoji = "✅" if success else "❌"
        print(f"{status_emoji} Test completed: {test_name}")

        # Update cost tracking
        self.cost_tracking["total_cost"] += actual_cost
        if success:
            self.cost_tracking["papers_processed"] += 1

    def track_api_call(self, model: str, tokens: int, cost: float):
        """Track API call for cost monitoring"""
        self.cost_tracking["api_calls"] += 1

        if model not in self.cost_tracking["model_usage"]:
            self.cost_tracking["model_usage"][model] = {
                "calls": 0,
                "tokens": 0,
                "cost": 0.0,
            }

        self.cost_tracking["model_usage"][model]["calls"] += 1
        self.cost_tracking["model_usage"][model]["tokens"] += tokens
        self.cost_tracking["model_usage"][model]["cost"] += cost

    def calculate_performance(self):
        """Calculate performance metrics"""
        total_time = time.time() - self.start_time
        successful_tests = [t for t in self.test_results if t.get("success", False)]

        if total_time > 0:
            self.performance_metrics["papers_per_minute"] = self.cost_tracking[
                "papers_processed"
            ] / (total_time / 60)

        if successful_tests:
            avg_duration = sum(t["duration"] for t in successful_tests) / len(
                successful_tests
            )
            self.performance_metrics["average_response_time"] = avg_duration

        total_tests = len(self.test_results)
        failed_tests = len([t for t in self.test_results if not t.get("success", True)])
        if total_tests > 0:
            self.performance_metrics["error_rate"] = failed_tests / total_tests

    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive test report"""
        self.calculate_performance()

        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total_tests": len(self.test_results),
                "successful_tests": len(
                    [t for t in self.test_results if t.get("success", False)]
                ),
                "failed_tests": len(
                    [t for t in self.test_results if not t.get("success", True)]
                ),
                "total_duration": time.time() - self.start_time,
                "total_cost": self.cost_tracking["total_cost"],
            },
            "cost_tracking": self.cost_tracking,
            "performance_metrics": self.performance_metrics,
            "test_results": self.test_results,
        }

        return report

    def save_report(self, filename: str = None):
        """Save test report to file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"data/test_reports/real_e2e_report_{timestamp}.json"

        # Ensure directory exists
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        report = self.generate_report()

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"📊 Test report saved: {filename}")
        return filename

    def print_summary(self):
        """Print test summary"""
        report = self.generate_report()
        summary = report["summary"]

        print("\n" + "=" * 60)
        print("📊 REAL E2E TEST SUMMARY")
        print("=" * 60)

        print(f"Total Tests: {summary['total_tests']}")
        print(f"Successful: {summary['successful_tests']}")
        print(f"Failed: {summary['failed_tests']}")
        print(f"Duration: {summary['total_duration']:.2f} seconds")
        print(f"Total Cost: ${summary['total_cost']:.4f}")

        print("\nPerformance:")
        print(f"  Papers/Minute: {self.performance_metrics['papers_per_minute']:.2f}")
        print(
            f"  Avg Response Time: {self.performance_metrics['average_response_time']:.2f}s"
        )
        print(f"  Error Rate: {self.performance_metrics['error_rate']:.2%}")

        print("\nCost Breakdown:")
        for model, usage in self.cost_tracking["model_usage"].items():
            print(f"  {model}:")
            print(f"    Calls: {usage['calls']}")
            print(f"    Tokens: {usage['tokens']:,}")
            print(f"    Cost: ${usage['cost']:.4f}")

        print("=" * 60)


def monitor_real_e2e_test(test_func):
    """Decorator to monitor real E2E tests"""

    def wrapper(*args, **kwargs):
        monitor = RealE2ETestMonitor()

        # Extract test info
        test_name = test_func.__name__
        expected_cost = kwargs.get("expected_cost", 0.0)

        # Start monitoring
        monitor.start_test(test_name, expected_cost)

        try:
            # Run test
            result = test_func(*args, **kwargs)

            # End monitoring
            monitor.end_test(test_name, True, expected_cost)

            return result

        except Exception as e:
            # End monitoring with failure
            monitor.end_test(test_name, False, expected_cost)
            print(f"❌ Test failed: {e}")
            raise

        finally:
            # Print summary
            monitor.print_summary()
            monitor.save_report()

    return wrapper


# Example usage
if __name__ == "__main__":
    # Test the monitor
    monitor = RealE2ETestMonitor()

    # Simulate some tests
    monitor.start_test("test_harvest", 0.0)
    time.sleep(1)
    monitor.end_test("test_harvest", True, 0.0)

    monitor.start_test("test_translation", 0.0013)
    monitor.track_api_call("deepseek/deepseek-v3.2-exp", 2000, 0.0013)
    time.sleep(2)
    monitor.end_test("test_translation", True, 0.0013)

    monitor.start_test("test_render", 0.0)
    time.sleep(0.5)
    monitor.end_test("test_render", True, 0.0)

    # Print summary
    monitor.print_summary()
    monitor.save_report()
