#!/usr/bin/env python3
"""Real OpenShop server lifecycle for QA acceptance.

Rules enforced here:
  * every runtime database/log/screenshot lives below
    /hf3fs-jd/data/tmp/openshop/ (3FS tmp);
  * data directories are new and empty (never reuse or delete product data);
  * the server is started as a real subprocess and stopped as a process group;
  * startup failures preserve the full server log and raise loudly.

This module is test tooling only. It never starts a production instance.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RUNTIME_ROOT = Path("/hf3fs-jd/data/tmp/openshop")
REPO_ROOT = Path(__file__).resolve().parents[2]
START_TIMEOUT_SECONDS = 45.0


class ServerStartError(RuntimeError):
    """The real server could not be started or became unhealthy."""


def runtime_path(path: str | Path, label: str) -> Path:
    # Keep the user-facing /hf3fs-jd prefix in evidence paths while verifying
    # the physical target really lives below the allowed 3FS tmp root.
    raw = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    resolved = raw.resolve()
    try:
        resolved.relative_to(RUNTIME_ROOT.resolve())
    except ValueError as exc:
        raise ServerStartError(
            f"{label} must live under {RUNTIME_ROOT} (3FS tmp), got {resolved}"
        ) from exc
    return raw


def ensure_new_data_dir(path: str | Path) -> Path:
    target = runtime_path(path, "data_dir")
    if target.exists():
        if not target.is_dir():
            raise ServerStartError(f"data_dir exists and is not a directory: {target}")
        entries = list(target.iterdir())
        if entries:
            raise ServerStartError(
                f"refusing to reuse non-empty data_dir: {target} "
                f"(first entries: {[p.name for p in entries[:5]]})"
            )
    else:
        target.mkdir(parents=True, exist_ok=False)
    target.chmod(0o700)
    return target


def evidence_file(path: str | Path, label: str) -> Path:
    target = runtime_path(path, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def tail(path: Path, lines: int = 80) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<log unreadable>"
    return "\n".join(text.splitlines()[-lines:])


def _port_has_listener(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _health(port: int) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
            return payload if payload.get("ok") is True else None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


class ServerProcess:
    """Owns one real OpenShop server process and its fresh database."""

    def __init__(
        self,
        *,
        repo: str | Path = REPO_ROOT,
        port: int,
        data_dir: str | Path,
        log_path: str | Path,
        host: str = "127.0.0.1",
    ) -> None:
        self.repo = Path(repo).resolve()
        self.port = int(port)
        self.host = host
        self.data_dir = ensure_new_data_dir(data_dir)
        self.log_path = evidence_file(log_path, "server_log")
        self.proc: subprocess.Popen | None = None
        self._log_handle = None
        self.health: dict | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "ServerProcess":
        if self.proc is not None:
            raise ServerStartError("server already started")
        if _port_has_listener(self.port):
            raise ServerStartError(
                f"port {self.port} already has a listener; choose a free QA port, "
                "refusing to touch the existing process"
            )
        server_py = self.repo / "apps" / "storefront" / "server.py"
        if not server_py.is_file():
            raise ServerStartError(f"server entrypoint missing: {server_py}")
        self._log_handle = open(self.log_path, "w", encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["OPENSHOP_LOG_LEVEL"] = "INFO"
        cmd = [
            sys.executable,
            "-B",
            str(server_py),
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--data-dir",
            str(self.data_dir),
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(self.repo),
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            rc = self.proc.poll()
            if rc is not None:
                message = (
                    f"server exited during startup rc={rc}; cmd={' '.join(cmd)}; "
                    f"log={self.log_path}\n--- server log tail ---\n{tail(self.log_path)}"
                )
                self._close_log()
                raise ServerStartError(message)
            self.health = _health(self.port)
            if self.health is not None:
                return self
            time.sleep(0.2)
        self.stop()
        raise ServerStartError(
            f"server did not become healthy within {START_TIMEOUT_SECONDS}s; "
            f"log={self.log_path}\n--- server log tail ---\n{tail(self.log_path)}"
        )

    def stop(self) -> None:
        proc = self.proc
        if proc is None:
            self._close_log()
            return
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self.proc = None
        self._close_log()

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.flush()
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def __enter__(self) -> "ServerProcess":
        return self.start()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.stop()
