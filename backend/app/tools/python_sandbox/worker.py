"""Sandboxed Python executor — used by the analysis tool.

Deliberately self-contained (no app imports) so the SAME file runs both as a
subprocess worker and inside the Docker sandbox image.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import resource
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Import whitelist — the only modules user snippets may load.
# ---------------------------------------------------------------------------
ALLOWED_MODULES = {
    "math", "statistics", "random", "re", "json", "collections", "itertools",
    "functools", "datetime", "numpy", "pandas", "matplotlib", "scipy",
}
_import_cache: dict[str, object] = {}


def _restricted_import(name: str, globals=None, locals=None, fromlist=(), level=0):
    base = (name or "").split(".")[0]
    if base not in ALLOWED_MODULES:
        raise ImportError(f"Import not allowed in sandbox: {name!r}")
    import importlib

    if "." in name or fromlist:
        # import matplotlib.pyplot / from matplotlib import pyplot: load the submodule fully
        # but return the top-level module so attribute binding works like real __import__.
        if name not in _import_cache:
            importlib.import_module(name)
            _import_cache[name] = True
        full = importlib.import_module(base)
        return full
    if base not in _import_cache:
        _import_cache[base] = importlib.import_module(base)
    return _import_cache[base]


def _to_plain(value):
    """Coerce numpy/pandas values into plain JSON-able Python."""
    import numbers

    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if hasattr(value, "to_dict"):
        try:
            return str(value.head(200)) if hasattr(value, "head") else str(value)
        except Exception:
            return str(value)
    if hasattr(value, "tolist"):
        try:
            return _to_plain(value.tolist())
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in list(value.items())[:200]}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(v) for v in value][:200]
    return value


class _Result:
    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = None

    def set(self, value) -> None:
        self.value = _to_plain(value)


def _make_sandbox(result_box: _Result) -> tuple[dict, object]:
    from RestrictedPython import compile_restricted, safe_builtins
    from RestrictedPython.Eval import default_guarded_getitem
    from RestrictedPython.Guards import full_write_guard

    for candidate in ("safer_getattr_raise", "safer_getattr", "protected_getattr", "guarded_getattr"):
        if hasattr(Guards := __import__("RestrictedPython.Guards", fromlist=["x"]), candidate):
            getattr_guard = getattr(Guards, candidate)
            break
    else:  # pragma: no cover
        getattr_guard = lambda obj, name: getattr(obj, name)

    try:
        from RestrictedPython.Guards import guarded_iter_unpack_sequence as iter_guard
    except ImportError:  # older/newer API fallback
        from RestrictedPython.Eval import default_guarded_iter_unpack_sequence as iter_guard

    builtins = dict(safe_builtins)
    builtins["__import__"] = _restricted_import
    builtins["result"] = result_box.set
    builtins["breakpoint"] = None
    # safe_builtins drops common harmless builtins; data analysis needs them back.
    # None of these can touch files, the network, or spawn processes.
    import builtins as _real_builtins

    for _name in (
        "list", "dict", "set", "frozenset", "object", "format", "sum", "min",
        "max", "enumerate", "reversed", "next", "iter", "any", "all", "filter",
        "map",
    ):
        builtins.setdefault(_name, _real_builtins.__dict__[_name])

    def _print_(*args, **kwargs):  # placeholder replaced below by PrintCollector
        print(*args, **kwargs)

    def _inplacevar_(op: str, var, val):  # 'n += 1' -> 'n = _inplacevar_("+=", n, 1)'
        ops = {
            "+=": lambda a, b: a + b, "-=": lambda a, b: a - b,
            "*=": lambda a, b: a * b, "/=": lambda a, b: a / b,
            "//=": lambda a, b: a // b, "%=": lambda a, b: a % b,
            "**=": lambda a, b: a ** b, "&=": lambda a, b: a & b,
            "|=": lambda a, b: a | b, "^=": lambda a, b: a ^ b,
            "<<=": lambda a, b: a << b, ">>=": lambda a, b: a >> b,
        }
        return ops[op](var, val)

    try:
        from RestrictedPython.PrintCollector import PrintCollector

        _print_ = PrintCollector
    except ImportError:  # pragma: no cover
        pass

    _locals = {
        "__builtins__": builtins,
        "_print_": _print_,
        "_inplacevar_": _inplacevar_,
        "_getattr_": getattr_guard,
        "_getitem_": default_guarded_getitem,
        "_write_": full_write_guard,
        "_iter_unpack_sequence_": iter_guard,
    }
    return _locals, compile_restricted


def run_code(code: str, files: dict[str, str], workdir: str) -> dict:
    """Executes sandboxed `code` in `workdir` (files written first). Returns a dict."""
    result_box = _Result()
    charts: list[dict] = []
    output = io.StringIO()
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = output, output

    try:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            safe_name = Path(name).name
            if safe_name and safe_name not in (".", ".."):
                (Path(workdir) / safe_name).write_text(content, encoding="utf-8", errors="replace")

        _locals, compile_factory = _make_sandbox(result_box)
        _locals["__name__"] = "__sandbox__"
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # ignore RestrictedPython's cosmetic SyntaxWarning
            code_obj = compile_factory(code, "<sandbox>", "exec")
        old_cwd = Path.cwd()
        os.chdir(workdir)
        try:
            exec(code_obj, _locals, _locals)  # noqa: S102 — RestrictedPython-compiled
        finally:
            os.chdir(old_cwd)

        charts = _capture_charts()
        stdout = _collected_print(_locals) + output.getvalue()

        return {
            "status": "ok",
            "stdout": stdout[-160000:],
            "result": result_box.value,
            "charts": charts,
            "error": None,
        }
    except TimeoutError:
        return {"status": "timeout", "error": "Execution exceeded the time limit."}
    except BaseException as exc:  # user snippet raised — report cleanly
        import traceback

        frame = traceback.extract_tb(exc.__traceback__)
        line = frame[-1].lineno if frame else 0
        stdout = (_collected_print(_locals) if "_locals" in dir() else "") + output.getvalue()
        return {
            "status": "error",
            "stdout": stdout[-160000:],
            "result": result_box.value,
            "charts": [],  # discard partial figures on failure
            "error": f"{type(exc).__name__} on line {line}: {exc}",
        }
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr


def _collected_print(_locals: dict) -> str:
    """Returns text captured by the restricted print collector, if print() was used."""
    from RestrictedPython.PrintCollector import PrintCollector

    collector = _locals.get("_print")
    if isinstance(collector, PrintCollector):
        try:
            return collector()
        except Exception:
            return ""
    if "printed" in _locals and isinstance(_locals["printed"], str):  # older transformer
        return _locals["printed"]
    return ""


def _capture_charts() -> list[dict]:
    """Render any open matplotlib figures to base64 PNGs, then close them."""
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt

        out: list[dict] = []
        for num in plt.get_fignums():
            fig = plt.figure(num)
            buf = io.BytesIO()
            try:
                fig.savefig(buf, format="png", bbox_inches="tight")
            except Exception:
                continue
            out.append({"name": f"figure_{num}.png", "png_base64": base64.b64encode(buf.getvalue()).decode()})
            plt.close(num)
        return out
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("out")
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    workdir = str(Path(args.spec).resolve().parent)

    # hard resource limits (belt & braces even inside Docker)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (min(spec.get("cpu_seconds", 8), 20),) * 2)
        mem_mb = spec.get("memory_mb", 512)
        resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    except (ValueError, OSError):
        pass

    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "mplcache"))

    import time as _time

    started = _time.monotonic()
    result = run_code(spec.get("code", ""), spec.get("files", {}), workdir)
    result["duration_s"] = round(_time.monotonic() - started, 3)
    result["sandbox"] = spec.get("sandbox", "python")
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def main_argv(args=None):
    sys.argv = args or sys.argv
    return main()