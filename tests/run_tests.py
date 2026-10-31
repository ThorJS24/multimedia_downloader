"""
Test runner.

    python tests/run_tests.py            # everything (browser + MCP-protocol tests included)
    python tests/run_tests.py --fast     # unit/API tier only (no Playwright, no spawned servers)
    python tests/run_tests.py -k queue   # only modules whose name contains 'queue'
"""
import argparse
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SLOW_PREFIXES = ("test_browser_", "test_mcp_protocol_audit")


def iter_suite(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_suite(item)
        else:
            yield item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="skip browser and spawned-server tests")
    ap.add_argument("-k", dest="keyword", default="", help="only modules containing this text")
    args = ap.parse_args()

    sys.path.insert(0, str(TESTS_DIR))
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for test in iter_suite(loader.discover(str(TESTS_DIR), pattern="test_*.py")):
        module = type(test).__module__.split(".")[-1]
        if args.fast and module.startswith(SLOW_PREFIXES):
            continue
        if args.keyword and args.keyword not in module:
            continue
        suite.addTest(test)

    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
