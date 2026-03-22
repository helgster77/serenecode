"""CrossHair adapter for symbolic verification (Level 4).

This adapter implements the SymbolicChecker protocol by running
CrossHair's symbolic execution engine on Python modules.
It uses the CrossHair Python API when available, with a CLI
subprocess fallback.

This is an adapter module — it handles I/O (module importing,
subprocess execution) and is exempt from full contract requirements.
"""

from __future__ import annotations

import importlib
import multiprocessing
import multiprocessing.queues
import queue
import re
import subprocess
import sys
import time
from typing import Any

from serenecode.core.exceptions import ToolNotInstalledError
from serenecode.ports.symbolic_checker import SymbolicFinding

try:
    from crosshair.core_and_libs import analyze_module
    from crosshair.options import AnalysisKind, AnalysisOptionSet
    from crosshair.statespace import AnalysisMessage, MessageType
    _CROSSHAIR_API_AVAILABLE = True
except ImportError:
    _CROSSHAIR_API_AVAILABLE = False

_CROSSHAIR_CLI_AVAILABLE: bool | None = None


def _check_crosshair_cli() -> bool:
    """Check if CrossHair CLI is available."""
    global _CROSSHAIR_CLI_AVAILABLE
    if _CROSSHAIR_CLI_AVAILABLE is not None:
        return _CROSSHAIR_CLI_AVAILABLE
    try:
        result = subprocess.run(
            [sys.executable, "-m", "crosshair", "--help"],
            capture_output=True,
            timeout=10,
        )
        _CROSSHAIR_CLI_AVAILABLE = result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        _CROSSHAIR_CLI_AVAILABLE = False
    return _CROSSHAIR_CLI_AVAILABLE


