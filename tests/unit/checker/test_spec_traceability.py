"""Tests for the spec traceability checker."""

from __future__ import annotations

from serenecode.checker.spec_traceability import (
    check_spec_traceability,
    extract_implementations,
    extract_integration_points,
    extract_spec_requirements,
    extract_verifications,
    validate_spec,
)
from serenecode.core.pipeline import SourceFile
from serenecode.models import CheckStatus


class TestExtractSpecRequirements:
    """Tests for extracting REQ-xxx identifiers from spec content."""

    def test_finds_all_requirements(self) -> None:
        spec = "### REQ-001: Auth\n### REQ-002: Sessions\n### REQ-003: Logging"
        reqs = extract_spec_requirements(spec)
        assert reqs == frozenset({"REQ-001", "REQ-002", "REQ-003"})

    def test_empty_spec_returns_empty(self) -> None:
        assert extract_spec_requirements("") == frozenset()
        assert extract_spec_requirements("No requirements here.") == frozenset()

    def test_deduplicates(self) -> None:
        spec = "REQ-001 mentioned here and REQ-001 again."
        reqs = extract_spec_requirements(spec)
        assert reqs == frozenset({"REQ-001"})

    def test_handles_four_digit_ids(self) -> None:
        spec = "### REQ-1234: Large project"
        reqs = extract_spec_requirements(spec)
        assert reqs == frozenset({"REQ-1234"})


class TestExtractImplementations:
    """Tests for extracting Implements: tags from source code."""

    def test_finds_implements_tag(self) -> None:
        source = '''
def authenticate(email: str) -> bool:
    """Authenticate user.

    Implements: REQ-001
    """
    return True
'''
        impls = extract_implementations(source)
        assert len(impls) == 1
        assert impls[0] == ("authenticate", "REQ-001", 2)

    def test_finds_multiple_reqs_in_one_function(self) -> None:
        source = '''
def process(data: str) -> str:
    """Process data.

    Implements: REQ-001, REQ-002
    """
    return data
'''
        impls = extract_implementations(source)
        assert len(impls) == 2
        req_ids = {r[1] for r in impls}
        assert req_ids == {"REQ-001", "REQ-002"}

    def test_ignores_non_docstring_comments(self) -> None:
        source = '''
# Implements: REQ-001
def func() -> None:
    """No implements tag here."""
    pass
'''
        impls = extract_implementations(source)
        assert len(impls) == 0

    def test_handles_syntax_error(self) -> None:
        assert extract_implementations("def broken(") == []

    def test_no_docstring_returns_empty(self) -> None:
        source = "def func() -> None:\n    pass\n"
        assert extract_implementations(source) == []


class TestExtractVerifications:
    """Tests for extracting Verifies: tags from test code."""

    def test_finds_verifies_tag(self) -> None:
        source = '''
def test_auth():
    """Test authentication.

    Verifies: REQ-001
    """
    assert True
'''
        verifs = extract_verifications(source)
        assert len(verifs) == 1
        assert verifs[0] == ("test_auth", "REQ-001", 2)

    def test_finds_multiple_reqs(self) -> None:
        source = '''
def test_combined():
    """Test both features.

    Verifies: REQ-001, REQ-002
    """
    assert True
'''
        verifs = extract_verifications(source)
        assert len(verifs) == 2


