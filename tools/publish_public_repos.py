#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Publish OpenShop to its two authorized external public mirrors.

This tool is intentionally narrow and fail-closed:
- only GitHub miaochunhui/openshop and Gitee sdfwefqaa/openshop are reachable;
- only local master and the annotated tag v0.2.0 are ever pushed;
- no force, mirror, delete, or unrelated branches/tags;
- tokens are read from operator-supplied external files and never logged;
- GitHub uses a per-command credential helper, not a persistent Git config;
- Gitee keeps the existing SSH login path untouched;
- mutating commands require --apply; every run writes evidence under a log root;
- no failed operation is retried automatically.

The tool was created for the 2026-10-13 OpenShop publication and is safe to keep
in-tree because it contains no secret values and rejects targets outside the two
hard-coded repositories.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat as statmod
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DEFAULT = Path("/hf3fs-jd/data/tmp/openshop-publication")
LIVE_SITE = "https://openshop.ai4everyone.top/"
TAG_NAME = "v0.2.0"
TAG_REF = "refs/tags/v0.2.0"
TAG_TARGET = "94c93261e637caa4f86669fc059edf5288bd27fe"
BRANCH_NAME = "master"
BRANCH_REF = "refs/heads/master"

GITHUB_OWNER = "miaochunhui"
GITHUB_REPO = "openshop"
GITHUB_ACCOUNT = "miaochunhui"
GITHUB_API = "https://api.github.com"
GITHUB_REPO_PATH = "/repos/miaochunhui/openshop"
GITHUB_HTML = "https://github.com/miaochunhui/openshop"
GITHUB_GIT_HTTPS = "https://github.com/miaochunhui/openshop.git"
GITHUB_GIT_PUSH = GITHUB_GIT_HTTPS
GITHUB_USER_AGENT = "openshop-public-repos/1.0"

GITEE_OWNER = "sdfwefqaa"
GITEE_REPO = "openshop"
GITEE_ACCOUNT = "sdfwefqaa"
GITEE_API = "https://gitee.com/api/v5"
GITEE_REPO_PATH = "/repos/sdfwefqaa/openshop"
GITEE_HTML = "https://gitee.com/sdfwefqaa/openshop"
GITEE_GIT_HTTPS = "https://gitee.com/sdfwefqaa/openshop.git"
GITEE_GIT_PUSH = "git@gitee.com:sdfwefqaa/openshop.git"

DESCRIPTION = (
    "OpenShop 开集 - open-source AI-mediated marketplace reference "
    "(Python stdlib + SQLite). Live demo: " + LIVE_SITE
)

CREATE_RECEIPT_KIND = "openshop-public-repos-create-receipt"
RECEIPT_VERSION = 1
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"

REDACTED = "***REDACTED***"
TOKEN_PIPE_ENV = "OPENSHOP_GH_CREDENTIAL_PIPE"
TOKEN_FILE_ENV = "OPENSHOP_GH_TOKEN_FILE"
_SECRETS: tuple[str, ...] = ()


