"""End-to-end tests for the CLI entry point.

Re-exports tests from test_check_command.py under the conventional name
so L1 test-existence checks find them for cli.py.
"""

from tests.e2e.test_check_command import *  # noqa: F401, F403
