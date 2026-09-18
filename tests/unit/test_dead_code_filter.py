"""Tests for dead-code advisory suppression and path reconciliation."""

from __future__ import annotations

import textwrap

from serenecode.core.dead_code_filter import (
    is_suppressed_dead_code,
    normalize_path,
    registered_symbol_sites,
    resolve_report_path,
)


_ROUTES = textwrap.dedent('''
    from contextlib import asynccontextmanager

    app = make_app()


    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}


    @app.websocket("/ws")
    async def ws_handler() -> None:
        return None


    @asynccontextmanager
    async def lifespan(scope: object):
        yield None


    @staticmethod
    def orphan() -> None:
        return None


    def plain() -> None:
        return None
''')

_HIERARCHY = textwrap.dedent('''
    class Base:
        def on_event(self) -> None:
            return None

        def only_here(self) -> None:
            return None


    class Child(Base):
        def on_event(self) -> None:
            return None
''')


def _sites(*sources: tuple[str, str]) -> frozenset[tuple[str, str, int]]:
    return registered_symbol_sites(tuple(sources))


def test_call_form_decorator_suppresses_handler() -> None:
    """Route handlers registered by a decorator are suppressed.

    Verifies: REQ-045
    """
    sites = _sites(("routes.py", _ROUTES))
    assert is_suppressed_dead_code("routes.py", "health", 7, "function", sites)
    assert is_suppressed_dead_code("routes.py", "ws_handler", 12, "function", sites)


def test_bare_decorator_suppresses_context_manager() -> None:
    """@asynccontextmanager hands the function to the runtime too.

    Verifies: REQ-045
    """
    sites = _sites(("routes.py", _ROUTES))
    assert is_suppressed_dead_code("routes.py", "lifespan", 17, "function", sites)


def test_decorator_line_and_def_line_both_suppress() -> None:
    """Backends differ on which line they attribute to a decorated def.

    Verifies: REQ-045
    """
    sites = _sites(("routes.py", _ROUTES))
    assert ("routes.py", "health", 7) in sites
    assert ("routes.py", "health", 8) in sites


def test_inert_decorator_does_not_suppress() -> None:
    """A @staticmethod-only definition is still dead if nothing names it.

    Verifies: REQ-045
    """
    sites = _sites(("routes.py", _ROUTES))
    assert not is_suppressed_dead_code("routes.py", "orphan", 22, "function", sites)


def test_undecorated_function_does_not_suppress() -> None:
    """Plain definitions keep their advisory.

    Verifies: REQ-045
    """
    sites = _sites(("routes.py", _ROUTES))
    assert not is_suppressed_dead_code("routes.py", "plain", 26, "function", sites)


def test_override_of_in_project_base_is_suppressed() -> None:
    """A method overriding a resolvable base method is suppressed.

    Verifies: REQ-045
    """
    sites = _sites(("models.py", _HIERARCHY))
    assert is_suppressed_dead_code("models.py", "on_event", 11, "method", sites)


def test_base_declaration_is_not_suppressed() -> None:
    """The base declaration itself is not an override.

    Verifies: REQ-045
    """
    sites = _sites(("models.py", _HIERARCHY))
    assert not is_suppressed_dead_code("models.py", "on_event", 3, "method", sites)
    assert not is_suppressed_dead_code("models.py", "only_here", 6, "method", sites)


def test_base_class_resolves_across_files() -> None:
    """A Protocol or ABC in another module of the project resolves.

    Verifies: REQ-045
    """
    port = textwrap.dedent('''
        from typing import Protocol


        class Reader(Protocol):
            def read_file(self, path: str) -> str:
                ...
    ''')
    adapter = textwrap.dedent('''
        from ports import Reader


        class LocalReader(Reader):
            def read_file(self, path: str) -> str:
                return ""
    ''')
    sites = _sites(("ports.py", port), ("adapters.py", adapter))
    assert is_suppressed_dead_code("adapters.py", "read_file", 6, "method", sites)


def test_override_decorator_suppresses_unresolvable_base() -> None:
    """@override covers base classes outside the scanned sources.

    Verifies: REQ-045
    """
    source = textwrap.dedent('''
        from typing import override

        from framework import Middleware


        class Mine(Middleware):
            @override
            def dispatch(self) -> None:
                return None
    ''')
    sites = _sites(("mw.py", source))
    assert is_suppressed_dead_code("mw.py", "dispatch", 8, "method", sites)