class PublishError(RuntimeError):
    """A controlled publication failure with a secret-free message."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def set_secrets(values: list[str]) -> None:
    global _SECRETS
    _SECRETS = tuple(v for v in values if v)


def redact(value: str) -> str:
    text = value
    for secret in _SECRETS:
        text = text.replace(secret, REDACTED)
        quoted = urllib.parse.quote(secret, safe="")
        if quoted and quoted != secret:
            text = text.replace(quoted, REDACTED)
    text = re.sub(r"(?i)(access_token=)[^&\s'\"]+", r"\1" + REDACTED, text)
    text = re.sub(r"(?i)(authorization:\s*(?:bearer|token)\s+)\S+", r"\1" + REDACTED, text)
    return text


def redact_obj(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, dict):
        return {redact_obj(str(k)): redact_obj(v) for k, v in value.items()}
    return value


def run_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_TRACE", "GIT_CURL", "GIT_CONFIG_"))
        and key not in {"GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH", "GIT_SSH_COMMAND"}
    }
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if extra:
        env.update(extra)
    return env


class Evidence:
    def __init__(self, mode: str, evidence_root: str | Path):
        self.mode = mode
        self.started_at = utc_now()
        self.run_id = stamp() + "-" + mode
        root = Path(evidence_root).expanduser().resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
            (root / "logs").mkdir(parents=True, exist_ok=True)
            (root / "reports").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PublishError("cannot create evidence root: " + type(exc).__name__) from None
        self.log_path = root / "logs" / (self.run_id + ".log")
        self.report_path = root / "reports" / (self.run_id + ".json")
        self.root = root
        self.facts: dict = {}
        try:
            with self.log_path.open("x", encoding="utf-8") as handle:
                handle.write("run_id=" + self.run_id + "\n")
                handle.write("mode=" + mode + "\n")
                handle.write("started_at=" + self.started_at + "\n")
        except OSError as exc:
            raise PublishError("cannot create evidence log: " + type(exc).__name__) from None

    def emit(self, message: str) -> None:
        text = redact(str(message))
        print(text, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            raise PublishError("cannot append evidence log") from None

    def fact(self, key: str, value) -> None:
        self.facts[key] = redact_obj(value)

    def finish(self, verdict: str, rc: int, error: str | None = None) -> None:
        finished = utc_now()
        report = {
            "kind": "openshop-public-repos-run",
            "mode": self.mode,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": finished,
            "verdict": verdict,
            "rc": rc,
            "facts": self.facts,
        }
        if error:
            report["error"] = redact(error)
        line = "PUBLISH_RUN verdict=%s mode=%s rc=%s report=%s log=%s" % (
            verdict, self.mode, rc, self.report_path, self.log_path
        )
        try:
            self.report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            print("PUBLISH_RUN_SAVE_FAILED " + type(exc).__name__, file=sys.stderr)
        print(redact(line), flush=True)


def token_metadata(name: str, path_value: str) -> tuple[str, dict]:
    path = Path(path_value).expanduser()
    try:
        st = path.lstat()
    except OSError as exc:
        raise PublishError(f"{name} token file cannot be stat-ed: {type(exc).__name__}") from None
    if not statmod.S_ISREG(st.st_mode):
        raise PublishError(f"{name} token path is not a regular file")
    if st.st_size < 8 or st.st_size > 8192:
        raise PublishError(f"{name} token file size is outside the accepted range")
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise PublishError(f"{name} token file cannot be read as ASCII") from None
    if not value or any(ch.isspace() for ch in value):
        raise PublishError(f"{name} token value is empty or malformed")
    mode = statmod.S_IMODE(st.st_mode)
    meta = {
        "file": str(path),
        "mode": oct(mode),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "size": st.st_size,
        "inode": st.st_ino,
        "mtime_ns": st.st_mtime_ns,
        "broad_permissions": bool(mode & 0o077),
    }
    set_secrets(list(_SECRETS) + [value])
    return value, meta


def _git_raw(args: list[str], *, root: Path, env_extra: dict[str, str] | None = None,
             timeout: int = 180) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            env=run_child_env(env_extra),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishError("git transport failure: " + type(exc).__name__) from None
    return result


def git_text(args: list[str], *, root: Path, env_extra: dict[str, str] | None = None,
             timeout: int = 180) -> str:
    result = _git_raw(args, root=root, env_extra=env_extra, timeout=timeout)
    if result.returncode:
        stderr = redact(result.stderr.decode("utf-8", "replace"))[-3000:]
        raise PublishError("git " + " ".join(args[:2]) + f" rc={result.returncode}: " + stderr)
    return result.stdout.decode("utf-8", "replace")


def github_credential_helper(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {"get", "store", "erase"}:
        print("github credential helper: invalid operation", file=sys.stderr)
        return 1
    if argv[0] != "get":
        return 0
    if sys.stdout.isatty() or os.environ.get(TOKEN_PIPE_ENV) != "1":
        print("github credential helper: disabled outside the publisher pipe", file=sys.stderr)
        return 1
    token_path = os.environ.get(TOKEN_FILE_ENV, "")
    if not token_path:
        print("github credential helper: missing token path", file=sys.stderr)
        return 1
    fields: dict[str, str] = {}
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            break
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            print("github credential helper: invalid request", file=sys.stderr)
            return 1
        fields[key] = value
    expected_path = "miaochunhui/openshop.git"
    if (
        fields.get("protocol") != "https"
        or fields.get("host") != "github.com"
        or fields.get("path", "").lstrip("/").casefold() != expected_path
        or fields.get("username", "x-access-token") not in {"x-access-token", GITHUB_ACCOUNT}
    ):
        print("github credential helper: request outside authorized repository", file=sys.stderr)
        return 1
    try:
        token, _meta = token_metadata("github", token_path)
    except PublishError:
        print("github credential helper: token unavailable", file=sys.stderr)
        return 1
    sys.stdout.write("username=x-access-token\npassword=" + token + "\n\n")
    sys.stdout.flush()
    return 0


def github_git_options() -> list[str]:
    helper = "!" + shlex.quote(sys.executable) + " " + shlex.quote(str(Path(__file__).resolve())) + " github-credential"
    return [
        "-c", "credential.helper=",
        "-c", "credential.helper=" + helper,
        "-c", "credential.useHttpPath=true",
    ]


def github_git_env(token_path: str) -> dict[str, str]:
    return {
        TOKEN_FILE_ENV: str(Path(token_path).expanduser().resolve()),
        TOKEN_PIPE_ENV: "1",
    }


def clean_git_options() -> list[str]:
    return ["-c", "credential.helper="]


def parse_refs(output: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise PublishError("malformed git ls-remote output")
        oid, ref = fields
        if not re.fullmatch(r"[0-9a-f]{40}", oid):
            raise PublishError("malformed git remote object id")
        if ref in refs:
            raise PublishError("duplicate git remote ref")
        refs[ref] = oid
    return refs


def ls_remote(url: str, root: Path, options: list[str] | None = None,
              env_extra: dict[str, str] | None = None) -> dict[str, str]:
    args = [*(options or []), "ls-remote", url, BRANCH_REF, TAG_REF]
    return parse_refs(git_text(args, root=root, env_extra=env_extra, timeout=120))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _api_error_detail(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    try:
        obj = json.loads(text)
    except (ValueError, UnicodeError):
        obj = None
    if isinstance(obj, dict):
        for key in ("message", "error_description", "error", "detail"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                return redact(value)[:300]
    # Gitee variants sometimes return a short non-JSON error body. Keep only a
    # bounded, redacted excerpt in internal evidence; token literals are removed.
    return redact(" ".join(text.split()))[:300]


def http_json(label: str, method: str, url: str, *, headers: dict[str, str],
              data: bytes | None = None, allow_missing: bool = False):
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024)
            status = int(response.status)
            text = raw.decode("utf-8", "replace")
            parsed = json.loads(text) if text.strip() else {}
            if not isinstance(parsed, dict):
                raise PublishError(label + ": API returned non-object JSON")
            return status, parsed
    except urllib.error.HTTPError as error:
        if allow_missing and error.code == 404:
            return 404, {}
        detail = ""
        try:
            detail = _api_error_detail(error.read(64 * 1024))
        except OSError:
            pass
        message = label + " HTTP=" + str(error.code)
        if detail:
            message += " message=" + detail
        raise PublishError(message) from None
    except urllib.error.URLError as error:
        raise PublishError(label + ": transport failure " + type(error.reason).__name__) from None
    except (ValueError, UnicodeError):
        raise PublishError(label + ": malformed JSON response") from None


def github_api(method: str, path: str, token: str | None = None, payload: dict | None = None,
               *, allow_missing: bool = False):
    base = path.split("?", 1)[0]
    jobs = re.fullmatch(re.escape(GITHUB_REPO_PATH) + r"/actions/runs/[0-9]+/jobs", base)
    allowed = (
        (method, base) in {
            ("GET", "/user"),
            ("GET", GITHUB_REPO_PATH),
            ("PATCH", GITHUB_REPO_PATH),
            ("POST", "/user/repos"),
            ("GET", GITHUB_REPO_PATH + "/actions/runs"),
            ("GET", GITHUB_REPO_PATH + "/actions/permissions"),
        }
        or bool(jobs and method == "GET")
    )
    if not allowed:
        raise PublishError("GitHub endpoint outside authorized publication scope")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": GITHUB_USER_AGENT,
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return http_json(
        "GitHub " + method + " " + base,
        method,
        GITHUB_API + path,
        headers=headers,
        data=data,
        allow_missing=allow_missing,
    )


def gitee_api(method: str, path: str, token: str | None = None, form: dict | None = None,
              json_payload: dict | None = None, *, allow_missing: bool = False):
    allowed = {
        ("GET", "/user"),
        ("GET", GITEE_REPO_PATH),
        ("PATCH", GITEE_REPO_PATH),
        ("POST", "/user/repos"),
    }
    if (method, path) not in allowed:
        raise PublishError("Gitee endpoint outside authorized publication scope")
    if form is not None and json_payload is not None:
        raise PublishError("Gitee request cannot mix form and JSON bodies")
    headers = {"Accept": "application/json", "User-Agent": GITHUB_USER_AGENT}
    query = ""
    if token:
        query = "?" + urllib.parse.urlencode({"access_token": token})
    data = None
    if json_payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(json_payload, ensure_ascii=False).encode("utf-8")
    elif form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        data = urllib.parse.urlencode(form).encode("utf-8")
    return http_json(
        "Gitee " + method + " " + path,
        method,
        GITEE_API + path + query,
        headers=headers,
        data=data,
        allow_missing=allow_missing,
    )


def verify_github_account(token: str) -> dict:
    status, user = github_api("GET", "/user", token)
    if status != 200:
        raise PublishError("GitHub account check returned unexpected status")
    login = user.get("login")
    if not isinstance(login, str) or login.casefold() != GITHUB_ACCOUNT:
        raise PublishError("GitHub authenticated account is not the authorized account")
    return {"account": login, "identity_ok": True}


def verify_gitee_account(token: str) -> dict:
    status, user = gitee_api("GET", "/user", token)
    if status != 200:
        raise PublishError("Gitee account check returned unexpected status")
    login = user.get("login") or user.get("username")
    if not isinstance(login, str) or login.casefold() != GITEE_ACCOUNT:
        raise PublishError("Gitee authenticated account is not the authorized account")
    return {"account": login, "identity_ok": True}


def github_repo_state(token: str | None) -> dict:
    status, repo = github_api("GET", GITHUB_REPO_PATH, token, allow_missing=True)
    if status == 404:
        return {"status": 404, "state": "ABSENT"}
    if status != 200:
        raise PublishError("GitHub repository state returned unexpected status")
    full_name = str(repo.get("full_name", ""))
    if full_name.casefold() != (GITHUB_OWNER + "/" + GITHUB_REPO).casefold():
        raise PublishError("GitHub repository identity mismatch")
    return {
        "status": 200,
        "state": "VISIBLE",
        "id": repo.get("id"),
        "full_name": full_name,
        "private": repo.get("private"),
        "archived": repo.get("archived"),
        "disabled": repo.get("disabled"),
        "fork": repo.get("fork"),
        "default_branch": repo.get("default_branch"),
        "html_url": repo.get("html_url"),
        "permissions": repo.get("permissions") if isinstance(repo.get("permissions"), dict) else {},
    }


def gitee_repo_state(token: str | None) -> dict:
    status, repo = gitee_api("GET", GITEE_REPO_PATH, token, allow_missing=True)
    if status == 404:
        return {"status": 404, "state": "ABSENT"}
    if status != 200:
        raise PublishError("Gitee repository state returned unexpected status")
    full_name = str(repo.get("full_name") or repo.get("path_with_namespace") or "")
    if full_name.casefold() != (GITEE_OWNER + "/" + GITEE_REPO).casefold():
        raise PublishError("Gitee repository identity mismatch")
    return {
        "status": 200,
        "state": "VISIBLE",
        "id": repo.get("id"),
        "full_name": full_name,
        "private": repo.get("private"),
        "default_branch": repo.get("default_branch"),
        "html_url": repo.get("html_url"),
        "permission": repo.get("permission") if isinstance(repo.get("permission"), dict) else {},
    }


def local_state(root: Path) -> dict:
    top = Path(git_text(["rev-parse", "--show-toplevel"], root=root).strip()).resolve()
    if top != root.resolve():
        raise PublishError("repo root does not match the requested Git top-level")
    branch = git_text(["rev-parse", "--abbrev-ref", "HEAD"], root=root).strip()
    head = git_text(["rev-parse", "HEAD"], root=root).strip()
    status_text = git_text(["status", "--porcelain"], root=root)
    tag_type = git_text(["cat-file", "-t", TAG_REF], root=root).strip()
    if tag_type != "tag":
        raise PublishError("release tag is not annotated")
    tag_object = git_text(["rev-parse", TAG_REF], root=root).strip()
    tag_target = git_text(["rev-parse", TAG_REF + "^{commit}"], root=root).strip()
    if tag_target != TAG_TARGET:
        raise PublishError("release tag target differs from the authorized v0.2.0 commit")
    ancestor = _git_raw(
        ["merge-base", "--is-ancestor", TAG_TARGET, head],
        root=root,
    ).returncode == 0
    if not ancestor:
        raise PublishError("release tag commit is not an ancestor of current master")
    object_count = git_text(["count-objects", "-v"], root=root).strip().splitlines()
    return {
        "root": str(root.resolve()),
        "branch": branch,
        "head": head,
        "clean": not bool(status_text.strip()),
        "tag": TAG_NAME,
        "tag_type": tag_type,
        "tag_object": tag_object,
        "tag_target": tag_target,
        "tag_is_ancestor": ancestor,
        "git_count_objects": object_count[0] if object_count else "unknown",
    }


def exact_secret_scan(root: Path, tokens: list[str]) -> dict:
    secrets = [t.encode("ascii") for t in tokens if t]
    if len(secrets) != 2:
        raise PublishError("secret scan requires both credentials")
    revisions = [BRANCH_REF, TAG_REF]
    names = git_text(["rev-list", "--objects", *revisions], root=root).splitlines()
    object_ids = [line.split(" ", 1)[0] for line in names if line]
    if not object_ids:
        raise PublishError("secret scan found an empty object set")
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=str(root),
        env=run_child_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    scanned = 0
    tail_keep = max(len(secret) for secret in secrets) - 1
    try:
        for oid in object_ids:
            proc.stdin.write((oid + "\n").encode("ascii"))
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if len(header) != 3 or header[0].decode("ascii") != oid:
                raise PublishError("Git object scan returned an invalid header")
            size = int(header[2])
            remaining = size
            tail = b""
            while remaining:
                block = proc.stdout.read(min(remaining, 1024 * 1024))
                if not block:
                    raise PublishError("Git object scan hit a truncated object")
                window = tail + block
                if any(secret in window for secret in secrets):
                    raise PublishError("exact token literal found in Git objects; no value printed")
                tail = window[-tail_keep:] if tail_keep > 0 else b""
                remaining -= len(block)
            if proc.stdout.read(1) != b"\n":
                raise PublishError("Git object scan missing object delimiter")
            scanned += 1
        proc.stdin.close()
        stderr = proc.stderr.read()
        code = proc.wait(timeout=30)
        if code:
            raise PublishError("Git object scan failed with rc=" + str(code) + " stderr_bytes=" + str(len(stderr)))
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    worktree_names = git_text(
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        root=root,
    ).split("\0")
    worktree_files = [name for name in worktree_names if name]
    if not worktree_files:
        raise PublishError("worktree secret scan found an empty file set")
    for name in worktree_files:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise PublishError("worktree secret scan refuses a missing or symlink path: " + name)
        data = path.read_bytes()
        if any(secret in data for secret in secrets):
            raise PublishError("exact token literal found in worktree; no value printed")
    return {
        "objects": scanned,
        "worktree_files": len(worktree_files),
        "exact_credential_hits": 0,
    }


def push_refspecs() -> list[str]:
    return [BRANCH_REF + ":" + BRANCH_REF, TAG_REF + ":" + TAG_REF]


def remote_push(root: Path, provider: str, github_token_file: str) -> None:
    if provider == "gitee":
        result = _git_raw(
            [*clean_git_options(), "push", "--atomic", GITEE_GIT_PUSH, *push_refspecs()],
            root=root,
            timeout=300,
        )
    elif provider == "github":
        result = _git_raw(
            [*github_git_options(), "push", "--atomic", GITHUB_GIT_PUSH, *push_refspecs()],
            root=root,
            env_extra=github_git_env(github_token_file),
            timeout=300,
        )
    else:
        raise PublishError("unknown publication provider")
    if result.returncode:
        stderr = redact(result.stderr.decode("utf-8", "replace"))[-4000:]
        raise PublishError(provider + " git push failed rc=" + str(result.returncode) + ": " + stderr)
    output = (result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")).strip()
    if output:
        print(redact(output)[-2000:], flush=True)


def verify_public_refs(root: Path) -> dict:
    gitee = ls_remote(GITEE_GIT_HTTPS, root, clean_git_options())
    github = ls_remote(GITHUB_GIT_HTTPS, root, clean_git_options())
    local = {
        "master": git_text(["rev-parse", BRANCH_REF], root=root).strip(),
        "tag_object": git_text(["rev-parse", TAG_REF], root=root).strip(),
        "tag_target": git_text(["rev-parse", TAG_REF + "^{commit}"], root=root).strip(),
    }
    result = {"local": local, "gitee": {}, "github": {}}
    for name, refs in (("gitee", gitee), ("github", github)):
        master = refs.get(BRANCH_REF)
        tag_object = refs.get(TAG_REF)
        if master != local["master"]:
            raise PublishError(name + " public master ref does not match local master")
        if tag_object != local["tag_object"]:
            raise PublishError(name + " public tag ref does not match local annotated tag")
        result[name] = {
            "master": master,
            "tag_object": tag_object,
            "refs_seen": len(refs),
        }
    return result


def github_default_branch(token: str) -> dict:
    status, repo = github_api("GET", GITHUB_REPO_PATH, token)
    if status != 200:
        raise PublishError("GitHub repository state unavailable after push")
    default_branch = repo.get("default_branch")
    changed = False
    if default_branch != BRANCH_NAME:
        status, updated = github_api("PATCH", GITHUB_REPO_PATH, token, {"default_branch": BRANCH_NAME})
        if status != 200 or updated.get("default_branch") != BRANCH_NAME:
            raise PublishError("GitHub default branch could not be set to master")
        changed = True
    return {"default_branch": BRANCH_NAME, "changed": changed, "private": repo.get("private"), "html_url": repo.get("html_url")}


def ci_runs_for_sha(token: str, sha: str) -> list[dict]:
    path = GITHUB_REPO_PATH + "/actions/runs?head_sha=" + urllib.parse.quote(sha) + "&per_page=50"
    status, payload = github_api("GET", path, token)
    if status != 200:
        raise PublishError("GitHub Actions runs query failed")
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise PublishError("GitHub Actions runs payload malformed")
    selected = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("head_sha") != sha:
            continue
        if run.get("head_branch") != BRANCH_NAME:
            continue
        if run.get("event") != "push":
            continue
        selected.append({
            "id": run.get("id"),
            "name": run.get("name"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "html_url": run.get("html_url"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "run_attempt": run.get("run_attempt"),
            "path": run.get("path"),
        })
    return selected


def ci_jobs(token: str, run_id) -> list[dict]:
    if not isinstance(run_id, int):
        raise PublishError("GitHub run id malformed")
    status, payload = github_api(
        "GET", GITHUB_REPO_PATH + "/actions/runs/" + str(run_id) + "/jobs?per_page=100",
        token,
    )
    if status != 200:
        raise PublishError("GitHub Actions jobs query failed")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise PublishError("GitHub Actions jobs payload malformed")
    facts = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        failed_steps = [
            step.get("name") for step in steps
            if isinstance(step, dict) and step.get("conclusion") not in {"success", "skipped", None}
        ]
        facts.append({
            "id": job.get("id"),
            "name": job.get("name"),
            "status": job.get("status"),
            "conclusion": job.get("conclusion"),
            "html_url": job.get("html_url"),
            "failed_steps": failed_steps,
        })
    return facts


def repo_state_summary(state: dict) -> dict:
    keep = {"status", "state", "id", "full_name", "private", "default_branch", "html_url",
            "archived", "disabled", "fork", "permissions", "permission"}
    return {k: v for k, v in state.items() if k in keep}


def write_receipt(evidence: Evidence, facts: dict) -> Path:
    receipts = evidence.root / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    path = receipts / (evidence.run_id + "-create.json")
    path.write_text(json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_create_receipt(path_value: str) -> dict:
    path = Path(path_value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PublishError("create receipt cannot be read or parsed") from None
    if payload.get("kind") != CREATE_RECEIPT_KIND or payload.get("version") != RECEIPT_VERSION:
        raise PublishError("create receipt kind/version mismatch")
    if payload.get("complete") is not True:
        raise PublishError("create receipt is not complete")
    if payload.get("tag_target") != TAG_TARGET:
        raise PublishError("create receipt tag target mismatch")
    for provider in ("gitee", "github"):
        repo = payload.get("repositories", {}).get(provider)
        if not isinstance(repo, dict) or not isinstance(repo.get("id"), int):
            raise PublishError("create receipt provider identity missing: " + provider)
    return payload


def ensure_repos_match_receipt(receipt: dict, gitee_state: dict, github_state: dict) -> None:
    for provider, state in (("gitee", gitee_state), ("github", github_state)):
        expected = receipt["repositories"][provider]
        if state.get("state") != "VISIBLE":
            raise PublishError(provider + " repository is missing")
        if state.get("id") != expected.get("id"):
            raise PublishError(provider + " repository id differs from creation receipt")
        if state.get("private") is not False:
            raise PublishError(provider + " repository is not public")
        permissions = state.get("permissions") if provider == "github" else state.get("permission")
        if isinstance(permissions, dict):
            if permissions.get("push") is False:
                raise PublishError(provider + " repository has no push permission")


def preflight(args, evidence: Evidence, tokens: dict[str, str], metas: dict[str, dict]) -> dict:
    local = local_state(Path(args.repo_root))
    evidence.fact("local", local)
    evidence.emit("local branch=" + local["branch"] + " clean=" + str(local["clean"]) + " head=" + local["head"])
    evidence.emit("release tag v0.2.0 target=" + local["tag_target"] + " object=" + local["tag_object"])
    evidence.fact("tokens", metas)
    for name in ("gitee", "github"):
        if metas[name].get("broad_permissions"):
            evidence.emit("warning: " + name + " token file permissions are broader than 0600; not modified")
    gitee_account = verify_gitee_account(tokens["gitee"])
    github_account = verify_github_account(tokens["github"])
    evidence.emit("gitee account verified: " + gitee_account["account"])
    evidence.emit("github account verified: " + github_account["account"])
    gitee_state = gitee_repo_state(tokens["gitee"])
    github_state = github_repo_state(tokens["github"])
    evidence.fact("accounts", {"gitee": gitee_account, "github": github_account})
    evidence.fact("repositories", {
        "gitee": repo_state_summary(gitee_state),
        "github": repo_state_summary(github_state),
    })
    evidence.emit("gitee repository state=" + gitee_state["state"] + " id=" + str(gitee_state.get("id")))
    evidence.emit("github repository state=" + github_state["state"] + " id=" + str(github_state.get("id")))
    scan = exact_secret_scan(Path(args.repo_root), [tokens["gitee"], tokens["github"]])
    evidence.fact("secret_scan", scan)
    evidence.emit("token literal scan: objects=" + str(scan["objects"]) + " worktree_files=" + str(scan["worktree_files"]) + " hits=0")
    return {
        "local": local,
        "gitee_state": gitee_state,
        "github_state": github_state,
        "account": {"gitee": gitee_account, "github": github_account},
    }


def create_repositories(args, evidence: Evidence, tokens: dict[str, str], metas: dict[str, dict]) -> int:
    pre = preflight(args, evidence, tokens, metas)
    if pre["gitee_state"]["state"] != "ABSENT" or pre["github_state"]["state"] != "ABSENT":
        raise PublishError("at least one target repository already exists; refusing to create or overwrite")
    plan = {
        "gitee": {"action": "create public empty repository", "name": GITEE_OWNER + "/" + GITEE_REPO},
        "github": {"action": "create public empty repository", "name": GITHUB_OWNER + "/" + GITHUB_REPO},
    }
    evidence.fact("plan", plan)
    if not args.apply:
        evidence.fact("mutation_performed", False)
        evidence.emit("DRY_RUN create: both repositories absent; no API mutation performed")
        evidence.finish("DRY_RUN", 0)
        return 0
    created: dict[str, dict] = {}
    try:
        status, gitee_repo = gitee_api(
            "POST", "/user/repos", tokens["gitee"],
            json_payload={
                "name": GITEE_REPO,
                "path": GITEE_REPO,
                "description": DESCRIPTION,
                "homepage": LIVE_SITE,
                "private": False,
                "auto_init": False,
                "has_issues": True,
                "has_wiki": False,
            },
        )
        if status not in (200, 201):
            raise PublishError("Gitee create returned unexpected HTTP status")
        full_name = str(gitee_repo.get("full_name") or gitee_repo.get("path_with_namespace") or "")
        if full_name.casefold() != (GITEE_OWNER + "/" + GITEE_REPO).casefold():
            raise PublishError("Gitee created repository identity mismatch")
        created["gitee"] = repo_state_summary(gitee_repo)
        evidence.fact("created", created)
        if gitee_repo.get("private") is not False:
            raise PublishError("Gitee created repository is not public")
        evidence.emit("created public Gitee repository id=" + str(gitee_repo.get("id")))
        status, github_repo = github_api(
            "POST", "/user/repos", tokens["github"],
            {
                "name": GITHUB_REPO,
                "private": False,
                "auto_init": False,
                "description": DESCRIPTION,
                "homepage": LIVE_SITE.lstrip("/"),
                "has_issues": True,
                "has_wiki": False,
            },
        )
        if status != 201:
            raise PublishError("GitHub create returned unexpected HTTP status")
        if github_repo.get("private") is not False:
            raise PublishError("GitHub created repository is not public")
        if str(github_repo.get("full_name", "")).casefold() != (GITHUB_OWNER + "/" + GITHUB_REPO).casefold():
            raise PublishError("GitHub created repository identity mismatch")
        created["github"] = repo_state_summary(github_repo)
        evidence.emit("created public GitHub repository id=" + str(github_repo.get("id")))
    except PublishError:
        evidence.fact("created", created)
        partial = {
            "kind": CREATE_RECEIPT_KIND,
            "version": RECEIPT_VERSION,
            "complete": False,
            "created_at": utc_now(),
            "tag_target": TAG_TARGET,
            "pre_state": {"gitee": "ABSENT", "github": "ABSENT"},
            "repositories": created,
        }
        partial_path = evidence.root / "receipts" / (evidence.run_id + "-create-partial.json")
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        evidence.emit("partial creation receipt=" + str(partial_path))
        raise
    evidence.fact("created", created)
    live_gitee = gitee_repo_state(tokens["gitee"])
    live_github = github_repo_state(tokens["github"])
    final = {
        "kind": CREATE_RECEIPT_KIND,
        "version": RECEIPT_VERSION,
        "complete": True,
        "created_at": utc_now(),
        "pre_state": {"gitee": "ABSENT", "github": "ABSENT"},
        "tag_target": TAG_TARGET,
        "repositories": {
            "gitee": repo_state_summary(live_gitee),
            "github": repo_state_summary(live_github),
        },
        "local_at_create": pre["local"],
        "token_metadata": metas,
        "secret_scan": evidence.facts.get("secret_scan", {}),
    }
    receipt_path = evidence.root / "receipts" / (evidence.run_id + "-create.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence.fact("mutation_performed", True)
    evidence.fact("create_receipt", str(receipt_path))
    evidence.emit("create receipt=" + str(receipt_path))
    evidence.emit("CREATE_REPOSITORIES verdict=PASS gitee=public github=public receipt=" + str(receipt_path))
    evidence.finish("PASS", 0)
    return 0


def recover_create(args, evidence: Evidence, tokens: dict[str, str], metas: dict[str, dict]) -> int:
    """Repair the single known empty/private Gitee repo, then create GitHub.

    This is not a blind retry of create. It requires the exact live Gitee id,
    an empty repository, private=true, admin rights, GitHub still absent, and
    an explicit --apply. It changes only the new empty repo visibility, then
    creates the still-absent GitHub target and writes a complete receipt.
    """
    expected_id = int(args.expected_gitee_id)
    root = Path(args.repo_root)
    local = local_state(root)
    evidence.fact("local", local)
    evidence.fact("tokens", metas)
    evidence.fact("recovery_expected_gitee_id", expected_id)
    verify_gitee_account(tokens["gitee"])
    verify_github_account(tokens["github"])
    secret_scan = exact_secret_scan(root, [tokens["gitee"], tokens["github"]])
    evidence.fact("secret_scan", secret_scan)
    gitee_before = gitee_repo_state(tokens["gitee"])
    github_before = github_repo_state(tokens["github"])
    evidence.fact("before", {
        "gitee": repo_state_summary(gitee_before),
        "github": repo_state_summary(github_before),
    })
    if gitee_before.get("state") != "VISIBLE":
        raise PublishError("Gitee recovery target is absent")
    if gitee_before.get("id") != expected_id:
        raise PublishError("Gitee recovery target id does not match the explicit expected id")
    if gitee_before.get("private") is not True:
        raise PublishError("Gitee recovery target is not private; refusing unexpected state")
    if gitee_before.get("default_branch"):
        raise PublishError("Gitee recovery target is not empty")
    permission = gitee_before.get("permission") if isinstance(gitee_before.get("permission"), dict) else {}
    if permission.get("admin") is not True or permission.get("push") is not True:
        raise PublishError("Gitee recovery target lacks confirmed admin/push permission")
    if github_before.get("state") != "ABSENT":
        raise PublishError("GitHub target is not absent; refusing recovery that could overwrite it")
    try:
        before_refs = ls_remote(GITEE_GIT_PUSH, root)
    except PublishError:
        before_refs = {"read_error": "authenticated-empty-read-failed"}
    evidence.fact("gitee_refs_before", before_refs)
    if any(not isinstance(k, str) or k.startswith("refs/") for k in before_refs):
        raise PublishError("Gitee recovery target has refs; refusing visibility change")
    if not args.apply:
        evidence.fact("mutation_performed", False)
        evidence.emit("DRY_RUN recover-create: would set the verified new empty Gitee repo id=" + str(expected_id) + " public, then create GitHub. No writes performed.")
        evidence.finish("DRY_RUN", 0)
        return 0
    status, patched = gitee_api(
        "PATCH", GITEE_REPO_PATH, None,
        json_payload={
            "access_token": tokens["gitee"],
            "name": GITEE_REPO,
            "path": GITEE_REPO,
            "description": DESCRIPTION,
            "homepage": LIVE_SITE,
            "private": "false",
        },
    )
    if status not in (200, 201):
        raise PublishError("Gitee visibility PATCH returned unexpected status")
    if patched.get("id") != expected_id or patched.get("private") is not False:
        raise PublishError("Gitee visibility PATCH did not confirm public state")
    live_gitee = gitee_repo_state(tokens["gitee"])
    evidence.fact("gitee_after_patch", repo_state_summary(live_gitee))
    if live_gitee.get("id") != expected_id or live_gitee.get("private") is not False:
        raise PublishError("Gitee authenticated readback did not confirm public state")
    evidence.emit("Gitee new empty repo is now public by authenticated API readback; id=" + str(expected_id))
    time.sleep(2)
    anonymous_status = "UNVERIFIED"
    try:
        anonymous_refs = ls_remote(GITEE_GIT_HTTPS, root, clean_git_options())
        anonymous_status = "EMPTY_PUBLIC_READ_OK" if anonymous_refs == {} else "UNEXPECTED_REFS"
    except PublishError:
        anonymous_status = "EMPTY_PUBLIC_READ_UNAVAILABLE"
    evidence.fact("gitee_anonymous_after_patch", anonymous_status)
    evidence.emit("Gitee anonymous empty-repo readback: " + anonymous_status + " (final refs prove publicity after push)")
    github_absent = github_repo_state(tokens["github"])
    if github_absent.get("state") != "ABSENT":
        raise PublishError("GitHub target became non-empty while repairing Gitee; stopping")
    created: dict[str, dict] = {"gitee": repo_state_summary(live_gitee)}
    try:
        status, github_repo = github_api(
            "POST", "/user/repos", tokens["github"],
            {
                "name": GITHUB_REPO,
                "private": False,
                "auto_init": False,
                "description": DESCRIPTION,
                "homepage": LIVE_SITE,
                "has_issues": True,
                "has_wiki": False,
            },
        )
        if status != 201:
            raise PublishError("GitHub create returned unexpected HTTP status")
        full_name = str(github_repo.get("full_name", ""))
        if full_name.casefold() != (GITHUB_OWNER + "/" + GITHUB_REPO).casefold():
            raise PublishError("GitHub created repository identity mismatch")
        created["github"] = repo_state_summary(github_repo)
        if github_repo.get("private") is not False:
            raise PublishError("GitHub created repository is not public")
    except PublishError:
        evidence.fact("created", created)
        partial_path = evidence.root / "receipts" / (evidence.run_id + "-recover-create-partial.json")
        partial = {
            "kind": CREATE_RECEIPT_KIND,
            "version": RECEIPT_VERSION,
            "complete": False,
            "created_at": utc_now(),
            "tag_target": TAG_TARGET,
            "pre_state": {"gitee": "ABSENT_AT_INITIAL_CREATE", "github": "ABSENT"},
            "repositories": created,
            "recovery": True,
            "recovery_expected_gitee_id": expected_id,
        }
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        evidence.emit("partial recovery receipt=" + str(partial_path))
        raise
    live_github = github_repo_state(tokens["github"])
    evidence.fact("created", {
        "gitee": repo_state_summary(live_gitee),
        "github": repo_state_summary(live_github),
    })
    evidence.emit("GitHub public repository created; id=" + str(live_github.get("id")))
    final = {
        "kind": CREATE_RECEIPT_KIND,
        "version": RECEIPT_VERSION,
        "complete": True,
        "created_at": utc_now(),
        "pre_state": {"gitee": "ABSENT", "github": "ABSENT"},
        "tag_target": TAG_TARGET,
        "repositories": {
            "gitee": repo_state_summary(live_gitee),
            "github": repo_state_summary(live_github),
        },
        "local_at_create": local,
        "token_metadata": metas,
        "secret_scan": secret_scan,
        "recovery": {
            "gitee_id": expected_id,
            "initial_create_defect": "The first Gitee POST created the new repository private despite private=false in the form payload. The tool stopped before GitHub creation.",
            "repaired_by": "explicit JSON PATCH private=false after verifying empty/private/admin state and explicit id",
            "github_was_absent_before_recovery_create": True,
        },
    }
    receipt_path = evidence.root / "receipts" / (evidence.run_id + "-create.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence.fact("mutation_performed", True)
    evidence.fact("create_receipt", str(receipt_path))
    evidence.emit("create receipt=" + str(receipt_path))
    evidence.emit("RECOVER_CREATE verdict=PASS gitee=public github=public receipt=" + str(receipt_path))
    evidence.finish("PASS", 0)
    return 0


def launch_publication(args, evidence: Evidence, tokens: dict[str, str], metas: dict[str, dict]) -> int:
    """Final initial-publication sequence for the known partial Gitee state.

    Gitee rejects public visibility for an empty repository (server error 422),
    so the proven order is:
      validate new private/empty Gitee id -> create GitHub public ->
      push master+tag to private Gitee -> push master+tag to GitHub ->
      make the now-nonempty Gitee repository public -> verify both publicly.
    This command requires a clean committed master and the annotated v0.2.0 tag.
    It never force-pushes, deletes, retries a failed write, or touches other repos.
    """
    expected_id = int(args.expected_gitee_id)
    root = Path(args.repo_root)
    local = local_state(root)
    evidence.fact("local", local)
    evidence.fact("tokens", metas)
    evidence.fact("expected_gitee_id", expected_id)
    if local["branch"] != BRANCH_NAME:
        raise PublishError("launch requires current branch master")
    if not local["clean"]:
        raise PublishError("launch requires a clean committed working tree")
    verify_gitee_account(tokens["gitee"])
    verify_github_account(tokens["github"])
    secret_scan = exact_secret_scan(root, [tokens["gitee"], tokens["github"]])
    evidence.fact("secret_scan", secret_scan)
    evidence.emit("pre-launch token literal scan: objects=" + str(secret_scan["objects"]) + " worktree_files=" + str(secret_scan["worktree_files"]) + " hits=0")
    gitee_before = gitee_repo_state(tokens["gitee"])
    github_before = github_repo_state(tokens["github"])
    evidence.fact("before", {
        "gitee": repo_state_summary(gitee_before),
        "github": repo_state_summary(github_before),
    })
    evidence.emit("pre-launch Gitee state=" + gitee_before["state"] + " id=" + str(gitee_before.get("id")) + " private=" + str(gitee_before.get("private")))
    evidence.emit("pre-launch GitHub state=" + github_before["state"])
    if gitee_before.get("state") != "VISIBLE":
        raise PublishError("Gitee launch target is absent")
    if gitee_before.get("id") != expected_id:
        raise PublishError("Gitee launch target id differs from the explicit expected id")
    if gitee_before.get("private") is not True:
        raise PublishError("Gitee launch target must be private before the content push")
    if gitee_before.get("default_branch"):
        raise PublishError("Gitee launch target is not an empty repository")
    permission = gitee_before.get("permission") if isinstance(gitee_before.get("permission"), dict) else {}
    if permission.get("admin") is not True or permission.get("push") is not True:
        raise PublishError("Gitee launch target lacks confirmed admin/push permission")
    if github_before.get("state") != "ABSENT":
        raise PublishError("GitHub launch target is not absent; refusing to overwrite")
    refs_before = {}
    try:
        refs_before = ls_remote(GITEE_GIT_PUSH, root)
    except PublishError:
        refs_before = {"read_error": "empty-private-read-unavailable"}
    evidence.fact("gitee_refs_before", refs_before)
    if any(isinstance(k, str) and k.startswith("refs/") for k in refs_before):
        raise PublishError("Gitee launch target already has refs; refusing initial launch")
    plan = {
        "gitee": "create content while private, then PATCH private=false because Gitee rejects empty public repos",
        "github": "create public empty repository, then push",
        "refs": push_refspecs(),
        "head": local["head"],
        "tag_object": local["tag_object"],
        "force": False,
    }
    evidence.fact("plan", plan)
    if not args.apply:
        evidence.fact("mutation_performed", False)
        evidence.emit("DRY_RUN launch: no repository creation, public change or push performed")
        evidence.finish("DRY_RUN", 0)
        return 0
    current_receipt_path = None
    try:
        status, github_repo = github_api(
            "POST", "/user/repos", tokens["github"],
            {
                "name": GITHUB_REPO,
                "private": False,
                "auto_init": False,
                "description": DESCRIPTION,
                "homepage": LIVE_SITE,
                "has_issues": True,
                "has_wiki": False,
            },
        )
        if status != 201:
            raise PublishError("GitHub create returned unexpected HTTP status")
        if github_repo.get("private") is not False:
            raise PublishError("GitHub created repository is not public")
        if str(github_repo.get("full_name", "")).casefold() != (GITHUB_OWNER + "/" + GITHUB_REPO).casefold():
            raise PublishError("GitHub created repository identity mismatch")
        github_id = github_repo.get("id")
        evidence.fact("github_created", repo_state_summary(github_repo))
        evidence.emit("created GitHub public empty repository id=" + str(github_id))
        evidence.emit("pushing master+tag to private Gitee first")
        remote_push(root, "gitee", args.github_token_file)
        gitee_refs_pushed = ls_remote(GITEE_GIT_PUSH, root)
        if (gitee_refs_pushed.get(BRANCH_REF) != local["head"]
                or gitee_refs_pushed.get(TAG_REF) != local["tag_object"]):
            raise PublishError("Gitee authenticated refs mismatch after content push")
        evidence.fact("gitee_refs_after_push", gitee_refs_pushed)
        evidence.emit("pushing master+tag to public GitHub")
        remote_push(root, "github", args.github_token_file)
        github_refs_pushed = ls_remote(
            GITHUB_GIT_PUSH, root, github_git_options(),
            github_git_env(args.github_token_file),
        )
        if (github_refs_pushed.get(BRANCH_REF) != local["head"]
                or github_refs_pushed.get(TAG_REF) != local["tag_object"]):
            raise PublishError("GitHub authenticated refs mismatch after content push")
        evidence.fact("github_refs_after_push", github_refs_pushed)
        evidence.emit("GitHub content push completed; public repo has source")
        evidence.emit("setting the now-nonempty Gitee repository to public")
        status, patched = gitee_api(
            "PATCH", GITEE_REPO_PATH, None,
            json_payload={
                "access_token": tokens["gitee"],
                "name": GITEE_REPO,
                "path": GITEE_REPO,
                "description": DESCRIPTION,
                "homepage": LIVE_SITE,
                "private": "false",
            },
        )
        if status not in (200, 201):
            raise PublishError("Gitee visibility PATCH returned unexpected status after content push")
        if patched.get("id") != expected_id:
            raise PublishError("Gitee visibility PATCH returned an unexpected repository id")
        live_gitee = gitee_repo_state(tokens["gitee"])
        evidence.fact("gitee_after_visibility_patch", repo_state_summary(live_gitee))
        if live_gitee.get("id") != expected_id or live_gitee.get("private") is not False:
            raise PublishError("Gitee authenticated readback did not confirm public state")
        evidence.emit("Gitee is now public by authenticated API readback")
        default = github_default_branch(tokens["github"])
        evidence.fact("github_default_branch", default)
        evidence.emit("waiting 2 seconds before anonymous public readback")
        time.sleep(2)
        public = verify_public_refs(root)
        evidence.fact("public_refs", public)
        live_github = github_repo_state(tokens["github"])
        if live_github.get("state") != "VISIBLE" or live_github.get("private") is not False:
            raise PublishError("GitHub authenticated readback does not confirm public state")
        gitee_public_api_status = "UNVERIFIED"
        try:
            gitee_public_status, gitee_public = gitee_api("GET", GITEE_REPO_PATH, None, allow_missing=True)
            gitee_public_api_status = str(gitee_public_status)
            if gitee_public_status == 200 and gitee_public.get("private") is not False:
                raise PublishError("Gitee anonymous API reports a non-public repository")
        except PublishError as exc:
            if gitee_public_api_status == "UNVERIFIED":
                evidence.emit("Gitee anonymous API readback unavailable; anonymous Git refs already verified")
            else:
                raise
        evidence.fact("gitee_anonymous_api", gitee_public_api_status)
        create_final = {
            "kind": CREATE_RECEIPT_KIND,
            "version": RECEIPT_VERSION,
            "complete": True,
            "created_at": utc_now(),
            "pre_state": {
                "gitee": "NEW_PRIVATE_EMPTY_ID_" + str(expected_id),
                "github": "ABSENT",
            },
            "tag_target": TAG_TARGET,
            "repositories": {
                "gitee": repo_state_summary(live_gitee),
                "github": repo_state_summary(live_github),
            },
            "local_at_create": local,
            "token_metadata": metas,
            "secret_scan": secret_scan,
            "initial_launch": {
                "sequence": "github_create, gitee_private_push, github_push, gitee_visibility_public",
                "gitee_empty_public_rejected": "Gitee service returns 422 for empty public repositories; content was pushed before PATCH private=false",
                "gitee_refs_after_push": gitee_refs_pushed,
                "github_refs_after_push": github_refs_pushed,
            },
        }
        current_receipt_path = evidence.root / "receipts" / (evidence.run_id + "-create.json")
        current_receipt_path.parent.mkdir(parents=True, exist_ok=True)
        current_receipt_path.write_text(json.dumps(create_final, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        evidence.fact("create_receipt", str(current_receipt_path))
        push_final = {
            "kind": "openshop-public-repos-push-receipt",
            "version": RECEIPT_VERSION,
            "complete": True,
            "pushed_at": utc_now(),
            "repo_root": str(root.resolve()),
            "master": local["head"],
            "tag_object": local["tag_object"],
            "tag_target": local["tag_target"],
            "remote_refs": {
                "gitee": {"master": local["head"], "tag_object": local["tag_object"]},
                "github": {"master": local["head"], "tag_object": local["tag_object"]},
            },
            "public_readback": public,
            "create_receipt": str(current_receipt_path),
            "secret_scan": secret_scan,
        }
        push_path = evidence.root / "receipts" / (evidence.run_id + "-push.json")
        push_path.write_text(json.dumps(push_final, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        evidence.fact("push_receipt", str(push_path))
        evidence.fact("mutation_performed", True)
        evidence.emit("create receipt=" + str(current_receipt_path))
        evidence.emit("push receipt=" + str(push_path))
        evidence.emit("LAUNCH verdict=PASS gitee=" + GITEE_HTML + " github=" + GITHUB_HTML + " master=" + local["head"])
        evidence.finish("PASS", 0)
        return 0
    except PublishError:
        partial_gitee = {}
        partial_github = {}
        try:
            partial_gitee = repo_state_summary(gitee_repo_state(tokens["gitee"]))
        except PublishError:
            partial_gitee = {"state": "UNREADABLE"}
        try:
            partial_github = repo_state_summary(github_repo_state(tokens["github"]))
        except PublishError:
            partial_github = {"state": "UNREADABLE"}
        evidence.fact("partial_state", {"gitee": partial_gitee, "github": partial_github})
        partial_path = evidence.root / "receipts" / (evidence.run_id + "-launch-partial.json")
        partial = {
            "kind": "openshop-public-repos-launch-partial",
            "version": RECEIPT_VERSION,
            "complete": False,
            "captured_at": utc_now(),
            "gitee": partial_gitee,
            "github": partial_github,
            "local": local,
            "note": "A launch write failed; inspect provider state before any recovery. No automatic retry.",
        }
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_text(json.dumps(partial, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if current_receipt_path is None:
            evidence.emit("partial launch receipt=" + str(partial_path))
        raise


def push_repositories(args, evidence: Evidence, tokens: dict[str, str], metas: dict[str, dict]) -> int:
    receipt = load_create_receipt(args.create_receipt)
    local = local_state(Path(args.repo_root))
    evidence.fact("local", local)
    if local["branch"] != BRANCH_NAME:
        raise PublishError("push requires current branch master")
    if not local["clean"]:
        raise PublishError("push requires a clean working tree")
    evidence.fact("tokens", metas)
    if metas["gitee"].get("broad_permissions") or metas["github"].get("broad_permissions"):
        evidence.emit("warning: a token file has permissions broader than 0600; not modified")
    verify_gitee_account(tokens["gitee"])
    verify_github_account(tokens["github"])
    gitee_state = gitee_repo_state(tokens["gitee"])
    github_state = github_repo_state(tokens["github"])
    ensure_repos_match_receipt(receipt, gitee_state, github_state)
    evidence.fact("repositories_before_push", {
        "gitee": repo_state_summary(gitee_state),
        "github": repo_state_summary(github_state),
    })
    secret_scan = exact_secret_scan(Path(args.repo_root), [tokens["gitee"], tokens["github"]])
    evidence.fact("secret_scan", secret_scan)
    evidence.emit("pre-push token literal scan: objects=" + str(secret_scan["objects"]) + " worktree_files=" + str(secret_scan["worktree_files"]) + " hits=0")
    root = Path(args.repo_root)
    refs_before = {
        "gitee": ls_remote(GITEE_GIT_PUSH, root),
        "github": ls_remote(GITHUB_GIT_PUSH, root, github_git_options(), github_git_env(args.github_token_file)),
    }
    evidence.fact("remote_refs_before", refs_before)
    local_tag_object = local["tag_object"]
    local_master = local["head"]
    for provider, refs in refs_before.items():
        remote_master = refs.get(BRANCH_REF)
        remote_tag = refs.get(TAG_REF)
        if remote_tag and remote_tag != local_tag_object:
            raise PublishError(provider + " remote tag exists with a different object; refusing no-force publication")
        if remote_master:
            ancestor = _git_raw(["merge-base", "--is-ancestor", remote_master, local_master], root=root).returncode == 0
            if not ancestor:
                raise PublishError(provider + " remote master has diverged; refusing non-fast-forward publication")
    if not args.apply:
        evidence.fact("mutation_performed", False)
        evidence.fact("push_plan", {"specs": push_refspecs(), "head": local_master, "tag_object": local_tag_object})
        evidence.emit("DRY_RUN push: master=" + local_master + " tag_object=" + local_tag_object + "; no refs changed")
        evidence.finish("DRY_RUN", 0)
        return 0
    pushed = {}
    for provider in ("gitee", "github"):
        evidence.emit("pushing " + provider + " master and annotated tag v0.2.0")
        remote_push(root, provider, args.github_token_file)
        remote_refs = ls_remote(
            GITEE_GIT_PUSH if provider == "gitee" else GITHUB_GIT_PUSH,
            root,
            None if provider == "gitee" else github_git_options(),
            None if provider == "gitee" else github_git_env(args.github_token_file),
        )
        if remote_refs.get(BRANCH_REF) != local_master or remote_refs.get(TAG_REF) != local_tag_object:
            raise PublishError(provider + " authenticated ref readback mismatch")
        pushed[provider] = {
            "master": remote_refs[BRANCH_REF],
            "tag_object": remote_refs[TAG_REF],
            "refs": remote_refs,
        }
        evidence.fact("pushed", pushed)
        evidence.emit(provider + " authenticated refs verified")
    evidence.emit("waiting 2 seconds before anonymous public readback")
    time.sleep(2)
    public = verify_public_refs(root)
    evidence.fact("public_refs", public)
    default = github_default_branch(tokens["github"])
    evidence.fact("github_default_branch", default)
    pushed_receipt = {
        "kind": "openshop-public-repos-push-receipt",
        "version": RECEIPT_VERSION,
        "complete": True,
        "pushed_at": utc_now(),
        "repo_root": str(root.resolve()),
        "master": local_master,
        "tag_object": local_tag_object,
        "tag_target": local["tag_target"],
        "remote_refs": pushed,
        "public_readback": public,
        "create_receipt": str(Path(args.create_receipt).resolve()),
        "secret_scan": secret_scan,
    }
    push_receipt = evidence.root / "receipts" / (evidence.run_id + "-push.json")
    push_receipt.write_text(json.dumps(pushed_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence.fact("mutation_performed", True)
    evidence.fact("push_receipt", str(push_receipt))
    evidence.emit("push receipt=" + str(push_receipt))
    evidence.emit("PUSH_REPOSITORIES verdict=PASS gitee=public github=public master=" + local_master + " tag=" + local_tag_object)
    evidence.finish("PASS", 0)
    return 0


def verify_repositories(args, evidence: Evidence, _tokens, _metas) -> int:
    local = local_state(Path(args.repo_root))
    evidence.fact("local", local)
    evidence.emit("anonymous public verification for master=" + local["head"] + " and tag v0.2.0")
    public = verify_public_refs(Path(args.repo_root))
    evidence.fact("public_refs", public)
    status, gh_public = github_api("GET", GITHUB_REPO_PATH, None)
    evidence.fact("github_public_api", {
        "status": status,
        "private": gh_public.get("private"),
        "default_branch": gh_public.get("default_branch"),
        "html_url": gh_public.get("html_url"),
        "full_name": gh_public.get("full_name"),
    })
    if status != 200 or gh_public.get("private") is not False:
        raise PublishError("GitHub anonymous API does not confirm a public repository")
    gitee_note = "ANONYMOUS_API_UNAVAILABLE_ANONYMOUS_GIT_REFS_VERIFIED"
    try:
        gitee_status, gitee_public = gitee_api("GET", GITEE_REPO_PATH, None, allow_missing=True)
    except PublishError:
        evidence.fact("gitee_public_api", {"status": "UNAVAILABLE", "note": gitee_note})
    else:
        evidence.fact("gitee_public_api", {
            "status": gitee_status,
            "private": gitee_public.get("private"),
            "default_branch": gitee_public.get("default_branch"),
            "html_url": gitee_public.get("html_url"),
        })
        if gitee_status == 200:
            if gitee_public.get("private") is not False:
                raise PublishError("Gitee anonymous API reports a non-public repository")
            gitee_note = "ANONYMOUS_API_CONFIRMS_PUBLIC"
        else:
            gitee_note = "ANONYMOUS_API_HTTP_" + str(gitee_status) + "_ANONYMOUS_GIT_REFS_VERIFIED"
    evidence.fact("gitee_anonymous_api_note", gitee_note)
    if gitee_note != "ANONYMOUS_API_CONFIRMS_PUBLIC":
        evidence.emit("warning: Gitee anonymous API did not independently confirm visibility; anonymous Git refs for both providers already matched")
    evidence.emit("PUBLIC_REPOSITORIES_VERIFY verdict=PASS gitee=" + GITEE_HTML + " github=" + GITHUB_HTML + " gitee_api=" + gitee_note)
    evidence.finish("PASS", 0)
    return 0


def ci_wait(args, evidence: Evidence, tokens: dict[str, str], _metas: dict[str, dict]) -> int:
    root = Path(args.repo_root)
    head = git_text(["rev-parse", BRANCH_REF], root=root).strip()
    evidence.fact("head", head)
    evidence.fact("workflow", CI_WORKFLOW_PATH)
    timeout = max(10, int(args.timeout))
    interval = max(5, min(30, int(args.interval)))
    deadline = time.monotonic() + timeout
    last_runs: list[dict] = []
    while True:
        runs = ci_runs_for_sha(tokens["github"], head)
        if runs != last_runs:
            evidence.fact("runs", runs)
            for run in runs:
                evidence.emit("run id=%s status=%s conclusion=%s url=%s" % (
                    run.get("id"), run.get("status"), run.get("conclusion"), run.get("html_url")
                ))
            last_runs = runs
        if runs:
            completed = [run for run in runs if run.get("status") == "completed"]
            failed = [run for run in completed if run.get("conclusion") != "success"]
            if failed:
                evidence.fact("completed_runs", completed)
                raise PublishError("GitHub Actions completed without success; inspect run URL in report")
            if len(completed) == len(runs):
                all_jobs = []
                pending_jobs = False
                total_jobs = 0
                for run in runs:
                    jobs = ci_jobs(tokens["github"], run.get("id"))
                    all_jobs.append({"run_id": run.get("id"), "jobs": jobs})
                    total_jobs += len(jobs)
                    for job in jobs:
                        if job.get("status") == "completed" and job.get("conclusion") != "success":
                            evidence.fact("jobs", all_jobs)
                            raise PublishError("GitHub Actions job did not succeed")
                        if job.get("status") != "completed":
                            pending_jobs = True
                evidence.fact("jobs_in_progress", all_jobs)
                if total_jobs > 0 and not pending_jobs:
                    evidence.fact("completed_runs", runs)
                    run_urls = [run.get("html_url") for run in runs]
                    evidence.emit("CI_WAIT verdict=PASS runs=" + str(len(runs)) + " jobs=" + str(total_jobs) + " url=" + ",".join(str(url) for url in run_urls))
                    evidence.finish("PASS", 0)
                    return 0
                if total_jobs == 0:
                    evidence.emit("CI run completed but no jobs visible yet; waiting for job metadata")
        if time.monotonic() >= deadline:
            evidence.fact("actions_permissions_status", "UNVERIFIED_TIMEOUT")
            raise PublishError("GitHub Actions did not reach a successful terminal state before timeout")
        evidence.emit("CI still pending; waiting " + str(interval) + " seconds")
        time.sleep(interval)


def rollback_plan(args, evidence: Evidence, tokens: dict[str, str], metas: dict[str, dict]) -> int:
    receipt = load_create_receipt(args.create_receipt)
    verify_gitee_account(tokens["gitee"])
    verify_github_account(tokens["github"])
    gitee_state = gitee_repo_state(tokens["gitee"])
    github_state = github_repo_state(tokens["github"])
    ensure_repos_match_receipt(receipt, gitee_state, github_state)
    plan = {
        "scope": "READ_ONLY_ROLLBACK_PLAN_NOT_EXECUTED",
        "new_resources_only": True,
        "pre_state": receipt.get("pre_state"),
        "gitee": {
            "repository": GITEE_HTML,
            "id": gitee_state.get("id"),
            "rollback": "Make private or delete this newly created repository via Gitee repository settings; no existing Gitee repo was modified.",
            "provider_settings": GITEE_HTML + "/settings",
            "permissions": gitee_state.get("permission"),
        },
        "github": {
            "repository": GITHUB_HTML,
            "id": github_state.get("id"),
            "rollback": "Make private or delete this newly created repository via GitHub repository settings; no existing GitHub repo was modified.",
            "provider_settings": GITHUB_HTML + "/settings",
            "permissions": github_state.get("permissions"),
        },
        "local": {
            "source_and_tag_remain_local": True,
            "live_site_unaffected": True,
            "tag_target": receipt.get("tag_target"),
        },
        "execution_note": "No destructive action was performed. This plan verifies live repo ids against the create receipt and confirms both targets are new public resources.",
    }
    evidence.fact("rollback_plan", plan)
    evidence.fact("token_metadata", metas)
    evidence.emit("ROLLBACK_PLAN verdict=PASS gitee_id=" + str(gitee_state.get("id")) + " github_id=" + str(github_state.get("id")))
    evidence.emit("Both targets were created new by this publication; deletion/private setting via provider UI reverts external exposure. No deletion executed.")
    evidence.finish("PASS", 0)
    return 0


def run_selftest() -> int:
    failures = 0
    checks = 0
    negative = 0
    with tempfile.TemporaryDirectory(prefix="openshop-publish-selftest-") as tmp:
        token_file = Path(tmp) / "fake-token"
        fake = "selftest-token-value-not-a-real-credential"
        token_file.write_text(fake + "\n", encoding="ascii")
        token_file.chmod(0o600)
        checks += 1
        try:
            token, meta = token_metadata("selftest", str(token_file))
            if token != fake or fake in json.dumps(meta):
                raise AssertionError()
        except (PublishError, AssertionError):
            failures += 1
        set_secrets([fake])
        checks += 1
        if fake in redact("value=" + fake):
            failures += 1
        checks += 1
        encoded = urllib.parse.quote(fake)
        if encoded in redact("value=" + encoded):
            failures += 1
        env = dict(os.environ)
        env.update({TOKEN_FILE_ENV: str(token_file), TOKEN_PIPE_ENV: "1", "PYTHONDONTWRITEBYTECODE": "1"})
        correct_req = "protocol=https\nhost=github.com\npath=miaochunhui/openshop.git\n\n"
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "github-credential", "get"],
            input=correct_req.encode(), capture_output=True, env=env, timeout=30, check=False,
        )
        checks += 1
        if process.returncode != 0 or (fake + "\n") not in process.stdout.decode("utf-8", "replace") or fake in process.stderr.decode():
            failures += 1
        wrong_req = "protocol=https\nhost=github.com\npath=miaochunhui/other.git\n\n"
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "github-credential", "get"],
            input=wrong_req.encode(), capture_output=True, env=env, timeout=30, check=False,
        )
        negative += 1
        if process.returncode == 0 or fake in process.stdout.decode("utf-8", "replace") or fake in process.stderr.decode():
            failures += 1
        no_pipe_env = dict(env)
        no_pipe_env.pop(TOKEN_PIPE_ENV)
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "github-credential", "get"],
            input=correct_req.encode(), capture_output=True, env=no_pipe_env, timeout=30, check=False,
        )
        negative += 1
        if process.returncode == 0 or fake in process.stdout.decode("utf-8", "replace") or fake in process.stderr.decode():
            failures += 1
        refs = {"refs/heads/master": "a" * 40, "refs/tags/v0.2.0": "b" * 40}
        checks += 1
        try:
            parsed = parse_refs("".join(v + "\t" + k + "\n" for k, v in refs.items()))
            if parsed != refs:
                failures += 1
        except PublishError:
            failures += 1
        specs = push_refspecs()
        checks += 1
        if specs != [BRANCH_REF + ":" + BRANCH_REF, TAG_REF + ":" + TAG_REF] or any("force" in s for s in specs):
            failures += 1
        checks += 1
        if TAG_TARGET != "94c93261e637caa4f86669fc059edf5288bd27fe":
            failures += 1
    verdict = "PASS" if not failures else "FAIL"
    print("PUBLISH_TOOL_SELFTEST verdict=%s checks=%d negative_controls=%d failures=%d" % (
        verdict, checks, negative, failures
    ))
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo-root", default=str(ROOT))
    common.add_argument("--evidence-root", default=str(EVIDENCE_DEFAULT))
    common.add_argument("--github-token-file", required=False)
    common.add_argument("--gitee-token-file", required=False)
    parser = argparse.ArgumentParser(
        description="Publish OpenShop master and v0.2.0 to Gitee/GitHub, with no force and no implicit retry."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest", help="offline security and refspec checks")
    sub.add_parser("github-credential", help=argparse.SUPPRESS)
    pre = sub.add_parser("preflight", parents=[common], help="read-only account, target and secret preflight")
    launch = sub.add_parser("launch", parents=[common], help="create GitHub, push to both new repositories, publicize nonempty Gitee, verify")
    launch.add_argument("--apply", action="store_true", help="perform remote writes; default is dry-run")
    launch.add_argument("--expected-gitee-id", type=int, required=True)
    push = sub.add_parser("push", parents=[common], help="fast-forward update master+v0.2.0 on the two public repositories")
    push.add_argument("--apply", action="store_true", help="perform remote writes; default is dry-run")
    push.add_argument("--create-receipt", required=True)
    verify = sub.add_parser("verify", parents=[common], help="anonymous public repository and refs verification")
    ciw = sub.add_parser("ci-wait", parents=[common], help="poll GitHub Actions to a terminal result")
    ciw.add_argument("--timeout", type=int, default=1200)
    ciw.add_argument("--interval", type=int, default=10)
    rollback = sub.add_parser("rollback-plan", parents=[common], help="read-only rollback plan for newly created repos")
    rollback.add_argument("--create-receipt", required=True)
    return parser


def load_tokens(args, evidence: Evidence) -> tuple[dict[str, str], dict[str, dict]]:
    if not args.github_token_file or not args.gitee_token_file:
        raise PublishError("both --github-token-file and --gitee-token-file are required")
    gitee_token, gitee_meta = token_metadata("gitee", args.gitee_token_file)
    github_token, github_meta = token_metadata("github", args.github_token_file)
    tokens = {"gitee": gitee_token, "github": github_token}
    metas = {"gitee": gitee_meta, "github": github_meta}
    evidence.fact("token_metadata", metas)
    return tokens, metas


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "github-credential":
        return github_credential_helper(argv[1:])
    if argv == ["selftest"]:
        return run_selftest()
    parser = build_parser()
    args = parser.parse_args(argv)
    needs_tokens = args.command in {"preflight", "launch", "push", "ci-wait", "rollback-plan"}
    evidence = Evidence(args.command, args.evidence_root)
    try:
        if args.command == "verify":
            return verify_repositories(args, evidence, {}, {})
        if needs_tokens:
            tokens, metas = load_tokens(args, evidence)
        else:
            tokens, metas = {}, {}
        if args.command == "preflight":
            preflight(args, evidence, tokens, metas)
            evidence.emit("PREFLIGHT verdict=PASS remote_mutation=false")
            evidence.finish("PASS", 0)
            return 0
        if args.command == "launch":
            return launch_publication(args, evidence, tokens, metas)
        if args.command == "push":
            return push_repositories(args, evidence, tokens, metas)
        if args.command == "ci-wait":
            return ci_wait(args, evidence, tokens, metas)
        if args.command == "rollback-plan":
            return rollback_plan(args, evidence, tokens, metas)
        raise PublishError("unsupported command")
    except PublishError as exc:
        evidence.emit("PUBLICATION_FAILED " + str(exc))
        evidence.finish("FAIL", 1, str(exc))
        return 1
    except KeyboardInterrupt:
        evidence.emit("PUBLICATION_INTERRUPTED")
        evidence.finish("INTERRUPTED", 130, "interrupted")
        return 130
    except Exception as exc:
        evidence.emit("PUBLICATION_ERROR " + type(exc).__name__ + ": " + redact(str(exc)))
        evidence.finish("ERROR", 2, type(exc).__name__ + ": " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
