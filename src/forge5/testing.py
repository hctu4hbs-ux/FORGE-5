"""Unittest discovery that rejects empty and entirely skipped suites."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, cast

from forge5.models import ForgeError


class CountingResult(unittest.TextTestResult):
    """Count started cases without subtracting fixture/subtest skip markers."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.executed = 0
        self.active: unittest.TestCase | None = None

    def startTest(self, test: unittest.TestCase) -> None:
        super().startTest(test)
        self.active = test
        self.executed += 1

    def stopTest(self, test: unittest.TestCase) -> None:
        super().stopTest(test)
        self.active = None

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        if test is self.active:
            self.executed -= 1


def run_tests(start: Path, pattern: str = "test*.py") -> dict[str, Any]:
    if not start.is_dir():
        raise ForgeError(f"Test discovery directory does not exist: {start}")
    suite = unittest.TestLoader().discover(str(start), pattern=pattern)
    discovered = suite.countTestCases()
    result = cast(
        CountingResult, unittest.TextTestRunner(verbosity=2, resultclass=CountingResult).run(suite)
    )
    executed = result.executed
    return {
        "discovered": discovered,
        "executed": executed,
        "skipped": len(result.skipped),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "passed": discovered > 0 and executed > 0 and result.wasSuccessful(),
    }
