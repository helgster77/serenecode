"""Filesystem context tests for scoped traceability checks."""

from dataclasses import replace
from pathlib import Path

from serenecode.adapters.local_fs import LocalFileReader
from serenecode.adapters.traceability_context import discover_traceability_sources
from serenecode.source_discovery import build_source_files


def test_context_includes_siblings_but_preserves_selected_source_snapshot(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    src = tmp_path / "src"
    src.mkdir()
    selected = src / "selected.py"
    selected.write_text('"""On disk."""')
    (src / "sibling.py").write_text('"""Sibling implementation."""')
    reader = LocalFileReader()
    sources = build_source_files([str(selected)], reader, str(tmp_path))
    snapshot = replace(sources[0], source='"""Edited snapshot."""')
    context = discover_traceability_sources((snapshot,), reader)
    assert len(context) == 2
    assert next(sf for sf in context if sf.file_path == str(selected)).source == snapshot.source
    assert any(sf.file_path.endswith("sibling.py") for sf in context)


def test_flat_context_excludes_test_sources(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("")
    selected = tmp_path / "module.py"
    selected.write_text('"""Implementation."""')
    (tmp_path / "test_module.py").write_text("def test_module():\n    assert True\n")
    reader = LocalFileReader()
    sources = build_source_files([str(selected)], reader, str(tmp_path))
    assert discover_traceability_sources(sources, reader) == sources
