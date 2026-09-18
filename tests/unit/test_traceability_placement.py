"""Tests for detection of traceability tags written where nothing reads them."""

from __future__ import annotations

import textwrap

from serenecode.checker.spec_traceability import (
    extract_implementations,
    extract_verifications,
)
from serenecode.checker.traceability_placement import (
    _tag_line,
    check_traceability_tag_placement,
)
from serenecode.models import CheckStatus


def _findings(*sources: tuple[str, str]) -> list[str]:
    """Return misplaced-tag messages for the given files."""
    results = check_traceability_tag_placement(
        tuple((path, textwrap.dedent(source)) for path, source in sources)
    )
    return [detail.message for result in results for detail in result.details]


def test_implements_in_a_module_docstring_is_reported() -> None:
    """The tag is named, not just its consequence.

    Verifies: REQ-052
    """
    messages = _findings(("src/ops.py", '''
        """Math operations.

        Implements: REQ-001
        """
    '''))
    assert len(messages) == 1
    assert "Implements: REQ-001" in messages[0]
    assert "module docstring" in messages[0]


def test_verifies_in_a_module_docstring_is_reported() -> None:
    """Test files are scanned on the same terms.

    Verifies: REQ-052
    """
    messages = _findings(("tests/test_ops.py", '''
        """Tests for math operations.

        Verifies: REQ-001
        """
    '''))
    assert len(messages) == 1
    assert "Verifies: REQ-001" in messages[0]


def test_every_listed_identifier_is_named() -> None:
    """A multi-identifier tag reports all of them.

    Verifies: REQ-052
    """
    messages = _findings(("src/ops.py", '''
        """Math operations.

        Implements: REQ-001, REQ-002, INT-001
        """
    '''))
    assert "REQ-001, REQ-002, INT-001" in messages[0]


def test_both_tag_kinds_in_one_module_are_reported_separately() -> None:
    """One finding per misplaced tag kind.

    Verifies: REQ-052
    """
    messages = _findings(("src/ops.py", '''
        """Math operations.

        Implements: REQ-001
        Verifies: REQ-002
        """
    '''))
    assert len(messages) == 2


def test_the_finding_locates_the_tag_line() -> None:
    """The reported line is the tag, not line 1.

    Verifies: REQ-052
    """
    results = check_traceability_tag_placement((("src/ops.py", textwrap.dedent('''
        """Math operations.

        A longer description that pushes the tag down the docstring.

        Implements: REQ-001
        """
    ''')),))
    assert results[0].line == 6
    assert results[0].status == CheckStatus.FAILED


def test_tags_on_functions_and_classes_are_not_reported() -> None:
    """The supported placement stays silent.

    Verifies: REQ-052
    """
    assert _findings(("src/ops.py", '''
        """Math operations."""


        def double(value: int) -> int:
            """Double a value.

            Implements: REQ-001
            """
            return value * 2


        class Doubler:
            """Doubles values.

            Implements: REQ-002
            """
    ''')) == []


def test_a_module_level_index_under_another_heading_is_not_a_tag() -> None:
    """The documented way to keep a module-level index stays silent.

    Verifies: REQ-052
    """
    assert _findings(("src/ops.py", '''
        """Math operations.

        Requirements implemented here, each tagged on its own symbol below:
        REQ-001, REQ-002
        """
    ''')) == []


def test_files_without_a_module_docstring_are_skipped() -> None:
    """No docstring, nothing to misplace.

    Verifies: REQ-052
    """
    assert _findings(("src/ops.py", "value = 1\n")) == []


def test_unparseable_sources_are_skipped() -> None:
    """A file that does not compile yields no placement findings.

    Verifies: REQ-052
    """
    assert _findings(("src/broken.py", "def (:\n")) == []


def test_empty_sources_are_skipped() -> None:
    """An empty file has no docstring.

    Verifies: REQ-052
    """
    assert _findings(("src/empty.py", "   \n")) == []


class TestCommaSeparatedIdentifiers:
    """One tag may carry several identifiers."""

    def test_implements_records_each_identifier(self) -> None:
        """A comma-separated list is expanded, not treated as one id.

        Verifies: REQ-053
        """
        refs = extract_implementations(textwrap.dedent('''
            def checkout(cart: object) -> None:
                """Submit payment.

                Implements: REQ-003, INT-001
                """
        '''))
        assert {identifier for _, identifier, _ in refs} == {"REQ-003", "INT-001"}

    def test_verifies_records_each_identifier(self) -> None:
        """The same holds for tests.

        Verifies: REQ-053
        """
        refs = extract_verifications(textwrap.dedent('''
            def test_checkout() -> None:
                """Verify checkout.

                Verifies: REQ-005, REQ-006, INT-002
                """
        '''))
        assert {identifier for _, identifier, _ in refs} == {
            "REQ-005", "REQ-006", "INT-002",
        }


class TestTagLineFallback:
    """The reported line degrades gracefully."""

    def test_line_is_found_when_the_marker_is_literal(self) -> None:
        """The tag's own line is reported.

        Verifies: REQ-052
        """
        assert _tag_line('"""Doc.\n\nImplements: REQ-001\n"""\n', "Implements") == 3

    def test_line_falls_back_to_one_when_the_marker_is_not_literal(self) -> None:
        """A docstring assembled from parts still yields a usable location.

        Verifies: REQ-052
        """
        assert _tag_line('"""Doc."""\n', "Implements") == 1