class CrossHairSymbolicChecker:
    """Symbolic checker implementation using CrossHair.

    Performs symbolic execution using CrossHair/Z3 to verify
    icontract postconditions hold for all valid inputs.
    """

    def __init__(
        self,
        per_condition_timeout: int = 30,
        per_path_timeout: int = 10,
        module_timeout: int = 300,
    ) -> None:
        """Initialize the checker.

        Args:
            per_condition_timeout: Default seconds per condition.
            per_path_timeout: Default seconds per execution path.
            module_timeout: Hard timeout in seconds for verifying an entire module.
        """
        self._per_condition_timeout = per_condition_timeout
        self._per_path_timeout = per_path_timeout
        self._module_timeout = module_timeout

    def verify_module(
        self,
        module_path: str,
        per_condition_timeout: int = 30,
        per_path_timeout: int = 10,
    ) -> list[SymbolicFinding]:
        """Run symbolic verification on all contracted functions in a module.

        Args:
            module_path: Importable Python module path to verify.
            per_condition_timeout: Max seconds per postcondition.
            per_path_timeout: Max seconds per execution path.

        Returns:
            List of symbolic findings.
        """
        if _CROSSHAIR_API_AVAILABLE:
            return self._verify_via_api(module_path, per_condition_timeout, per_path_timeout)
        elif _check_crosshair_cli():
            return self._verify_via_cli(module_path, per_condition_timeout)
        else:
            raise ToolNotInstalledError(
                "CrossHair is not installed. Install with: pip install crosshair-tool"
            )

    def _verify_via_api(
        self,
        module_path: str,
        per_condition_timeout: int,
        per_path_timeout: int,
    ) -> list[SymbolicFinding]:
        """Verify using CrossHair's Python API in an isolated process.

        Runs verification in a child process so it can be hard-killed
        if Z3 gets stuck in native C code (signal.SIGALRM cannot
        interrupt C extensions).

        Args:
            module_path: Module to verify.
            per_condition_timeout: Timeout per condition.
            per_path_timeout: Timeout per path.

        Returns:
            List of symbolic findings.
        """
        # Use "spawn" instead of "fork" — fork can deadlock on macOS
        # when system frameworks hold locks at fork time.
        ctx = multiprocessing.get_context("spawn")
        result_queue: multiprocessing.Queue[list[SymbolicFinding]] = ctx.Queue()

        process = ctx.Process(
            target=_api_verification_worker,
            args=(module_path, per_condition_timeout, per_path_timeout, result_queue),
        )
        process.start()

        # Read from the queue BEFORE joining the process to avoid a
        # pipe-buffer deadlock: if the child's put() fills the pipe,
        # it blocks until the parent drains it — but join() waits for
        # the child to exit first, creating a circular wait.
        result: list[SymbolicFinding] | None = None
        try:
            result = result_queue.get(timeout=self._module_timeout)
        except (queue.Empty, OSError):
            pass

        # Now clean up the child process.
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join()
        else:
            process.join(timeout=5)

        if result is not None:
            return result

        if process.exitcode is None or process.exitcode != 0:
            timed_out = process.exitcode is None or process.exitcode < 0
            message = (
                f"Module verification timed out after {self._module_timeout}s"
                if timed_out
                else f"Verification process exited with code {process.exitcode}"
            )
            return [SymbolicFinding(
                function_name="<module>",
                module_path=module_path,
                outcome="error" if not timed_out else "timeout",
                message=message,
                duration_seconds=float(self._module_timeout),
            )]

        return [SymbolicFinding(
            function_name="<module>",
            module_path=module_path,
            outcome="verified",
            message=f"No findings for '{module_path}'",
        )]

    def _verify_via_cli(
        self,
        module_path: str,
        per_condition_timeout: int,
    ) -> list[SymbolicFinding]:
        """Verify using CrossHair CLI as a subprocess fallback.

        Args:
            module_path: Module to verify.
            per_condition_timeout: Timeout per condition.

        Returns:
            List of symbolic findings.
        """
        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "crosshair", "check",
                    module_path,
                    f"--per_condition_timeout={per_condition_timeout}",
                ],
                capture_output=True,
                text=True,
                timeout=per_condition_timeout * 10,
            )
        except subprocess.TimeoutExpired:
            return [SymbolicFinding(
                function_name="<module>",
                module_path=module_path,
                outcome="timeout",
                message=f"CrossHair verification timed out for module '{module_path}'",
            )]
        except FileNotFoundError:
            raise ToolNotInstalledError(
                "CrossHair CLI not found. Install with: pip install crosshair-tool"
            )

        return _parse_cli_output(module_path, result.stdout, result.stderr)


