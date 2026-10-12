#!/usr/bin/env python3
"""Local source-release rehearsal for OpenShop.

This does not publish anything. It packages an existing annotated Git tag,
verifies the archive boundaries and SHA-256, runs the standard-library test
suite and repository gate from the extracted tree, then boots that extracted
tree on loopback and checks health plus public catalog endpoints.

Run after creating the local release tag:

    python3 -B tools/rehearse_release.py --tag v0.1.0 \
      --out-dir /path/to/release-evidence

Production deployment, remote CI and rollback are explicitly out of scope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tarfile
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    "test-results",
    "data.sqlite3",
    "openshop.sqlite3",
}
FORBIDDEN_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".log", ".pyc", ".pyo", ".pem", ".key"}


class RehearsalFailure(RuntimeError):
    pass


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_checked(command: list[str], *, cwd: Path, log_path: Path, env: dict[str, str] | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as handle:
        result = subprocess.run(command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, env=env)
    if result.returncode != 0:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2500:]
        except OSError:
            pass
        raise RehearsalFailure(f"command failed rc={result.returncode}: {' '.join(command)}\n{tail}")
    return result.returncode


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RehearsalFailure(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def choose_port(requested: int | None) -> int:
    if requested is not None:
        if not (1 <= requested <= 65535):
            raise RehearsalFailure("--port must be between 1 and 65535")
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for_http(url: str, timeout: float = 40.0) -> bytes:
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if response.status == 200:
                    return response.read()
                last_error = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.35)
    raise RehearsalFailure(f"timed out waiting for {url}: {last_error}")


def http_json(url: str) -> tuple[int, object]:
    with urllib.request.urlopen(url, timeout=5.0) as response:
        raw = response.read()
        return response.status, json.loads(raw.decode("utf-8"))


def archive_boundary_audit(archive: Path, extracted: Path) -> dict[str, object]:
    if not tarfile.is_tarfile(archive):
        raise RehearsalFailure("release output is not a tar archive")
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        names = [member.name for member in members]
        unsafe: list[str] = []
        forbidden: list[str] = []
        for member in members:
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts or member.issym() or member.islnk():
                unsafe.append(name)
            parts = Path(name).parts
            if any(part in FORBIDDEN_PARTS for part in parts):
                forbidden.append(name)
            if Path(name).suffix.lower() in FORBIDDEN_SUFFIXES:
                forbidden.append(name)
        if unsafe:
            raise RehearsalFailure("archive contains unsafe paths: " + ", ".join(sorted(set(unsafe))[:5]))
        if forbidden:
            raise RehearsalFailure("archive contains runtime/secret-like files: " + ", ".join(sorted(set(forbidden))[:5]))
        required = {
            "apps/storefront/server.py",
            "LICENSE",
            "README.md",
            "tools/check_repository.py",
        }
        roots = {name.split("/", 1)[0] for name in names if "/" in name}
        if len(roots) != 1:
            raise RehearsalFailure(f"archive must have exactly one top-level directory, got {sorted(roots)}")
        root = next(iter(roots))
        for suffix in required:
            if f"{root}/{suffix}" not in names:
                raise RehearsalFailure(f"archive missing required path: {suffix}")
        handle.extractall(extracted)
    extracted_root = extracted / root
    if not extracted_root.is_dir():
        raise RehearsalFailure("archive root could not be extracted")
    return {"members": len(members), "top_level_root": root, "extracted_root": str(extracted_root)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rehearse a local OpenShop source tag without publishing it")
    parser.add_argument("--tag", required=True, help="existing annotated tag, e.g. v0.1.0")
    parser.add_argument("--out-dir", required=True, type=Path, help="external evidence directory")
    parser.add_argument("--port", type=int, default=None, help="loopback rehearsal port (default: free port)")
    args = parser.parse_args()

    started = stamp()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise RehearsalFailure(f"out-dir must be new or empty: {out_dir}")

    summary: dict[str, object] = {
        "kind": "openshop-local-source-release-rehearsal",
        "started_at": started,
        "published": False,
        "tag": args.tag,
        "checks": {},
        "verdict": "FAIL",
    }
    error_text = ""
    port = choose_port(args.port)
    summary["port"] = port
    try:
        if git("status", "--porcelain"):
            raise RehearsalFailure("working tree is dirty; refusing to rehearse a source release")
        release_commit = git("rev-parse", "HEAD")
        tag_commit = git("rev-parse", f"{args.tag}^{{commit}}")
        if release_commit != tag_commit:
            raise RehearsalFailure(f"tag {args.tag} does not point at current HEAD")
        tag_type = git("cat-file", "-t", args.tag)
        if tag_type != "tag":
            raise RehearsalFailure(f"{args.tag} is not an annotated tag (got {tag_type})")
        summary["release_commit"] = release_commit
        summary["tag_type"] = tag_type

        version = args.tag[1:] if args.tag.startswith("v") else args.tag
        archive = out_dir / f"openshop-{version}.tar.gz"
        prefix = f"openshop-{version}/"
        run_checked(
            ["git", "archive", f"--format=tar.gz", f"--prefix={prefix}", f"--output={archive}", args.tag],
            cwd=REPO,
            log_path=out_dir / "logs" / "git-archive.log",
        )
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (out_dir / f"{archive.name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        summary["archive"] = str(archive)
        summary["archive_sha256"] = digest

        extraction_dir = out_dir / "extracted"
        extraction_dir.mkdir()
        archive_info = archive_boundary_audit(archive, extraction_dir)
        extracted_root = Path(str(archive_info["extracted_root"]))
        summary["checks"]["archive_boundary"] = "PASS"
        summary["archive_members"] = archive_info["members"]

        env = dict(os.environ)
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "OPENSHOP_TMP_BASE": str(out_dir / "test-tmp"),
                "TMPDIR": str(out_dir / "test-tmp"),
            }
        )
        (out_dir / "test-tmp").mkdir(parents=True, exist_ok=True)

        run_checked(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=extracted_root,
            log_path=out_dir / "logs" / "unittest-discover.log",
            env=env,
        )
        summary["checks"]["unittest_discover"] = "PASS"

        run_checked(
            [sys.executable, "-B", "tools/check_repository.py", "--selftest"],
            cwd=extracted_root,
            log_path=out_dir / "logs" / "repository-gate-selftest.log",
            env=env,
        )
        summary["checks"]["repository_gate_selftest"] = "PASS"
        run_checked(
            [sys.executable, "-B", "tools/check_repository.py", "--check"],
            cwd=extracted_root,
            log_path=out_dir / "logs" / "repository-gate-check.log",
            env=env,
        )
        summary["checks"]["repository_gate_check"] = "PASS"

        data_dir = out_dir / "runtime-data"
        server_log = out_dir / "logs" / "server.log"
        server_log.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-u",
            "-B",
            "apps/storefront/server.py",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
        ]
        with open(server_log, "wb") as handle:
            proc = subprocess.Popen(
                command,
                cwd=extracted_root,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        try:
            health = wait_for_http(f"http://127.0.0.1:{port}/healthz")
            if health.strip() != b"ok":
                raise RehearsalFailure("health endpoint did not return ok")
            status, api_health = http_json(f"http://127.0.0.1:{port}/api/health")
            if status != 200 or not isinstance(api_health, dict):
                raise RehearsalFailure("api health response is not a JSON object")
            data = api_health.get("data") if isinstance(api_health.get("data"), dict) else api_health
            if not isinstance(data, dict) or data.get("demo") is not True:
                raise RehearsalFailure("api health does not declare demo=true")
            status, home = http_json(f"http://127.0.0.1:{port}/api/home")
            if status != 200 or not isinstance(home, dict):
                raise RehearsalFailure("public home API did not return JSON")
            home_data = home.get("data") if isinstance(home.get("data"), dict) else None
            if not home_data:
                raise RehearsalFailure("public home API has no data payload")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5.0) as response:
                page = response.read(2000)
                if response.status != 200 or b"OpenShop" not in page and b"\xe5\xbc\x80\xe9\x9b\x86" not in page:
                    raise RehearsalFailure("storefront page did not return the expected HTML shell")
            summary["checks"]["extracted_server_health"] = "PASS"
            summary["checks"]["extracted_public_api"] = "PASS"
            summary["server_url_used"] = f"http://127.0.0.1:{port}/"
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=4)
        summary["verdict"] = "PASS"
        summary["finished_at"] = stamp()
        (out_dir / "release-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"REHEARSAL verdict=PASS tag={args.tag} commit={release_commit} "
            f"sha256={digest} checks={len(summary['checks'])} out={out_dir}",
            flush=True,
        )
        return 0
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        summary["error"] = error_text
        summary["finished_at"] = stamp()
        (out_dir / "release-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / "first-failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print(f"REHEARSAL verdict=FAIL error={error_text} out={out_dir}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
