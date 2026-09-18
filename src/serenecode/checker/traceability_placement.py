"""Detection of traceability tags written where nothing reads them.

Traceability tags are read from function and class docstrings. A tag written
in a *module* docstring is inert: it looks like a traceability record, reads
like one in review, and contributes nothing. Without this check the only
symptom is the requirement being reported as having no implementation and no
test, which does not hint that the tag exists a few lines away in the wrong
docstring.

This is a core module — no I/O operations are permitted. Source code is
received as strings, not read from files.
"""

from __future__ import annotations

import ast
import re

import icontract

from serenecode.checker.spec_traceability import (
    _IMPLEMENTS_PATTERN,
    _SPEC_ITEM_PATTERN,
    _VERIFIES_PATTERN,
)
from serenecode.contracts.predicates import is_non_empty_string
from serenecode.models import (
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
)


# The tag kinds, paired with the pattern that finds them and the docstring
# kind each one belongs on.
_TAG_KINDS: tuple[tuple[str, "re.Pattern[str]", str], ...] = (
    ("Implements", _IMPLEMENTS_PATTERN, "the implementing function or class"),
    ("Verifies", _VERIFIES_PATTERN, "the test function or class"),
)


@icontract.require(lambda sources: isinstance(sources, tuple), "sources must be a tuple")
@icontract.require(
    lambda sources: all(
        isinstance(entry, tuple)
        and len(entry) == 2
        and is_non_empty_string(entry[0])
        and isinstance(entry[1], str)
        for entry in sources
    ),
    "each source must be a (non-empty file path, source text) pair",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def check_traceability_tag_placement(
    sources: tuple[tuple[str, str], ...],
) -> list[FunctionResult]:
    """Report traceability tags found in module docstrings.

    Implements: REQ-052

    Args:
        sources: Tuple of (file_path, source) pairs to scan.

    Returns:
        List of FunctionResult, one per misplaced tag.
    """
    results: list[FunctionResult] = []

    # Loop invariant: results holds misplaced-tag findings for sources[0..i]
    for file_path, source in sources:
        results.extend(_findings_for_source(file_path, source))

    return results


@icontract.require(lambda file_path: is_non_empty_string(file_path), "file_path must be non-empty")
@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _findings_for_source(file_path: str, source: str) -> list[FunctionResult]:
    """Report misplaced tags in one file's module docstring."""
    docstring = _module_docstring(source)
    if docstring is None:
        return []

    results: list[FunctionResult] = []
    # Loop invariant: results holds findings for tag kinds[0..i]
    for label, pattern, owner in _TAG_KINDS:
        match = pattern.search(docstring)
        if match is None:
            continue
        identifiers = _SPEC_ITEM_PATTERN.findall(match.group(0))
        if not identifiers:
            continue
        results.append(_misplaced_tag_finding(
            file_path, source, label, owner, tuple(identifiers),
        ))

    return results


@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _module_docstring(source: str) -> str | None:
    """Return a module's docstring, or None when there is none."""
    if not source.strip():
        return None
    # silent-except: placement checking is best-effort; an unparseable file is reported by the structural checks instead
    try:
        return ast.get_docstring(ast.parse(source))
    except (SyntaxError, TypeError, ValueError):
        return None


@icontract.require(lambda file_path: is_non_empty_string(file_path), "file_path must be non-empty")
@icontract.require(lambda label: is_non_empty_string(label), "label must be non-empty")
@icontract.require(lambda owner: is_non_empty_string(owner), "owner must be non-empty")
@icontract.require(
    lambda identifiers: isinstance(identifiers, tuple) and len(identifiers) > 0,
    "identifiers must be a non-empty tuple",
)
@icontract.ensure(
    lambda result: result.status == CheckStatus.FAILED,
    "a misplaced tag is a failure",
)
def _misplaced_tag_finding(
    file_path: str,
    source: str,
    label: str,
    owner: str,
    identifiers: tuple[str, ...],
) -> FunctionResult:
    """Build the finding for one misplaced tag."""
    identifier_list = ", ".join(identifiers)
    return FunctionResult(
        function="<module>",
        file=file_path,
        line=_tag_line(source, label),
        level_requested=1,
        level_achieved=0,
        status=CheckStatus.FAILED,
        details=(Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="spec_traceability",
            finding_type="misplaced_traceability_tag",
            message=(
                f"'{label}: {identifier_list}' is in the module docstring, "
                f"where traceability does not read it — tags are recognised "
                f"on function and class docstrings only, so this tag "
                f"contributes nothing"
            ),
            suggestion=(
                f"Move '{label}: {identifier_list}' to the docstring of "
                f"{owner}. To keep a module-level index, write it under a "
                f"different heading so it does not read as a tag."
            ),
        ),),
    )


@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.require(lambda label: is_non_empty_string(label), "label must be non-empty")
@icontract.ensure(lambda result: isinstance(result, int) and result >= 1, "result must be a line number")
def _tag_line(source: str, label: str) -> int:
    """Return the first line carrying the tag, or 1 when it cannot be located."""
    marker = label + ":"
    # Loop invariant: no line in [0..index) carries the tag marker
    for index, line in enumerate(source.splitlines(), start=1):
        if marker in line:
            return index
    return 1