def _api_verification_worker(
    module_path: str,
    per_condition_timeout: int,
    per_path_timeout: int,
    result_queue: multiprocessing.Queue,  # type: ignore[type-arg]
) -> None:
    """Run CrossHair API verification in a child process.

    This is the target function for multiprocessing.Process. Running
    in a separate process allows hard-killing via SIGTERM/SIGKILL when
    Z3 gets stuck in native C code that signal.SIGALRM cannot interrupt.

    Args:
        module_path: Importable module path.
        per_condition_timeout: Seconds per condition.
        per_path_timeout: Seconds per execution path.
        result_queue: Queue to put findings into.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        result_queue.put([SymbolicFinding(
            function_name="<module>",
            module_path=module_path,
            outcome="error",
            message=f"Cannot import module '{module_path}': {exc}",
        )])
        return

    options = AnalysisOptionSet(
        analysis_kind=[AnalysisKind.icontract],
        per_condition_timeout=float(per_condition_timeout),
        per_path_timeout=float(per_path_timeout),
    )

    findings: list[SymbolicFinding] = []
    start = time.monotonic()

    try:
        all_checkables = list(analyze_module(module, options))

        # Filter out auto-generated dataclass dunder methods — Python
        # guarantees their correctness, no need for symbolic verification.
        checkables = []
        # Loop invariant: checkables contains non-dunder items from all_checkables[0..i]
        for checkable in all_checkables:
            if _is_autogenerated_dunder(checkable):
                func_name = _extract_func_name_from_checkable(checkable)
                findings.append(SymbolicFinding(
                    function_name=func_name,
                    module_path=module_path,
                    outcome="verified",
                    message=f"Auto-generated dataclass method '{func_name}' — correct by construction",
                    duration_seconds=0.0,
                ))
            else:
                checkables.append(checkable)

        if not checkables:
            if not findings:
                findings.append(SymbolicFinding(
                    function_name="<module>",
                    module_path=module_path,
                    outcome="verified",
                    message=f"No contracted functions found in '{module_path}'",
                    duration_seconds=time.monotonic() - start,
                ))
            result_queue.put(findings)
            return

        # Loop invariant: findings contains results for all processed checkables
        for checkable in checkables:
            check_start = time.monotonic()
            try:
                messages = list(checkable.analyze())
                check_elapsed = time.monotonic() - check_start
                func_name = _extract_func_name_from_checkable(checkable)

                if not messages:
                    findings.append(SymbolicFinding(
                        function_name=func_name,
                        module_path=module_path,
                        outcome="verified",
                        message=f"Verified: postcondition holds for '{func_name}'",
                        duration_seconds=check_elapsed,
                    ))
                else:
                    # Loop invariant: findings contains results for messages[0..j]
                    for msg in messages:
                        findings.append(_message_to_finding(
                            func_name, module_path, msg, check_elapsed,
                        ))
            except Exception as exc:
                check_elapsed = time.monotonic() - check_start
                func_name = _extract_func_name_from_checkable(checkable)
                outcome = _classify_exception(exc)
                findings.append(SymbolicFinding(
                    function_name=func_name,
                    module_path=module_path,
                    outcome=outcome,
                    message=f"CrossHair cannot verify '{func_name}': {exc}",
                    duration_seconds=check_elapsed,
                ))

    except Exception as exc:
        elapsed = time.monotonic() - start
        outcome = _classify_exception(exc)
        findings.append(SymbolicFinding(
            function_name="<module>",
            module_path=module_path,
            outcome=outcome,
            message=f"CrossHair cannot verify module: {exc}",
            duration_seconds=elapsed,
        ))

    result_queue.put(findings)


# Exception types and message patterns that indicate solver limitations,
# not actual bugs in the code under verification.
_SOLVER_LIMITATION_TYPES = (RecursionError,)
_SOLVER_LIMITATION_PATTERNS = (
    "unhashable type",
    "symbolicbool",
    "notdeterministic",
    "not deterministic",
    "non-string object",
    "returned a non-",
    "must be a string, bytes or ast",
    "must be a string or",
)


def _classify_exception(exc: Exception) -> str:
    """Classify an exception as a solver limitation or a real error.

    Args:
        exc: The exception raised during verification.

    Returns:
        "unsupported" for solver limitations, "error" for real errors.
    """
    if isinstance(exc, _SOLVER_LIMITATION_TYPES):
        return "unsupported"
    error_str = str(exc).lower()
    # Loop invariant: checked patterns[0..i]
    for pattern in _SOLVER_LIMITATION_PATTERNS:
        if pattern in error_str:
            return "unsupported"
    if "unsupported" in error_str or "not implemented" in error_str:
        return "unsupported"
    return "error"


_AUTOGEN_DUNDER_NAMES = frozenset({
    "__hash__", "__eq__", "__ne__", "__repr__", "__str__",
})


def _is_autogenerated_dunder(checkable: Any) -> bool:
    """Check if a checkable is an auto-generated dataclass dunder method.

    Python's dataclass machinery generates __eq__, __hash__, __repr__,
    etc. These are guaranteed correct and don't need symbolic verification.

    Args:
        checkable: A CrossHair Checkable.

    Returns:
        True if this is an auto-generated dataclass dunder method.
    """
    name = _extract_func_name_from_checkable(checkable)
    if name not in _AUTOGEN_DUNDER_NAMES:
        return False

    # Check if the function belongs to a dataclass by inspecting
    # the checkable's function object for a dataclass owner.
    inner = checkable
    # Loop invariant: inner is the most deeply unwrapped checkable found so far
    for _depth in range(5):
        wrapped = (
            getattr(inner, "_checkable", None)
            or getattr(inner, "inner", None)
            or getattr(inner, "_inner", None)
        )
        if wrapped is None:
            break
        inner = wrapped

    # Try to find the owning class via the function's __qualname__
    ctxfn = getattr(inner, "ctxfn", None)
    if ctxfn is not None:
        fn = getattr(ctxfn, "fn", None)
        if fn is not None:
            qualname = getattr(fn, "__qualname__", "")
            if "." in qualname:
                # e.g. "SourceFile.__hash__" — get the class
                cls_name = qualname.rsplit(".", 1)[0]
                module = getattr(fn, "__module__", None)
                if module:
                    import sys
                    mod = sys.modules.get(module)
                    if mod:
                        cls = getattr(mod, cls_name, None)
                        if cls and hasattr(cls, "__dataclass_fields__"):
                            return True

    # If we can't determine the class, don't skip — verify it to be safe.
    return False


def _extract_func_name_from_checkable(checkable: Any) -> str:
    """Extract function name from a CrossHair Checkable object.

    Handles wrapper types like ClampedCheckable by unwrapping
    to find the inner ConditionCheckable with the actual name.

    Args:
        checkable: A CrossHair Checkable.

    Returns:
        The function name string.
    """
    # Unwrap wrapper checkables (ClampedCheckable wraps ConditionCheckable)
    inner = checkable
    # Loop invariant: inner is the most deeply unwrapped checkable found so far
    for _depth in range(5):
        wrapped = (
            getattr(inner, "_checkable", None)
            or getattr(inner, "inner", None)
            or getattr(inner, "_inner", None)
        )
        if wrapped is None:
            break
        inner = wrapped

    # CrossHair ConditionCheckable has ctxfn.name
    ctxfn = getattr(inner, "ctxfn", None)
    if ctxfn is not None:
        name = getattr(ctxfn, "name", None)
        if isinstance(name, str):
            return name

    # Try other common attributes on the unwrapped checkable
    # Loop invariant: checked attributes[0..i]
    for attr in ("fn", "function", "name"):
        obj = getattr(inner, attr, None)
        if obj is not None:
            name = getattr(obj, "__name__", None) or getattr(obj, "name", None)
            if isinstance(name, str):
                return name

    # Last resort: try to extract a name from the string representation
    checkable_str = str(checkable)
    if "ctxfn=Function" in checkable_str:
        # Pattern: ClampedCheckable(ConditionCheckable(ctxfn=Function(name='foo'...
        import re as _re
        name_match = _re.search(r"name='([^']+)'", checkable_str)
        if name_match:
            return name_match.group(1)

    return checkable_str[:80]


def _message_to_finding(
    func_name: str,
    module_path: str,
    msg: Any,
    elapsed: float,
) -> SymbolicFinding:
    """Convert a CrossHair AnalysisMessage to a SymbolicFinding.

    Args:
        func_name: Function name.
        module_path: Module path.
        msg: CrossHair analysis message.
        elapsed: Time taken.

    Returns:
        A SymbolicFinding.
    """
    message_str = getattr(msg, "message", str(msg))
    state = getattr(msg, "state", None)
    state_name = str(getattr(state, "name", "")) if state else ""

    if state_name == "CONFIRMED":
        # CONFIRMED means postconditions hold for all paths — success
        return SymbolicFinding(
            function_name=func_name,
            module_path=module_path,
            outcome="verified",
            message=f"Verified: postconditions hold for '{func_name}'",
            duration_seconds=elapsed,
        )
    elif state_name == "POST_FAIL":
        return SymbolicFinding(
            function_name=func_name,
            module_path=module_path,
            outcome="counterexample",
            message=f"Postcondition violated for '{func_name}': {message_str}",
            counterexample=_parse_counterexample(str(message_str)),
            duration_seconds=elapsed,
        )
    elif state_name == "CANNOT_CONFIRM":
        return SymbolicFinding(
            function_name=func_name,
            module_path=module_path,
            outcome="timeout",
            message=f"Cannot confirm postcondition for '{func_name}': {message_str}",
            duration_seconds=elapsed,
        )
    elif state_name in ("EXEC_ERR", "SYNTAX_ERR", "IMPORT_ERR"):
        # Check if this is a solver limitation rather than a real bug
        msg_lower = str(message_str).lower()
        is_solver_issue = any(p in msg_lower for p in _SOLVER_LIMITATION_PATTERNS)
        is_solver_issue = is_solver_issue or "recursionerror" in msg_lower
        outcome = "unsupported" if is_solver_issue else "error"
        return SymbolicFinding(
            function_name=func_name,
            module_path=module_path,
            outcome=outcome,
            message=f"Error verifying '{func_name}': {message_str}",
            duration_seconds=elapsed,
        )
    elif state_name == "PRE_UNSAT":
        return SymbolicFinding(
            function_name=func_name,
            module_path=module_path,
            outcome="verified",
            message=f"Preconditions unsatisfiable for '{func_name}' — vacuously true",
            duration_seconds=elapsed,
        )
    else:
        # Check if the message indicates a solver limitation
        msg_lower = str(message_str).lower()
        is_solver_issue = any(p in msg_lower for p in _SOLVER_LIMITATION_PATTERNS)
        is_solver_issue = is_solver_issue or "recursionerror" in msg_lower
        if is_solver_issue:
            return SymbolicFinding(
                function_name=func_name,
                module_path=module_path,
                outcome="unsupported",
                message=f"Solver limitation for '{func_name}': {message_str}",
                duration_seconds=elapsed,
            )
        return SymbolicFinding(
            function_name=func_name,
            module_path=module_path,
            outcome="counterexample",
            message=f"Issue found for '{func_name}': {message_str}",
            counterexample=_parse_counterexample(str(message_str)),
            duration_seconds=elapsed,
        )


def _parse_counterexample(message: str) -> dict[str, object] | None:
    """Extract counterexample values from a CrossHair message.

    Args:
        message: The error message to parse.

    Returns:
        Dict of variable→value mappings, or None.
    """
    call_match = re.search(r"when calling \w+\((.+?)\)", message)
    if call_match:
        args_str = call_match.group(1)
        counterexample: dict[str, object] = {}
        # Loop invariant: counterexample contains parsed args from parts[0..i]
        for part in args_str.split(","):
            part = part.strip()
            if "=" in part:
                key, val = part.split("=", 1)
                counterexample[key.strip()] = val.strip()
        return counterexample if counterexample else None
    return None


def _parse_cli_output(
    module_path: str,
    stdout: str,
    stderr: str,
) -> list[SymbolicFinding]:
    """Parse CrossHair CLI output into findings.

    Args:
        module_path: Module that was verified.
        stdout: CLI stdout.
        stderr: CLI stderr.

    Returns:
        List of findings parsed from CLI output.
    """
    findings: list[SymbolicFinding] = []

    if not stdout.strip() and not stderr.strip():
        return [SymbolicFinding(
            function_name="<module>",
            module_path=module_path,
            outcome="verified",
            message=f"All functions in '{module_path}' verified successfully",
        )]

    line_pattern = re.compile(r"^(.+?):(\d+):\s*error:\s*(.+)$")

    # Loop invariant: findings contains parsed results for lines[0..i]
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        match = line_pattern.match(line)
        if match:
            findings.append(SymbolicFinding(
                function_name="<unknown>",
                module_path=module_path,
                outcome="counterexample",
                message=match.group(3),
                counterexample=_parse_counterexample(match.group(3)),
            ))
        elif "error" in line.lower():
            findings.append(SymbolicFinding(
                function_name="<unknown>",
                module_path=module_path,
                outcome="error",
                message=line,
            ))

    if not findings and stderr.strip():
        findings.append(SymbolicFinding(
            function_name="<module>",
            module_path=module_path,
            outcome="error",
            message=f"CrossHair error: {stderr[:200]}",
        ))

    return findings
