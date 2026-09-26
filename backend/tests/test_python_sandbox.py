"""Security/behavior tests for the sandboxed Python analysis tool."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.tools.python_analysis import PythonAnalysisTool, WORKER

sys.path.insert(0, str(Path(WORKER).parent))


def test_validate_caps():
    t = PythonAnalysisTool()
    assert t._validate("", {}) == "Empty code."
    assert t._validate("x" * 50_000, {})  # too large
    assert t._validate("print(1)", {"a.csv": "x" * 250_000})
    assert t._validate("print(1)", {"a": "ok", "b": "ok", "c": "ok", "d": "ok", "e": "ok",
                                    "f": "ok", "g": "ok", "h": "ok", "i": "ok", "j": "ok",
                                    "k": "ok"})
    assert t._validate("print(1)", {"a": "\x00bad"})
    assert t._validate("print(1)", {"a": "ok"}) is None


@pytest.mark.asyncio
async def test_disabled_backend_refuses_to_run(monkeypatch):
    t = PythonAnalysisTool()
    t._backend = "disabled"
    t._backend_checked = sys.maxsize
    r = await t('print(open("/etc/passwd").read())', timeout_seconds=2)
    assert r["status"] == "disabled"
    assert "no sandbox" in r["error"]


def test_restricted_runner_blocks_dangerous_code():
    from worker import run_code  # type: ignore[import-not-found]

    r = run_code("import os\nprint(os.listdir('/'))", {}, "/tmp/pysand-test-guards")
    assert r["status"] == "error"
    assert "Import not allowed" in r["error"]

    r = run_code("print(open('/etc/shadow').read())", {}, "/tmp/pysand-test-guards")
    assert r["status"] == "error"
    assert "not defined" in r["error"]

    r = run_code("import subprocess; subprocess.run(['id'])", {}, "/tmp/pysand-test-guards")
    assert "Import not allowed" in r["error"]


def test_restricted_runner_runs_analysis_and_captures():
    from worker import run_code  # type: ignore[import-not-found]

    r = run_code(
        "import statistics\nx = [10, 20, 30, 40]\n"
        "print('mean:', statistics.mean(x))\nresult(statistics.stdev(x))",
        {}, "/tmp/pysand-test-analysis",
    )
    assert r["status"] == "ok"
    assert "mean: 25" in r["stdout"]
    assert r["result"] is not None


def test_import_whitelist():
    from worker import _restricted_import  # type: ignore[import-not-found]

    for bad in ("os", "subprocess", "socket", "requests", "urllib"):
        with pytest.raises(ImportError):
            _restricted_import(bad)
    import math

    assert _restricted_import("math") is math


def test_main_worker_hard_cpu_limit_terminates_loop():
    """End-to-end: worker.main() must bound runaway code via RLIMIT_CPU, not hang."""
    import json
    import os
    import subprocess
    import tempfile

    from app.tools.python_analysis import WORKER

    with tempfile.TemporaryDirectory(prefix="pysand-loop-") as tmp:
        spec = {"code": "while True:\n    pass", "files": {}, "cpu_seconds": 1, "memory_mb": 512}
        spec_path = os.path.join(tmp, "__spec__.json")
        out_path = os.path.join(tmp, "__out__.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)

        proc = subprocess.run(
            [sys.executable, str(WORKER), spec_path, out_path],
            capture_output=True, timeout=30,
        )
        # Must terminate on its own (CPU limit), not be our test's fallback timeout.
        assert os.path.exists(out_path) is False or json.loads(
            open(out_path, encoding="utf-8").read()
        ).get("status") != "ok"