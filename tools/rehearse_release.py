#!/usr/bin/env python3
"""Local source-release rehearsal and its negative-control self-test.

This does not publish anything. It packages an existing annotated Git tag,
verifies archive boundaries/SHA-256, runs the standard-library test suite and
repository gate from the extracted tree, then boots that extracted tree on
loopback and exercises health, session/demo login, catalog and storefront.

Run after creating the local release tag:

    python3 -B tools/rehearse_release.py --tag v0.1.0 \
      --out-dir /path/to/release-evidence

The self-test uses only temporary local fixtures and deliberately verifies
that dirty trees, bad tags, unsafe archives and unhealthy endpoints fail:

    python3 -B tools/rehearse_release.py --selftest \
      --out-dir /path/to/empty-selftest-evidence

Production deployment, remote CI, public DNS/TLS and systemd rollback are
explicitly out of scope.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
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
REQUIRED_ARCHIVE_PATHS = {
    "apps/storefront/server.py",
    "LICENSE",
    "README.md",
    "tools/check_repository.py",
}


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


def git_at(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RehearsalFailure(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def worktree_is_clean(repo: Path) -> bool:
    return not git_at(repo, "status", "--porcelain")


def validate_release_identity(tag: str, repo: Path = REPO) -> dict[str, str]:
    if not worktree_is_clean(repo):
        raise RehearsalFailure("working tree is dirty; refusing to rehearse a source release")
    release_commit = git_at(repo, "rev-parse", "HEAD")
    tag_commit = git_at(repo, "rev-parse", f"{tag}^{{commit}}")
    if release_commit != tag_commit:
        raise RehearsalFailure(f"tag {tag} does not point at current HEAD")
    tag_type = git_at(repo, "cat-file", "-t", tag)
    if tag_type != "tag":
        raise RehearsalFailure(f"{tag} is not an annotated tag (got {tag_type})")
    return {"release_commit": release_commit, "tag_type": tag_type}


def choose_port(requested: int | None) -> int:
    if requested is not None:
        if not (1 <= requested <= 65535):
            raise RehearsalFailure("--port must be between 1 and 65535")
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def choose_unique_ports(count: int, exclude: set[int] | None = None) -> list[int]:
    used = set(exclude or ())
    ports: list[int] = []
    while len(ports) < count:
        candidate = choose_port(None)
        if candidate in used:
            continue
        used.add(candidate)
        ports.append(candidate)
    return ports


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
        roots = {name.split("/", 1)[0] for name in names if "/" in name}
        if len(roots) != 1:
            raise RehearsalFailure(f"archive must have exactly one top-level directory, got {sorted(roots)}")
        root = next(iter(roots))
        for suffix in REQUIRED_ARCHIVE_PATHS:
            if f"{root}/{suffix}" not in names:
                raise RehearsalFailure(f"archive missing required path: {suffix}")
        handle.extractall(extracted)
    extracted_root = extracted / root
    if not extracted_root.is_dir():
        raise RehearsalFailure("archive root could not be extracted")
    return {
        "members": len(members),
        "top_level_root": root,
        "extracted_root": str(extracted_root),
        "names": names,
    }


def write_archive_manifest(archive: Path, manifest_path: Path) -> int:
    with tarfile.open(archive, "r:gz") as handle:
        names = sorted(member.name for member in handle.getmembers())
    manifest_path.write_text(chr(10).join(names) + chr(10), encoding="utf-8")
    return len(names)


class LocalHttpSession:
    """Cookie/CSRF-aware client used only against the localhost rehearsal server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.csrf = ""

    def json(self, method: str, path: str, body: dict[str, object] | None = None) -> tuple[int, dict]:
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            if self.csrf:
                headers["X-OpenShop-CSRF"] = self.csrf
        request = urllib.request.Request(
            self.base_url + path, data=payload, headers=headers, method=method
        )
        with self.opener.open(request, timeout=10.0) as response:
            raw = response.read()
            status = response.status
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RehearsalFailure(f"{method} {path} did not return JSON: {type(exc).__name__}") from exc
        if not isinstance(parsed, dict):
            raise RehearsalFailure(f"{method} {path} JSON root is not an object")
        return status, parsed

    def catalog_and_demo_login(self) -> dict[str, object]:
        status, session = self.json("GET", "/api/session")
        if status != 200:
            raise RehearsalFailure(f"GET /api/session returned HTTP {status}")
        session_data = session.get("data")
        if not isinstance(session_data, dict):
            raise RehearsalFailure("session response has no data object")
        self.csrf = str(session_data.get("csrf") or "")
        if not self.csrf:
            raise RehearsalFailure("guest session did not provide a CSRF token")
        status, login = self.json("POST", "/api/auth/demo", {"role": "buyer"})
        if status != 200:
            raise RehearsalFailure(f"demo buyer login returned HTTP {status}")
        login_data = login.get("data")
        if not isinstance(login_data, dict):
            raise RehearsalFailure("demo login response has no data object")
        user = login_data.get("user")
        role = user.get("role") if isinstance(user, dict) else login_data.get("role")
        if role != "buyer":
            raise RehearsalFailure("demo login did not establish a buyer session")
        self.csrf = str(login_data.get("csrf") or self.csrf)
        status, products = self.json("GET", "/api/products?page_size=1")
        if status != 200:
            raise RehearsalFailure(f"catalog API returned HTTP {status}")
        product_data = products.get("data")
        items = product_data.get("items") if isinstance(product_data, dict) else None
        if not isinstance(items, list) or not items:
            raise RehearsalFailure("catalog API returned no products after demo login")
        return {"product_count_seen": len(items), "demo_role": role, "csrf_present": True}