class TestCheckSpecTraceability:
    """Tests for the full spec traceability check."""

    def _make_source(self, name: str, content: str) -> SourceFile:
        return SourceFile(
            file_path=f"src/pkg/{name}.py",
            module_path=f"pkg/{name}.py",
            source=content,
        )

    def test_all_covered_passes(self) -> None:
        spec = "### REQ-001: Feature A"
        source = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source='''
def feature_a() -> None:
    """Do feature A.

    Implements: REQ-001
    """
    pass
''',
        )
        test_sources = (
            ("tests/test_mod.py", '''
def test_feature_a():
    """Test feature A.

    Verifies: REQ-001
    """
    pass
'''),
        )
        result = check_spec_traceability(spec, (source,), test_sources)
        assert result.passed is True

    def test_missing_implementation_fails(self) -> None:
        spec = "### REQ-001: Feature A\n### REQ-002: Feature B"
        source = self._make_source("mod", '''
def feature_a() -> None:
    """Do feature A.

    Implements: REQ-001
    """
    pass
''')
        test_sources = (
            ("tests/test_mod.py", '''
def test_a():
    """Verifies: REQ-001"""
    pass
def test_b():
    """Verifies: REQ-002"""
    pass
'''),
        )
        result = check_spec_traceability(spec, (source,), test_sources)
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any("missing_implementation" in d.finding_type for r in failed for d in r.details)

    def test_missing_verification_fails(self) -> None:
        spec = "### REQ-001: Feature A"
        source = self._make_source("mod", '''
def feature_a() -> None:
    """Implements: REQ-001"""
    pass
''')
        result = check_spec_traceability(spec, (source,), ())
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any("missing_verification" in d.finding_type for r in failed for d in r.details)

    def test_orphan_reference_fails(self) -> None:
        spec = "### REQ-001: Feature A"
        source = self._make_source("mod", '''
def feature_a() -> None:
    """Implements: REQ-001"""
    pass
def feature_b() -> None:
    """Implements: REQ-999"""
    pass
''')
        test_sources = (
            ("tests/test_mod.py", '''
def test_a():
    """Verifies: REQ-001"""
    pass
'''),
        )
        result = check_spec_traceability(spec, (source,), test_sources)
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any("orphan_reference" in d.finding_type for r in failed for d in r.details)

    def test_empty_spec_passes(self) -> None:
        source = self._make_source("mod", '"""Module."""\n')
        result = check_spec_traceability("No requirements.", (source,), ())
        assert result.passed is True


class TestValidateSpec:
    """Tests for spec validation."""

    def test_valid_spec_passes(self) -> None:
        spec = """\
# Project Spec

**Source:** none — synthetic fixture.

### REQ-001: Authentication
Users must authenticate with email and password.

### REQ-002: Session Management
Sessions expire after 30 minutes.

### REQ-003: Logging
All actions must be logged.
"""
        result = validate_spec(spec)
        assert result.passed is True
        passed = [r for r in result.results if r.status == CheckStatus.PASSED]
        assert any("3 requirements" in d.message for r in passed for d in r.details)

    def test_no_requirements_fails(self) -> None:
        result = validate_spec("This spec has no requirements.")
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any("no_requirements" in d.finding_type for r in failed for d in r.details)

    def test_duplicate_req_fails(self) -> None:
        spec = """\
**Source:** none — synthetic fixture.

### REQ-001: First
Description.

### REQ-001: Duplicate
Another description.
"""
        result = validate_spec(spec)
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any("duplicate_requirement" in d.finding_type for r in failed for d in r.details)

    def test_gap_in_sequence_fails(self) -> None:
        spec = """\
**Source:** none — synthetic fixture.

### REQ-001: First
Description.

### REQ-003: Third
Description.
"""
        result = validate_spec(spec)
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any("gap_in_sequence" in d.finding_type for r in failed for d in r.details)
        assert any("REQ-002" in d.message for r in failed for d in r.details)

    def test_missing_description_fails(self) -> None:
        spec = """\
**Source:** none — synthetic fixture.

### REQ-001
No description on the heading line.
"""
        result = validate_spec(spec)
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any("missing_description" in d.finding_type for r in failed for d in r.details)

    def test_single_req_passes(self) -> None:
        spec = (
            "**Source:** none — synthetic fixture.\n\n"
            "### REQ-001: The only requirement\nDetails here.\n"
        )
        result = validate_spec(spec)
        assert result.passed is True

    def test_missing_traceability_source_fails(self) -> None:
        spec = """\
### REQ-001: Only requirement
Description body.
"""
        result = validate_spec(spec)
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any(
            d.finding_type == "missing_traceability_source"
            for r in failed for d in r.details
        )

    def test_four_digit_ids_pass(self) -> None:
        spec = """\
**Source:** none — synthetic fixture.

### REQ-0001: First
Description.

### REQ-0002: Second
Description.
"""
        result = validate_spec(spec)
        assert result.passed is True


class TestExtractIntegrationPoints:
    """Tests for INT field parsing used by Level 6 integration semantics."""

    def test_strips_markdown_backticks_from_source_and_target(self) -> None:
        spec = """\
### REQ-001: Needed for INT Supports
Body.

### INT-001: Boundary
Kind: call
Source: `checkout.Service.checkout`
Target: `payments.Gateway.charge`
Supports: REQ-001
"""
        points = extract_integration_points(spec)
        assert len(points) == 1
        assert points[0].source == "checkout.Service.checkout"
        assert points[0].target == "payments.Gateway.charge"
