"""Property-based tests for contract predicates using Hypothesis.

Every predicate function is tested with @given to verify it behaves
correctly across a wide range of inputs. @example decorators capture
known edge cases as regression tests.
"""

from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from serenecode.contracts.predicates import (
    is_non_empty_string,
    is_non_negative_int,
    is_pascal_case,
    is_positive_int,
    is_snake_case,
    is_upper_snake_case,
    is_valid_exit_code,
    is_valid_file_path_string,
    is_valid_template_name,
    is_valid_verification_level,
)


class TestIsNonEmptyStringProperty:
    """Property-based tests for is_non_empty_string."""

    @given(value=st.text(min_size=1).filter(lambda s: s.strip()))
    @example(value="a")
    @example(value=" a ")
    @example(value="\ttab")
    def test_non_whitespace_strings_accepted(self, value: str) -> None:
        assert is_non_empty_string(value) is True

    @given(value=st.text().filter(lambda s: not s.strip()))
    @example(value="")
    @example(value="   ")
    @example(value="\n\t")
    def test_whitespace_only_strings_rejected(self, value: str) -> None:
        assert is_non_empty_string(value) is False

    @given(value=st.text())
    def test_return_is_always_bool(self, value: str) -> None:
        result = is_non_empty_string(value)
        assert isinstance(result, bool)

    @given(value=st.text())
    def test_equivalent_to_strip_check(self, value: str) -> None:
        assert is_non_empty_string(value) == (len(value.strip()) > 0)


class TestIsValidVerificationLevelProperty:
    """Property-based tests for is_valid_verification_level."""

    @given(level=st.integers(min_value=1, max_value=6))
    def test_valid_range_accepted(self, level: int) -> None:
        assert is_valid_verification_level(level) is True

    @given(level=st.integers().filter(lambda x: x < 1 or x > 6))
    @example(level=0)
    @example(level=7)
    @example(level=-1)
    @example(level=100)
    def test_out_of_range_rejected(self, level: int) -> None:
        assert is_valid_verification_level(level) is False


class TestIsValidExitCodeProperty:
    """Property-based tests for is_valid_exit_code."""

    @given(code=st.sampled_from([0, 1, 2, 3, 4, 5, 6, 10]))
    def test_valid_codes_accepted(self, code: int) -> None:
        assert is_valid_exit_code(code) is True

    @given(code=st.integers().filter(lambda x: x not in {0, 1, 2, 3, 4, 5, 6, 10}))
    @example(code=7)
    @example(code=9)
    @example(code=-1)
    def test_invalid_codes_rejected(self, code: int) -> None:
        assert is_valid_exit_code(code) is False


class TestIsNonNegativeIntProperty:
    """Property-based tests for is_non_negative_int."""

    @given(value=st.integers(min_value=0))
    @example(value=0)
    def test_non_negative_accepted(self, value: int) -> None:
        assert is_non_negative_int(value) is True

    @given(value=st.integers(max_value=-1))
    @example(value=-1)
    def test_negative_rejected(self, value: int) -> None:
        assert is_non_negative_int(value) is False


class TestIsPositiveIntProperty:
    """Property-based tests for is_positive_int."""

    @given(value=st.integers(min_value=1))
    def test_positive_accepted(self, value: int) -> None:
        assert is_positive_int(value) is True

    @given(value=st.integers(max_value=0))
    @example(value=0)
    def test_non_positive_rejected(self, value: int) -> None:
        assert is_positive_int(value) is False


class TestIsValidFilePathStringProperty:
    """Property-based tests for is_valid_file_path_string."""

    @given(value=st.text(min_size=1).filter(
        lambda s: "\x00" not in s and ".." not in s.replace("\\", "/").split("/"),
    ))
    def test_safe_non_empty_accepted(self, value: str) -> None:
        assert is_valid_file_path_string(value) is True

    @given(prefix=st.text(min_size=1), suffix=st.text())
    def test_null_byte_rejected(self, prefix: str, suffix: str) -> None:
        value = prefix + "\x00" + suffix
        assert is_valid_file_path_string(value) is False

    def test_empty_rejected(self) -> None:
        assert is_valid_file_path_string("") is False

    @given(prefix=st.text(), suffix=st.text())
    @example(prefix="", suffix="etc/passwd")
    @example(prefix="foo/bar", suffix="baz")
    def test_path_traversal_rejected(self, prefix: str, suffix: str) -> None:
        sep = "/" if prefix else ""
        value = f"{prefix}{sep}../{suffix}" if prefix else f"../{suffix}"
        assert is_valid_file_path_string(value) is False


class TestIsSnakeCaseProperty:
    """Property-based tests for is_snake_case."""

    @given(parts=st.lists(
        st.from_regex(r"[a-z][a-z0-9]*", fullmatch=True),
        min_size=1, max_size=4,
    ))
    def test_valid_snake_case_accepted(self, parts: list[str]) -> None:
        name = "_".join(parts)
        assert is_snake_case(name) is True

    @given(name=st.from_regex(r"[A-Z][a-zA-Z]*", fullmatch=True))
    def test_pascal_case_rejected(self, name: str) -> None:
        assert is_snake_case(name) is False


class TestIsPascalCaseProperty:
    """Property-based tests for is_pascal_case."""

    @given(name=st.from_regex(r"[A-Z][a-zA-Z0-9]*", fullmatch=True))
    def test_valid_pascal_case_accepted(self, name: str) -> None:
        assert is_pascal_case(name) is True

    @given(name=st.from_regex(r"[a-z][a-z_]*", fullmatch=True))
    def test_snake_case_rejected(self, name: str) -> None:
        assert is_pascal_case(name) is False


class TestIsUpperSnakeCaseProperty:
    """Property-based tests for is_upper_snake_case."""

    @given(parts=st.lists(
        st.from_regex(r"[A-Z][A-Z0-9]*", fullmatch=True),
        min_size=1, max_size=4,
    ))
    def test_valid_upper_snake_accepted(self, parts: list[str]) -> None:
        name = "_".join(parts)
        assert is_upper_snake_case(name) is True


class TestIsValidTemplateNameProperty:
    """Property-based tests for is_valid_template_name."""

    @given(name=st.sampled_from(["default", "strict", "minimal"]))
    def test_valid_names_accepted(self, name: str) -> None:
        assert is_valid_template_name(name) is True

    @given(name=st.text().filter(lambda s: s not in ("default", "strict", "minimal")))
    @example(name="DEFAULT")
    @example(name="custom")
    @example(name="")
    def test_invalid_names_rejected(self, name: str) -> None:
        assert is_valid_template_name(name) is False