def _write_tar_fixture(path: Path, extra: dict[str, bytes] | None = None) -> None:
    members: dict[str, bytes] = {
        f"openshop-selftest/{name}": b"selftest fixture\n"
        for name in sorted(REQUIRED_ARCHIVE_PATHS)
    }
    if extra:
        members.update(extra)
    with tarfile.open(path, "w:gz") as handle:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            handle.addfile(info, io.BytesIO(content))


def run_selftest(out_dir: Path) -> int:
    controls: list[dict[str, str]] = []
    summary: dict[str, object] = {
        "kind": "openshop-release-rehearsal-selftest",
        "started_at": stamp(),
        "published": False,
        "controls": controls,
        "verdict": "FAIL",
    }

    def control(name: str, expect_reject: bool, action) -> None:
        rejected = False
        detail = ""
        try:
            action()
            detail = "accepted"
        except Exception as exc:  # fixture errors are rejection evidence for this control
            rejected = True
            detail = f"{type(exc).__name__}: {exc}"
        ok = rejected if expect_reject else not rejected
        row = {
            "name": name,
            "expected": "reject" if expect_reject else "accept",
            "observed": "rejected" if rejected else "accepted",
            "detail": detail[:500],
            "verdict": "PASS" if ok else "FAIL",
        }
        controls.append(row)
        print(
            f"SELFTEST_CONTROL name={name} expected={row['expected']} "
            f"observed={row['observed']} verdict={row['verdict']}",
            flush=True,
        )
        if not ok:
            raise RehearsalFailure(
                f"selftest control {name} expected {row['expected']} but observed {row['observed']}"
            )

    try:
        with tempfile.TemporaryDirectory(prefix="release-rehearsal-selftest-", dir=str(out_dir)) as tmp:
            work = Path(tmp)
            repo = work / "repo"
            repo.mkdir()
            git_at(repo, "init", "-q")
            git_at(repo, "config", "user.name", "OpenShop Release Selftest")
            git_at(repo, "config", "user.email", "selftest@openshop.invalid")
            (repo / "README.md").write_text("selftest fixture\n", encoding="utf-8")
            git_at(repo, "add", "README.md")
            git_at(repo, "commit", "-q", "-m", "selftest initial")

            def require_clean() -> None:
                if not worktree_is_clean(repo):
                    raise RehearsalFailure("dirty tree detected")

            control("clean_tree_accepted", False, require_clean)
            (repo / "untracked-secret.log").write_text("not for release\n", encoding="utf-8")
            control("dirty_tree_rejected", True, require_clean)
            (repo / "untracked-secret.log").unlink()

            git_at(repo, "tag", "-a", "v-selftest", "-m", "annotated selftest fixture")
            control("annotated_tag_accepted", False, lambda: validate_release_identity("v-selftest", repo=repo))

            (repo / "second.txt").write_text("second commit\n", encoding="utf-8")
            git_at(repo, "add", "second.txt")
            git_at(repo, "commit", "-q", "-m", "selftest second")
            control("tag_not_at_head_rejected", True, lambda: validate_release_identity("v-selftest", repo=repo))

            git_at(repo, "tag", "v-light-selftest")
            control("lightweight_tag_rejected", True, lambda: validate_release_identity("v-light-selftest", repo=repo))
            control("missing_tag_rejected", True, lambda: validate_release_identity("v-does-not-exist", repo=repo))

            good = work / "good.tar.gz"
            _write_tar_fixture(good)
            extract_ok = work / "extract-ok"
            extract_ok.mkdir()
            control("safe_archive_accepted", False, lambda: archive_boundary_audit(good, extract_ok))

            bad_artifact = work / "bad-artifact.tar.gz"
            _write_tar_fixture(bad_artifact, {"openshop-selftest/openshop.sqlite3": b"not shipped\n"})
            extract_bad = work / "extract-bad"
            extract_bad.mkdir()
            control("runtime_artifact_rejected", True, lambda: archive_boundary_audit(bad_artifact, extract_bad))

            traversal = work / "traversal.tar.gz"
            _write_tar_fixture(traversal, {"openshop-selftest/../escape.txt": b"escape\n"})
            extract_traversal = work / "extract-traversal"
            extract_traversal.mkdir()
            control("traversal_archive_rejected", True, lambda: archive_boundary_audit(traversal, extract_traversal))

            dead_port = choose_port(None)
            control(
                "unhealthy_endpoint_rejected",
                True,
                lambda: wait_for_http(f"http://127.0.0.1:{dead_port}/healthz", timeout=0.7),
            )

        summary["finished_at"] = stamp()
        summary["verdict"] = "PASS"
        summary["control_count"] = len(controls)
        (out_dir / "release-selftest-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"REHEARSAL_SELFTEST verdict=PASS controls={len(controls)} out={out_dir}",
            flush=True,
        )
        return 0
    except Exception as exc:
        summary["finished_at"] = stamp()
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["control_count"] = len(controls)
        (out_dir / "release-selftest-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / "selftest-first-failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print(f"REHEARSAL_SELFTEST verdict=FAIL error={summary['error']} out={out_dir}", file=sys.stderr, flush=True)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Rehearse a local OpenShop source tag without publishing it")
    parser.add_argument("--tag", help="existing annotated tag, e.g. v0.1.0")
    parser.add_argument("--out-dir", required=True, type=Path, help="external evidence directory")
    parser.add_argument("--selftest", action="store_true", help="run temporary-fixture negative controls only")
    parser.add_argument("--port", type=int, default=None, help="loopback rehearsal port (default: free port)")
    args = parser.parse_args()
    if not args.selftest and not args.tag:
        parser.error("--tag is required unless --selftest is used")

    started = stamp()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise RehearsalFailure(f"out-dir must be new or empty: {out_dir}")
    if args.selftest:
        return run_selftest(out_dir)

    summary: dict[str, object] = {
        "kind": "openshop-local-source-release-rehearsal",
        "started_at": started,
        "published": False,
        "tag": args.tag,
        "checks": {},
        "verdict": "FAIL",
    }
    port = choose_port(args.port)
    summary["port"] = port
    try:
        identity = validate_release_identity(str(args.tag))
        release_commit = identity["release_commit"]
        tag_type = identity["tag_type"]
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
        manifest_path = out_dir / "archive-manifest.txt"
        manifest_count = write_archive_manifest(archive, manifest_path)
        summary["archive"] = str(archive)
        summary["archive_sha256"] = digest
        summary["archive_manifest"] = str(manifest_path)
        summary["archive_manifest_members"] = manifest_count

        extraction_dir = out_dir / "extracted"
        extraction_dir.mkdir()
        archive_info = archive_boundary_audit(archive, extraction_dir)
        extracted_root = Path(str(archive_info["extracted_root"]))
        summary["checks"]["archive_boundary"] = "PASS"
        summary["archive_members"] = archive_info["members"]

        # Each backend suite owns a real server. Give this rehearsal isolated
        # ports so it can run alongside other developer/agent tests that use
        # the default 8861/8862/8865 ports.
        catalog_port, trade_port, seller_port = choose_unique_ports(3, exclude={port})
        env = dict(os.environ)
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "OPENSHOP_TMP_BASE": str(out_dir / "test-tmp"),
                "TMPDIR": str(out_dir / "test-tmp"),
                "OPENSHOP_CATALOG_TEST_PORT": str(catalog_port),
                "OPENSHOP_TRADE_TEST_PORT": str(trade_port),
                "OPENSHOP_SELLER_TEST_PORT": str(seller_port),
            }
        )
        summary["unit_test_ports"] = {
            "catalog": catalog_port,
            "trade": trade_port,
            "seller": seller_port,
        }
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
            session_checks = LocalHttpSession(f"http://127.0.0.1:{port}").catalog_and_demo_login()
            summary["session_check"] = session_checks
            summary["checks"]["extracted_server_health"] = "PASS"
            summary["checks"]["extracted_public_api"] = "PASS"
            summary["checks"]["extracted_demo_login"] = "PASS"
            summary["checks"]["extracted_catalog_api"] = "PASS"
            summary["server_url_used"] = f"http://127.0.0.1:{port}/"
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=4)
            stopped = proc.poll() is not None
        if not stopped:
            raise RehearsalFailure("rehearsal server did not stop cleanly")
        summary["checks"]["extracted_clean_stop"] = "PASS"
        server_text = ""
        try:
            server_text = server_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        if "Traceback (most recent call last)" in server_text:
            raise RehearsalFailure("extracted server log contains an unexpected traceback")
        summary["checks"]["server_log_clean"] = "PASS"
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