def test_non_function_symbol_types_are_never_suppressed() -> None:
    """Variables, classes, imports and attributes keep their advisory.

    Verifies: REQ-045
    """
    sites = _sites(("routes.py", _ROUTES))
    assert not is_suppressed_dead_code("routes.py", "health", 7, "variable", sites)
    assert not is_suppressed_dead_code("routes.py", "health", 7, "class", sites)


def test_unparseable_source_contributes_no_sites() -> None:
    """A file that does not compile is skipped, not fatal.

    Verifies: REQ-045
    """
    assert _sites(("broken.py", "def (:\n")) == frozenset()


def test_cyclic_base_classes_terminate() -> None:
    """A malformed hierarchy cannot spin the base walk.

    Verifies: REQ-045
    """
    source = textwrap.dedent('''
        class A(B):
            def shared(self) -> None:
                return None


        class B(A):
            def shared(self) -> None:
                return None
    ''')
    sites = _sites(("cycle.py", source))
    assert is_suppressed_dead_code("cycle.py", "shared", 3, "method", sites)


def test_normalize_path_strips_dot_prefix_and_backslashes() -> None:
    """Path comparison is textual and separator-agnostic.

    Verifies: REQ-047
    """
    assert normalize_path("./src/a.py") == "src/a.py"
    assert normalize_path("src\\a.py") == "src/a.py"


def test_resolve_report_path_maps_absolute_back_to_caller_spelling() -> None:
    """Vulture absolutizes; the report uses the caller's spelling.

    Verifies: REQ-047
    """
    known = ("src/serenecode/models.py", "src/serenecode/cli.py")
    assert resolve_report_path(
        "/home/dev/project/src/serenecode/cli.py", known,
    ) == "src/serenecode/cli.py"


def test_resolve_report_path_keeps_exact_match() -> None:
    """An already-matching path is returned as given.

    Verifies: REQ-047
    """
    assert resolve_report_path("src/a.py", ("src/a.py",)) == "src/a.py"


def test_resolve_report_path_passes_unknown_paths_through() -> None:
    """A path matching nothing is reported unchanged.

    Verifies: REQ-047
    """
    assert resolve_report_path("/elsewhere/x.py", ("src/a.py",)) == "/elsewhere/x.py"


def test_resolve_report_path_ignores_empty_known_paths() -> None:
    """An empty entry is a suffix of everything and must not match.

    Verifies: REQ-047
    """
    assert resolve_report_path("/", ("",)) == "/"
    assert resolve_report_path("/a/b.py", ("", "a/b.py")) == "a/b.py"


def test_subscripted_base_class_resolves() -> None:
    """A generic base still names the class it inherits from.

    Verifies: REQ-045
    """
    source = textwrap.dedent('''
        from typing import Generic, TypeVar

        T = TypeVar("T")


        class Base(Generic[T]):
            def handle(self) -> None:
                return None


        class Child(Base[int]):
            def handle(self) -> None:
                return None
    ''')
    sites = _sites(("generic.py", source))
    assert is_suppressed_dead_code("generic.py", "handle", 13, "method", sites)


def test_dotted_base_class_resolves_by_final_segment() -> None:
    """`mod.Base` resolves to the class named `Base`.

    Verifies: REQ-045
    """
    base = textwrap.dedent('''
        class Base:
            def handle(self) -> None:
                return None
    ''')
    child = textwrap.dedent('''
        import mod


        class Child(mod.Base):
            def handle(self) -> None:
                return None
    ''')
    sites = _sites(("mod.py", base), ("child.py", child))
    assert is_suppressed_dead_code("child.py", "handle", 6, "method", sites)


def test_unnameable_base_expression_is_ignored() -> None:
    """A computed base contributes no inherited method names.

    Verifies: REQ-045
    """
    source = textwrap.dedent('''
        class Child(make_base()):
            def handle(self) -> None:
                return None
    ''')
    sites = _sites(("computed.py", source))
    assert not is_suppressed_dead_code("computed.py", "handle", 3, "method", sites)
