"""Tests for shared contract predicates.

Tests both positive and negative cases for every predicate function
in the contracts.predicates module.
"""

from __future__ import annotations

import pytest

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


class TestIsNonEmptyString:
    """Tests for is_non_empty_string predicate."""

    @pytest.mark.parametrize(
        "value",
        ["hello", "a", " hello ", "  x  ", "multi\nline"],
    )
    def test_accepts_non_empty_strings(self, value: str) -> None:
        assert is_non_empty_string(value) is True

    @pytest.mark.parametrize(
        "value",
        ["", " ", "   ", "\t", "\n", "\t\n "],
    )
    def test_rejects_empty_or_whitespace_strings(self, value: str) -> None:
        assert is_non_empty_string(value) is False


class TestIsValidVerificationLevel:
    """Tests for is_valid_verification_level predicate."""

    @pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
    def test_accepts_valid_levels(self, level: int) -> None:
        assert is_valid_verification_level(level) is True

    @pytest.mark.parametrize("level", [0, -1, 7, 10, 100, -100])
    def test_rejects_invalid_levels(self, level: int) -> None:
        assert is_valid_verification_level(level) is False


class TestIsValidExitCode:
    """Tests for is_valid_exit_code predicate."""

    @pytest.mark.parametrize("code", [0, 1, 2, 3, 4, 5, 6, 10])
    def test_accepts_valid_exit_codes(self, code: int) -> None:
        assert is_valid_exit_code(code) is True

    @pytest.mark.parametrize("code", [-1, 7, 8, 9, 11, 100])
    def test_rejects_invalid_exit_codes(self, code: int) -> None:
        assert is_valid_exit_code(code) is False


class TestIsNonNegativeInt:
    """Tests for is_non_negative_int predicate."""

    @pytest.mark.parametrize("value", [0, 1, 42, 1000000])
    def test_accepts_non_negative(self, value: int) -> None:
        assert is_non_negative_int(value) is True

    @pytest.mark.parametrize("value", [-1, -42, -1000000])
    def test_rejects_negative(self, value: int) -> None:
        assert is_non_negative_int(value) is False


class TestIsPositiveInt:
    """Tests for is_positive_int predicate."""

    @pytest.mark.parametrize("value", [1, 2, 42, 1000000])
    def test_accepts_positive(self, value: int) -> None:
        assert is_positive_int(value) is True

    @pytest.mark.parametrize("value", [0, -1, -42])
    def test_rejects_non_positive(self, value: int) -> None:
        assert is_positive_int(value) is False


class TestIsValidFilePathString:
    """Tests for is_valid_file_path_string predicate."""

    @pytest.mark.parametrize(
        "value",
        ["file.py", "/abs/path.py", "relative/path", ".", "a"],
    )
    def test_accepts_valid_paths(self, value: str) -> None:
        assert is_valid_file_path_string(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "path\x00with_null",
            "../escape",
            "foo/../../etc/passwd",
            "..\\windows\\escape",
            "..",
        ],
    )
    def test_rejects_invalid_paths(self, value: str) -> None:
        assert is_valid_file_path_string(value) is False


class TestIsSnakeCase:
    """Tests for is_snake_case predicate."""

    @pytest.mark.parametrize(
        "name",
        ["hello", "hello_world", "my_func", "a", "x1", "get_value2"],
    )
    def test_accepts_snake_case(self, name: str) -> None:
        assert is_snake_case(name) is True

    @pytest.mark.parametrize(
        "name",
        ["Hello", "helloWorld", "HELLO", "hello_", "_hello", "hello__world", "1hello", ""],
    )
    def test_rejects_non_snake_case(self, name: str) -> None:
        assert is_snake_case(name) is False


class TestIsPascalCase:
    """Tests for is_pascal_case predicate."""

    @pytest.mark.parametrize(
        "name",
        ["Hello", "HelloWorld", "MyClass", "A", "X1", "HTTPError"],
    )
    def test_accepts_pascal_case(self, name: str) -> None:
        assert is_pascal_case(name) is True

    @pytest.mark.parametrize(
        "name",
        ["hello", "helloWorld", "hello_world", "HELLO_WORLD", "1Hello", ""],
    )
    def test_rejects_non_pascal_case(self, name: str) -> None:
        assert is_pascal_case(name) is False


class TestIsUpperSnakeCase:
    """Tests for is_upper_snake_case predicate."""

    @pytest.mark.parametrize(
        "name",
        ["HELLO", "HELLO_WORLD", "MY_CONST", "A", "X1", "MAX_VALUE_2"],
    )
    def test_accepts_upper_snake_case(self, name: str) -> None:
        assert is_upper_snake_case(name) is True

    @pytest.mark.parametrize(
        "name",
        ["hello", "Hello", "hello_world", "HELLO_", "_HELLO", "1HELLO", ""],
    )
    def test_rejects_non_upper_snake_case(self, name: str) -> None:
        assert is_upper_snake_case(name) is False


class TestIsValidTemplateName:
    """Tests for is_valid_template_name predicate."""

    @pytest.mark.parametrize("name", ["default", "strict", "minimal"])
    def test_accepts_valid_templates(self, name: str) -> None:
        assert is_valid_template_name(name) is True

    @pytest.mark.parametrize("name", ["", "custom", "DEFAULT", "Strict"])
    def test_rejects_invalid_templates(self, name: str) -> None:
        assert is_valid_template_name(name) is False
