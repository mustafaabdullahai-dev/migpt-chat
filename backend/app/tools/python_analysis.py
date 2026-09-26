"""Python analysis tool — sandboxed execution for data/CSV analysis, statistics,
numerical computation and chart generation.

Backends (in order of preference):
  1. docker  — isolated container (`--network none`, memory/CPU pids caps, non-root)
  2. restricted — RestrictedPython in a subprocess with rlimits/import whitelist
  3. disabled — tool refuses to run if no sandbox is available

The tool NEVER executes arbitrary code in the server process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("tools.python_analysis")

WORKER = Path(__file__).resolve().parent / "python_sandbox" / "worker.py"
SANDBOX_DIR = Path(__file__).resolve().parent / "python_sandbox"
IMAGE = "python-sandbox:latest"
MAX_CODE = 40_000
MAX_FILES = 10
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 500_000


class PythonAnalysisArgs(BaseModel):
    code: str = Field(
        ...,
        description="Python snippet for analysis. Safe builtins only; importable modules: "
                    "math, statistics, random, re, json, collections, itertools, functools, "
                    "datetime, numpy, pandas, scipy, matplotlib. Call result(x) to return a "
                    "value; matplotlib figures are returned as PNG charts.",
    )
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Files available to the snippet, e.g. {'data.csv': '<csv text>'}. Read them "
                    "by filename from the current directory inside the snippet.",
    )
    timeout_seconds: int = Field(20, ge=1, le=120)


class PythonAnalysisTool:
    name = "python_analysis"
    is_async = True

    def __init__(self) -> None:
        self._backend: str | None = None
        self._backend_checked = 0.0
        self._image_ok = False

    def backend(self) -> str:
        """Resolve the sandbox backend, cached briefly; 'disabled' never runs code."""
        now = time.monotonic()
        if self._backend is not None and now - self._backend_checked < 60:
            return self._backend
        self._backend = self._probe()
        self._backend_checked = now
        logger.info("python analysis sandbox backend", extra={"backend": self._backend})
        return self._backend

    def _probe(self) -> str:
        if not settings.enable_python_analysis or settings.python_sandbox == "disabled":
            return "disabled"
        if settings.python_sandbox in ("docker", "auto"):
            if shutil.which("docker") and self._docker_ok():
                return "docker"
        if settings.python_sandbox == "restricted" or shutil.which("docker") is None:
            try:
                import RestrictedPython  # noqa: F401
                import numpy  # noqa: F401
                import pandas  # noqa: F401

                return "restricted"
            except ImportError:
                return "disabled"
        return "disabled"

    def _docker_ok(self) -> bool:
        try:
            out = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, timeout=8,
            )
            return out.returncode == 0
        except Exception:
            return False

    async def __call__(self, code: str, files: dict[str, str] | None = None,
                       timeout_seconds: int = 20) -> dict:
        backend = self.backend()
        if backend == "disabled":
            return {
                "status": "disabled",
                "error": "Python analysis is disabled: no sandbox backend is available "
                         "(enable Docker or install RestrictedPython/numpy/pandas).",
            }
        files = files or {}
        problem = self._validate(code, files)
        if problem:
            return {"status": "validation_error", "error": problem}

        started = time.monotonic()
        try:
            if backend == "docker":
                result = self._run_docker(code, files, timeout_seconds)
            else:
                result = self._run_restricted(code, files, timeout_seconds)
        except subprocess.TimeoutExpired:
            result = {"status": "timeout", "error": f"Execution exceeded {timeout_seconds}s."}
        except Exception as exc:  # environment failure — safe to report, nothing executed badly
            logger.warning("python analysis run failed", extra={"error": str(exc)})
            result = {"status": "sandbox_error", "error": f"Sandbox failed: {exc}"}
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return result

    # ----- validation (defense in depth) -----
    @staticmethod
    def _validate(code: str, files: dict[str, str]) -> str | None:
        if not code or not code.strip():
            return "Empty code."
        if len(code) > MAX_CODE:
            return f"Code too large (max {MAX_CODE} chars)."
        if len(files) > MAX_FILES:
            return f"Too many files (max {MAX_FILES})."
        total = 0
        for name, content in files.items():
            if "\x00" in content or not content:
                return f"Invalid file content: {name!r}"
            size = len(content.encode("utf-8", "replace"))
            if size > MAX_FILE_BYTES:
                return f"File too large: {name} ({size} bytes)."
            total += size
        if total > MAX_TOTAL_BYTES:
            return "Total file size exceeds the limit."
        return None

    # ----- docker backend -----
    def _ensure_image(self) -> None:
        if self._image_ok:
            return
        inspect = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=20)
        if inspect.returncode != 0:
            logger.warning("building sandbox image (first use)")
            build = subprocess.run(
                ["docker", "build", "-q", "-t", IMAGE, "-f", "Dockerfile", "."],
                cwd=SANDBOX_DIR, capture_output=True, timeout=600,
            )
            if build.returncode != 0:
                raise RuntimeError(f"docker build failed: {build.stderr.decode()[-400:]}")
        self._image_ok = True

    def _run_docker(self, code: str, files: dict[str, str], timeout_seconds: int) -> dict:
        self._ensure_image()
        with tempfile.TemporaryDirectory(prefix="pysand-") as tmp:
            os.chmod(tmp, 0o777)
            spec = {"code": code, "files": files, "cpu_seconds": timeout_seconds, "memory_mb": 512, "sandbox": "docker"}
            (Path(tmp) / "__spec__.json").write_text(json.dumps(spec), encoding="utf-8")
            cmd = [
                "docker", "run", "--rm", "--network", "none",
                "--memory", "768m", "--cpus", "1", "--pids-limit", "64",
                "-v", f"{tmp}:/work:rw",
                IMAGE, "python", "/sandbox/__worker__.py", "/work/__spec__.json", "/work/__out__.json",
            ]
            subprocess.run(cmd, capture_output=True, timeout=timeout_seconds + 120, check=False)
            return self._read_out(tmp)

    # ----- restricted subprocess backend -----
    def _run_restricted(self, code: str, files: dict[str, str], timeout_seconds: int) -> dict:
        with tempfile.TemporaryDirectory(prefix="pysand-") as tmp:
            spec = {"code": code, "files": files, "cpu_seconds": timeout_seconds, "memory_mb": 512, "sandbox": "restricted"}
            (Path(tmp) / "__spec__.json").write_text(json.dumps(spec), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(WORKER), f"{tmp}/__spec__.json", f"{tmp}/__out__.json"],
                capture_output=True, timeout=timeout_seconds + 120, check=False,
            )
            return self._read_out(tmp)

    @staticmethod
    def _read_out(tmp: str) -> dict:
        out_path = Path(tmp) / "__out__.json"
        if not out_path.exists():
            return {
                "status": "error",
                "error": "Sandbox terminated before producing output (timeout, memory, or CPU limit).",
            }
        return json.loads(out_path.read_text(encoding="utf-8"))


python_analysis_tool = PythonAnalysisTool()