"""Minimal tests for checker.compositional_parsing module."""

from __future__ import annotations

from serenecode.checker.compositional_parsing import parse_module_info


def test_parse_module_info_is_callable() -> None:
    """parse_module_info exists and is callable."""
    assert callable(parse_module_info)


def test_all_imports_lists_nested_and_dynamic_imports() -> None:
    """Top-level lists stay as they were; all_imports sees the whole file.

    Verifies: REQ-069
    """
    source = (
        "import os\n"
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from app.adapters.db import Database\n"
        "\n"
        "try:\n"
        "    import fastjson\n"
        "except ImportError:\n"
        "    fastjson = None\n"
        "\n"
        "\n"
        "def load(name):\n"
        "    from app import adapters\n"
        "    import importlib\n"
        "    return importlib.import_module('app.adapters.cache'), __import__('plugins.extra'), __import__(name)\n"
    )

    info = parse_module_info(source, "app/core/engine.py", "app/core/engine.py")

    assert info.imports == ("os",)
    assert info.from_imports == (("typing", "TYPE_CHECKING"),)
    for target in (
        "os", "typing", "app.adapters.db", "app.adapters.db.Database", "fastjson",
        "app", "app.adapters", "importlib", "app.adapters.cache", "plugins.extra",
    ):
        assert target in info.all_imports, target
    assert len(set(info.all_imports)) == len(info.all_imports)


def test_function_info_records_the_precondition_waiver() -> None:
    """Verifies: REQ-065"""
    source = (
        "import icontract\n"
        "\n"
        "\n"
        "# no-precondition: every Order is valid by its own invariant\n"
        "@icontract.ensure(lambda order, result: result is order, 'identity')\n"
        "def keep(order):\n"
        "    return order\n"
        "\n"
        "\n"
        "def plain(order):\n"
        "    return order\n"
    )

    info = parse_module_info(source, "core/orders.py", "core/orders.py")

    waived = {function.name: function.precondition_waived for function in info.function_infos}
    assert waived == {"keep": True, "plain": False}
