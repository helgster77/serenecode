"""Coverage-gap tests for private spec_traceability helpers.

These tests exercise the INT-field validation helpers and orphan-reference
finding builder directly, using small in-memory spec fixtures.
"""

from __future__ import annotations

from serenecode.checker.spec_traceability import (
    _integration_field_failure,
    _orphan_reference_findings,
    _validate_kind_field,
    _validate_required_fields,
    _validate_supports_field,
)
from serenecode.models import CheckStatus


class TestOrphanReferenceFindings:
    """Tests for _orphan_reference_findings."""

    def test_orphan_with_implementation_location_produces_finding(self) -> None:
        orphans = {"REQ-099"}
        implemented = {"REQ-099": [("src/auth.py", "authenticate", 12)]}
        results = _orphan_reference_findings(orphans, implemented, {})
        assert len(results) == 1
        assert results[0].function == "REQ-099"
        assert results[0].file == "src/auth.py"
        assert results[0].line == 12
        assert results[0].status == CheckStatus.FAILED
        assert results[0].details[0].finding_type == "orphan_reference"

    def test_orphan_without_any_location_is_skipped(self) -> None:
        orphans = {"REQ-777"}
        results = _orphan_reference_findings(orphans, {}, {})
        assert results == []

    def test_mixed_orphans_only_located_ones_reported(self) -> None:
        orphans = {"REQ-001", "REQ-002"}
        verified = {"REQ-002": [("tests/test_auth.py", "test_login", 5)]}
        results = _orphan_reference_findings(orphans, {}, verified)
        assert len(results) == 1
        assert results[0].function == "REQ-002"
        assert results[0].file == "tests/test_auth.py"
        assert results[0].line == 5

    def test_empty_orphans_yields_no_findings(self) -> None:
        assert _orphan_reference_findings(set(), {}, {}) == []


class TestValidateRequiredFields:
    """Tests for _validate_required_fields."""

    def test_all_fields_present_yields_no_findings(self) -> None:
        fields = {
            "Kind": ("call", 2),
            "Source": ("Component.function", 3),
            "Target": ("Dependency.function", 4),
        }
        assert _validate_required_fields("INT-001", 1, fields) == []

    def test_all_fields_missing_yields_three_findings(self) -> None:
        results = _validate_required_fields("INT-001", 10, {})
        assert len(results) == 3
        messages = [r.details[0].message for r in results]
        assert any("'Kind'" in m for m in messages)
        assert any("'Source'" in m for m in messages)
        assert any("'Target'" in m for m in messages)
        assert all(r.line == 10 for r in results)
        assert all(r.status == CheckStatus.FAILED for r in results)

    def test_missing_kind_only(self) -> None:
        fields = {
            "Source": ("A.f", 3),
            "Target": ("B.g", 4),
        }
        results = _validate_required_fields("INT-002", 1, fields)
        assert len(results) == 1
        assert "'Kind'" in results[0].details[0].message

    def test_missing_source_only(self) -> None:
        fields = {
            "Kind": ("call", 2),
            "Target": ("B.g", 4),
        }
        results = _validate_required_fields("INT-003", 1, fields)
        assert len(results) == 1
        assert "'Source'" in results[0].details[0].message

    def test_missing_target_only(self) -> None:
        fields = {
            "Kind": ("call", 2),
            "Source": ("A.f", 3),
        }
        results = _validate_required_fields("INT-004", 1, fields)
        assert len(results) == 1
        assert "'Target'" in results[0].details[0].message


class TestValidateKindField:
    """Tests for _validate_kind_field."""

    def test_absent_kind_yields_no_findings(self) -> None:
        assert _validate_kind_field("INT-001", {}) == []

    def test_supported_kind_call_yields_no_findings(self) -> None:
        assert _validate_kind_field("INT-001", {"Kind": ("call", 2)}) == []

    def test_supported_kind_implements_case_insensitive(self) -> None:
        assert _validate_kind_field("INT-001", {"Kind": ("Implements", 2)}) == []

    def test_unsupported_kind_yields_failure(self) -> None:
        results = _validate_kind_field("INT-005", {"Kind": ("teleport", 7)})
        assert len(results) == 1
        assert results[0].function == "INT-005"
        assert results[0].file == "SPEC.md"
        assert results[0].line == 7
        assert results[0].status == CheckStatus.FAILED
        detail = results[0].details[0]
        assert detail.finding_type == "unsupported_integration_kind"
        assert "teleport" in detail.message
        assert "call" in detail.suggestion
        assert "implements" in detail.suggestion


class TestValidateSupportsField:
    """Tests for _validate_supports_field."""

    def test_absent_supports_yields_no_findings(self) -> None:
        assert _validate_supports_field("INT-001", {}, frozenset()) == []

    def test_malformed_supports_yields_invalid_reference(self) -> None:
        fields = {"Supports": ("not-a-req-id", 5)}
        results = _validate_supports_field("INT-001", fields, frozenset({"REQ-001"}))
        assert len(results) == 1
        assert results[0].line == 5
        detail = results[0].details[0]
        assert detail.finding_type == "invalid_support_reference"
        assert "invalid Supports field" in detail.message

    def test_empty_supports_value_is_malformed(self) -> None:
        fields = {"Supports": ("   ", 6)}
        results = _validate_supports_field("INT-001", fields, frozenset())
        assert len(results) == 1
        assert results[0].details[0].finding_type == "invalid_support_reference"

    def test_all_supports_declared_yields_no_findings(self) -> None:
        fields = {"Supports": ("REQ-001, REQ-002", 4)}
        declared = frozenset({"REQ-001", "REQ-002"})
        assert _validate_supports_field("INT-002", fields, declared) == []

    def test_undeclared_req_in_supports_yields_failure(self) -> None:
        fields = {"Supports": ("REQ-001, REQ-999", 4)}
        declared = frozenset({"REQ-001"})
        results = _validate_supports_field("INT-003", fields, declared)
        assert len(results) == 1
        detail = results[0].details[0]
        assert detail.finding_type == "invalid_support_reference"
        assert "REQ-999" in detail.message
        assert "REQ-999" in detail.suggestion


class TestIntegrationFieldFailure:
    """Tests for _integration_field_failure."""

    def test_builds_standardized_missing_field_failure(self) -> None:
        result = _integration_field_failure(
            "INT-007", 42, "Kind", "Add 'Kind: call' below the heading.",
        )
        assert result.function == "INT-007"
        assert result.file == "SPEC.md"
        assert result.line == 42
        assert result.level_requested == 1
        assert result.level_achieved == 0
        assert result.status == CheckStatus.FAILED
        detail = result.details[0]
        assert detail.finding_type == "missing_integration_field"
        assert "'Kind'" in detail.message
        assert detail.suggestion == "Add 'Kind: call' below the heading."
