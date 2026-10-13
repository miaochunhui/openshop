#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""OpenShop repository gate.

Audit this repository without network access and without third-party Python
packages. The gate is intentionally allowed to fail while the product is being
built: failures are evidence, not something to waive.

Modes:
  --check      audit the current tree
  --selftest   build positive/negative temporary fixtures and verify every check

JavaScript syntax is checked with node --check from the host. If Node is not
available the gate fails instead of silently skipping JS verification.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Sequence

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
FIXED_PORT = 8780
FIXED_DATA_DIR = "/var/lib/openshop"
FIXED_HOST = "127.0.0.1"
MAX_FINDINGS_PRINTED = 80
BACKTICK = chr(96)

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".openshop-data", "var", "tests-output", "test-results",
    "playwright-report", "docs/screenshots-local", "htmlcov", ".tox",
    ".nox", ".cache",
}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf", ".zip",
    ".gz", ".tar", ".tgz", ".woff", ".woff2", ".ttf", ".otf", ".db",
    ".sqlite3", ".pyc", ".pyo",
}
WEB_ASSET_SUFFIXES = {
    ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
    ".woff", ".woff2", ".ttf", ".otf",
}

REQUIRED_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    "LICENSE",
    "package.json",
    ".github/workflows/ci.yml",
    "deploy/README.md",
    "deploy/healthcheck.py",
    "deploy/systemd/openshop.service",
    "docs/INSTALL.md",
    "docs/OPEN-SOURCE.md",
    "docs/RELEASE.md",
    "docs/architecture.md",
    "docs/product.md",
    "docs/design.md",
    "docs/api-contract.md",
    "docs/data-contract.md",
    "apps/storefront/server.py",
    "web/index.html",
    "data/catalog.json",
)
REQUIRED_ROUTE_FILES = (
    "apps/storefront/routes/catalog.py",
    "apps/storefront/routes/trade.py",
    "apps/storefront/routes/seller.py",
)
REQUIRED_PAGE_FILES = (
    "web/js/pages/home.js",
    "web/js/pages/catalog.js",
    "web/js/pages/product.js",
    "web/js/pages/cart.js",
    "web/js/pages/checkout.js",
    "web/js/pages/account.js",
    "web/js/pages/order.js",
    "web/js/pages/group.js",
    "web/js/pages/seller.js",
    "web/js/pages/about.js",
    "web/js/pages/help.js",
)
README_TERMS = (
    ("positioning", "开集"),
    ("Python version", "Python 3.10"),
    ("explicit data-dir", "--data-dir"),
    ("fixed port", "--port 8780"),
    ("demo buyer", "demo_buyer"),
    ("demo seller", "demo_seller"),
    ("fictional-data boundary", "虚构"),
    ("simulated-payment boundary", "模拟"),
    ("non-production boundary", "非生产"),
    ("test command", "unittest"),
    ("repository gate command", "tools/check_repository.py --check"),
    ("license", "LICENSE"),
    ("self-operated supply", "自营"),
    ("brand supply", "品牌"),
    ("group-buying mode", "拼团"),
)
FORBIDDEN_README_PATTERNS = (
    ("public launch/release claim", re.compile(r"(?:已经|已)\s*(?:正式\s*)?(?:上线|发布|部署)")),
    ("production-ready claim", re.compile(r"production[\s_-]*ready", re.I)),
    ("512 capacity claim", re.compile(r"(?:支持|达到|可达|承诺|实现)\s*512\b")),
)
PLACEHOLDER_PATTERNS = (
    ("TODO marker", re.compile(r"\bTODO\b")),
    ("FIXME marker", re.compile(r"\bFIXME\b")),
    ("WIP marker", re.compile(r"\bWIP\b")),
    ("STUB marker", re.compile(r"\bSTUB\b")),
    ("page stub", re.compile(r"page stub", re.I)),
    ("route placeholder class", re.compile(r"""class=["'][^"'\n]*\broute-placeholder\b""", re.I)),
    ("in-progress marker", re.compile(r"\bIN[ _-]?PROGRESS\b", re.I)),
    ("seat implementation stub", re.compile(r"\bimplemented by\b[^\n]{0,80}\bseat\b", re.I)),
    ("in-progress Chinese copy", re.compile(r"正在由[^\n]{0,40}实现")),
    ("NotImplementedError", re.compile(r"\bNotImplementedError\b")),
    ("not implemented marker", re.compile(r"\bnot implemented\b", re.I)),
    ("coming soon marker", re.compile(r"\bcoming soon\b", re.I)),
    ("lorem ipsum placeholder", re.compile(r"\blorem ipsum\b", re.I)),
    ("Chinese placeholder copy", re.compile(r"占位页|占位文案")),
)
PLACEHOLDER_ROOTS = ("apps/", "packages/", "web/", "tools/", "deploy/", ".github/")
PLACEHOLDER_SUFFIXES = {
    ".py", ".js", ".mjs", ".html", ".css", ".sh",
    ".yml", ".yaml", ".service", ".conf",
}
SECRET_PATTERNS = (
    ("private-key block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("AWS access-key form", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token form", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("Slack token form", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("Stripe live-key form", re.compile(r"\bsk_live_[0-9A-Za-z]{24,}\b")),
    ("Google API-key form", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT form", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("credential in URL", re.compile(r"\bhttps?://[^/\s:@]+:[^/\s@]{6,}@")),
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)\b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd)\b
        \s*[:=]\s*["'][^"'\n]{12,}["']"""
)
SECRET_SOURCE_SUFFIXES = {
    ".py", ".js", ".mjs", ".json", ".yml", ".yaml", ".toml",
    ".sh", ".service", ".conf", ".cfg", ".ini", ".env",
}
MAX_TEXT_BYTES = 4 * 1024 * 1024
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
CSS_URL_RE = re.compile(
    r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^'")\s][^)]*?))\s*\)""",
    re.I | re.S,
)
CSS_IMPORT_STRING_RE = re.compile(r"""@import\s+(["'])([^"']+)\1""", re.I)
CSS_HTTP_RE = re.compile(r"(?i)https?://")
JS_IMPORT_LINE_RE = re.compile(
    r"""(?m)^[ \t]*(?:import|export)\b[^\n;]*?\bfrom\s*["']([^"']+)["']"""
)
JS_IMPORT_SIDE_EFFECT_RE = re.compile(r"""(?m)^[ \t]*import\s*["']([^"']+)["']""")
JS_IMPORT_MULTILINE_RE = re.compile(
    r"""(?ms)^[ \t]*(?:import|export)\s*\{[^}]*\}\s*from\s*["']([^"']+)["']"""
)
JS_DYNAMIC_IMPORT_RE = re.compile(r"""\bimport\s*\(\s*["']([^"']+)["']\s*\)""")
JS_SINK_PATTERNS = (
    ("fetch to absolute URL", re.compile(r"""\bfetch\s*\(\s*["'\x60](?:https?:)?//""", re.I)),
    ("WebSocket/EventSource to absolute URL", re.compile(r"""\b(?:WebSocket|EventSource)\s*\(\s*["'\x60](?:wss?|https?)://""", re.I)),
    ("importScripts/sendBeacon to absolute URL", re.compile(r"""\b(?:importScripts|sendBeacon)\s*\(\s*["'\x60](?:https?:)?//""", re.I)),
    ("remote URL assigned to src", re.compile(r"""\.\bsrc\s*=\s*["'\x60](?:https?:)?//""", re.I)),
    ("remote CSS url in runtime script", re.compile(r"""url\(\s*["']?\s*(?:https?:)?//""", re.I)),
    ("remote element resource in runtime script", re.compile(r"""<(?:img|script|link|iframe|source|video|audio|embed|object)\b[^>]{0,500}\b(?:src|href)\s*=\s*["'](?:https?:)?//""", re.I)),
)
JS_INLINE_HANDLER_RE = re.compile(r"""<[A-Za-z][^>\n]{0,500}\son[a-z]{3,}\s*=""", re.I)
JS_INLINE_SCRIPT_RE = re.compile(r"""<script\b(?![^>]*\bsrc\s*=)""", re.I)
JS_JAVASCRIPT_URL_RE = re.compile(r"""javascript\s*:""", re.I)
VOID_HTML_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
LINK_RESOURCE_RELS = {
    "stylesheet", "icon", "shortcut", "apple-touch-icon", "manifest",
    "preload", "modulepreload", "prefetch", "preconnect", "dns-prefetch",
}
ASSET_JSON_KEYS = {"src", "cover", "image", "icon", "avatar", "poster"}


@dataclass
class Finding:
    path: str
    message: str


@dataclass
class CheckResult:
    check_id: str
    title: str
    failures: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    def fail(self, path: str, message: str) -> None:
        self.failures.append(Finding(path, message))

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(Finding(path, message))

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass
class GateReport:
    root: Path
    checks: list[CheckResult]

    @property
    def failures(self) -> list[Finding]:
        return [finding for check in self.checks for finding in check.failures]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for check in self.checks for finding in check.warnings]

    @property
    def failure_checks(self) -> set[str]:
        return {check.check_id for check in self.checks if check.failures}

    @property
    def passed(self) -> bool:
        return not self.failures


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_absolute_network_url(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    if value.startswith("//"):
        return True
    scheme = urllib.parse.urlsplit(value).scheme.lower()
    return scheme in {"http", "https", "ws", "wss", "ftp"}


def _strip_css_comments(text: str) -> str:
    return CSS_COMMENT_RE.sub("", text)


def _css_url_value(match: re.Match[str]) -> str:
    for group in match.groups():
        if group is not None:
            return group.strip()
    return ""


def _css_urls(text: str) -> list[str]:
    cleaned = _strip_css_comments(text)
    values = [_css_url_value(match) for match in CSS_URL_RE.finditer(cleaned)]
    values.extend(match.group(2).strip() for match in CSS_IMPORT_STRING_RE.finditer(cleaned))
    return [value for value in values if value]


def _mask_data_css_urls(text: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        value = _css_url_value(match)
        if value.lower().startswith("data:"):
            return "url(DATA)"
        return match.group(0)

    return CSS_URL_RE.sub(replace_url, _strip_css_comments(text))


def _external_css_evidence(text: str) -> list[str]:
    cleaned = _strip_css_comments(text)
    evidence: list[str] = []
    for value in _css_urls(cleaned):
        if _is_absolute_network_url(value):
            evidence.append("absolute URL in CSS url()/@import")
            break
    if CSS_HTTP_RE.search(_mask_data_css_urls(cleaned)):
        evidence.append("literal http(s) URL remains in CSS outside data: URLs")
    for match in CSS_IMPORT_STRING_RE.finditer(cleaned):
        if match.group(2).strip().startswith("//"):
            evidence.append("protocol-relative URL in CSS @import")
            break
    return list(dict.fromkeys(evidence))


def _strip_fenced_and_inline_code(text: str) -> str:
    lines: list[str] = []
    fence: str | None = None
    tick_fence = BACKTICK * 3
    for line in text.splitlines(keepends=True):
        probe = line.lstrip()
        if fence is not None:
            if probe.startswith(fence):
                fence = None
            continue
        if probe.startswith(tick_fence):
            fence = tick_fence
            continue
        if probe.startswith("~~~"):
            fence = "~~~"
            continue
        lines.append(line)
    without_fences = "".join(lines)
    inline_code = BACKTICK + "[^" + BACKTICK + "\n]*" + BACKTICK
    return re.sub(inline_code, "", without_fences)


def _markdown_anchors(text: str) -> set[str]:
    scan = _strip_fenced_and_inline_code(text)
    anchors: set[str] = set()
    for raw in re.findall(r"<a\s+[^>]*(?:id|name)=[\"']([^\"']+)[\"'][^>]*>", scan, re.I):
        anchors.add(raw)
    counts: dict[str, int] = {}
    for match in re.finditer(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", scan):
        heading = match.group(1).strip()
        heading = re.sub(r"<[^>]+>", "", heading)
        heading = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", heading)
        heading = re.sub(r"[*_~]", "", heading).strip().lower()
        slug = re.sub(r"[^\w\s-]", "", heading, flags=re.UNICODE)
        slug = re.sub(r"\s+", "-", slug).strip("-")
        if not slug:
            continue
        seen = counts.get(slug, 0)
        counts[slug] = seen + 1
        anchors.add(slug if seen == 0 else f"{slug}-{seen}")
    return anchors


class _HtmlIntegrityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.stack: list[tuple[str, int]] = []
        self.doctype = False
        self.counts = {"html": 0, "head": 0, "body": 0, "title": 0}
        self.ids: dict[str, int] = {}
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower() == "doctype html":
            self.doctype = True

    def _record_start(self, tag: str, attrs: list[tuple[str, str | None]], line: int, self_closing: bool) -> None:
        tag = tag.lower()
        if tag in self.counts:
            self.counts[tag] += 1
        if tag == "title":
            self._in_title = True
        for name, value in attrs:
            if name.lower() == "id" and value:
                if value in self.ids:
                    self.errors.append(f"line {line}: duplicate id={value!r}")
                else:
                    self.ids[value] = line
        if tag not in VOID_HTML_TAGS and not self_closing:
            self.stack.append((tag, line))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record_start(tag, attrs, self.getpos()[0], False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record_start(tag, attrs, self.getpos()[0], True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        line = self.getpos()[0]
        if tag == "title":
            self._in_title = False
        if tag in VOID_HTML_TAGS:
            self.errors.append(f"line {line}: closing tag for void element {tag}")
            return
        if not self.stack:
            self.errors.append(f"line {line}: unexpected closing tag {tag}")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                unclosed = ", ".join(item[0] for item in self.stack[index + 1:])
                self.errors.append(f"line {line}: closing tag {tag} while {unclosed} still open")
                del self.stack[index:]
                return
        self.errors.append(f"line {line}: stray closing tag {tag}")

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title_parts.append(data.strip())

    def finish(self) -> list[str]:
        errors = list(self.errors)
        if not self.doctype:
            errors.append("missing <!doctype html>")
        for tag in ("html", "head", "body"):
            if self.counts[tag] != 1:
                errors.append(f"expected exactly one <{tag}> element, found {self.counts[tag]}")
        if self.counts["title"] != 1 or not "".join(self.title_parts).strip():
            errors.append("expected exactly one non-empty <title> element")
        for tag, line in self.stack:
            errors.append(f"line {line}: unclosed <{tag}> element")
        return errors


class _HtmlInlineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []

    def _scan(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        line = self.getpos()[0]
        attrs_map = {name.lower(): (value or "") for name, value in attrs}
        if tag.lower() == "script" and not attrs_map.get("src", "").strip():
            self.errors.append(f"line {line}: inline script element without src")
        for name, value in attrs_map.items():
            if re.fullmatch(r"on[a-z]+", name):
                self.errors.append(f"line {line}: inline event handler attribute {name}")
            if value.strip().lower().startswith("javascript:"):
                self.errors.append(f"line {line}: javascript URL in attribute {name}")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._scan(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._scan(tag, attrs)


class _HtmlResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.external_resources: list[str] = []
        self.external_links: list[str] = []
        self.local_refs: list[str] = []
        self.css_texts: list[str] = []
        self._in_style = False

    def _scan_url(self, tag: str, attr: str, value: str, *, as_link: bool = False, track_local: bool = True) -> None:
        value = value.strip()
        if _is_absolute_network_url(value):
            if as_link:
                self.external_links.append(f"<{tag} {attr}>")
            else:
                self.external_resources.append(f"<{tag} {attr}>")
        elif track_local and value and not value.startswith(("#", "data:", "blob:", "javascript:", "mailto:", "tel:")):
            self.local_refs.append(value)

    def _handle_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {name.lower(): (value or "") for name, value in attrs}
        if "style" in attrs_map:
            self.css_texts.append(attrs_map["style"])
        if tag == "style":
            self._in_style = True
        if tag == "base" and attrs_map.get("href", "").strip():
            self.external_resources.append("<base href> is not supported")
        if tag == "link":
            rel_tokens = {token.lower() for token in attrs_map.get("rel", "").split()}
            href = attrs_map.get("href", "")
            if href:
                if rel_tokens & LINK_RESOURCE_RELS:
                    self._scan_url(tag, "href", href)
                else:
                    self._scan_url(tag, "href", href, as_link=True, track_local=False)
        if tag in {"a", "area"} and _is_absolute_network_url(attrs_map.get("href", "")):
            self.external_links.append(f"<{tag} href>")
        if tag == "form" and _is_absolute_network_url(attrs_map.get("action", "")):
            self.external_resources.append("<form action>")
        if tag == "object":
            self._scan_url(tag, "data", attrs_map.get("data", ""))
        if tag == "video":
            self._scan_url(tag, "poster", attrs_map.get("poster", ""))
        if tag in {"script", "img", "source", "video", "audio", "track", "iframe", "embed", "input"}:
            self._scan_url(tag, "src", attrs_map.get("src", ""))
        if tag in {"use", "image"}:
            self._scan_url(tag, "href", attrs_map.get("href", "") or attrs_map.get("xlink:href", ""))
        if attrs_map.get("srcset"):
            for candidate in attrs_map["srcset"].split(","):
                value = candidate.strip().split(" ", 1)[0]
                self._scan_url(tag, "srcset", value)
        if tag == "meta":
            http_equiv = attrs_map.get("http-equiv", "").strip().lower()
            content = attrs_map.get("content", "")
            if http_equiv == "refresh":
                match = re.search(r"(?i)\burl\s*=\s*['\"]?([^\s'\";]+)", content)
                if match and _is_absolute_network_url(match.group(1)):
                    self.external_resources.append("<meta http-equiv=refresh>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_attrs(tag.lower(), attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_attrs(tag.lower(), attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.css_texts.append(data)


class RepositoryGate:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.node = shutil.which("node")

    def iter_files(self) -> list[Path]:
        paths: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root, topdown=True, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS and name != ".git")
            for name in sorted(filenames):
                if name == ".git":
                    continue
                path = current / name
                if path.is_symlink():
                    continue
                paths.append(path)
        return sorted(paths, key=lambda item: item.as_posix())

    def files_with_suffix(self, suffixes: set[str] | tuple[str, ...]) -> list[Path]:
        suffixes = set(suffixes)
        return [path for path in self.iter_files() if path.suffix.lower() in suffixes]

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _js_imports(self, text: str) -> list[str]:
        specs: list[str] = []
        for pattern in (JS_IMPORT_LINE_RE, JS_IMPORT_SIDE_EFFECT_RE, JS_IMPORT_MULTILINE_RE, JS_DYNAMIC_IMPORT_RE):
            specs.extend(pattern.findall(text))
        return list(dict.fromkeys(specs))

    def run(self) -> GateReport:
        checks: list[Callable[[CheckResult], None]] = [
            self.check_required_files,
            self.check_test_suite,
            self.check_readme_contract,
            self.check_license,
            self.check_route_surface,
            self.check_placeholder_stubs,
            self.check_json_syntax,
            self.check_python_syntax,
            self.check_js_syntax,
            self.check_html_syntax,
            self.check_inline_scripts,
            self.check_external_resources,
            self.check_secret_patterns,
            self.check_asset_references,
            self.check_markdown_links,
            self.check_css_tokens,
            self.check_ci_policy,
            self.check_deploy_policy,
            self.check_no_third_party,
        ]
        results: list[CheckResult] = []
        for check in checks:
            check_id = check.__name__.removeprefix("check_")
            result = CheckResult(check_id=check_id, title=check_id.replace("_", " "))
            try:
                check(result)
            except Exception as exc:  # noqa: BLE001 - fail closed
                result.fail(".", f"internal gate error: {type(exc).__name__}: {exc}")
            results.append(result)
        return GateReport(self.root, results)

    def check_required_files(self, result: CheckResult) -> None:
        for rel in REQUIRED_FILES:
            path = self.root / rel
            if not path.is_file():
                result.fail(rel, "required repository file is missing")
            elif path.stat().st_size == 0:
                result.fail(rel, "required repository file is empty")

    def check_test_suite(self, result: CheckResult) -> None:
        tests = self.root / "tests"
        if not tests.is_dir():
            result.fail("tests/", "standard-library test directory is missing")
            return
        candidates = sorted({path.resolve() for path in tests.rglob("test_*.py")})
        if not candidates:
            result.fail("tests/", "no tests/test_*.py discovered by unittest")
            return
        usable = 0
        for path in candidates:
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            has_unittest = re.search(r"(?m)^\s*(?:import unittest|from unittest\b)", text)
            has_test = re.search(r"(?m)^\s*def test_\w+\s*\(", text)
            if has_unittest and has_test:
                usable += 1
        if usable == 0:
            result.fail("tests/", "no unittest test module with a test_* function found")

    def check_readme_contract(self, result: CheckResult) -> None:
        path = self.root / "README.md"
        if not path.is_file():
            result.fail("README.md", "README is missing")
            return
        text = self.read_text(path)
        missing = [label for label, term in README_TERMS if term not in text]
        if missing:
            result.fail("README.md", "missing required content: " + ", ".join(missing))
        # Public-launch copy is allowed only after this repository has an explicit
        # deployment record: both README and docs/deployment.md name the public
        # origin, and the record documents TLS, rollback and the external boundary.
        # This preserves the original fail-closed behavior for a pre-launch tree
        # while letting a genuinely deployed release tell the truth. It is a
        # documentation gate, not proof that a remote service is alive.
        deployment_record = self.root / "docs/deployment.md"
        public_record_ok = False
        public_origin = "https://openshop.ai4everyone.top/"
        if deployment_record.is_file() and public_origin in text:
            try:
                deployment_text = self.read_text(deployment_record)
            except (OSError, UnicodeDecodeError):
                deployment_text = ""
            public_record_ok = all(
                needle in deployment_text
                for needle in (public_origin, "公网入口", "TLS", "回滚", "外部 Git")
            )
        for label, pattern in FORBIDDEN_README_PATTERNS:
            if pattern.search(text):
                if label == "public launch/release claim" and public_record_ok:
                    continue
                result.fail("README.md", f"forbidden public claim: {label}")
        if re.search(r"\b512\b", text):
            result.warn("README.md", "contains the number 512; review that it is not a capacity claim")

    def check_license(self, result: CheckResult) -> None:
        path = self.root / "LICENSE"
        if not path.is_file():
            result.fail("LICENSE", "LICENSE is missing")
            return
        try:
            text = self.read_text(path)
        except (OSError, UnicodeDecodeError) as exc:
            result.fail("LICENSE", f"cannot read license text: {type(exc).__name__}")
            return
        required = (
            "Apache License",
            "Version 2.0, January 2004",
            "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION",
            "END OF TERMS AND CONDITIONS",
        )
        if any(fragment not in text for fragment in required):
            result.fail("LICENSE", "does not look like the intact Apache-2.0 license text")

    def check_route_surface(self, result: CheckResult) -> None:
        for rel in REQUIRED_ROUTE_FILES:
            path = self.root / rel
            if not path.is_file():
                result.fail(rel, "required route module is missing")
                continue
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError) as exc:
                result.fail(rel, f"cannot read route module: {type(exc).__name__}")
                continue
            if "router.add(" not in text:
                result.fail(rel, "route module registers no route via router.add()")
        direct_export_re = re.compile(r"\bexport\s+(?:default\s+)?(?:async\s+)?function\s+render\b")
        export_list_re = re.compile(r"\bexport\s*\{[^}]*\brender\b[^}]*\}")
        for rel in REQUIRED_PAGE_FILES:
            path = self.root / rel
            if not path.is_file():
                result.fail(rel, "required page module is missing")
                continue
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError) as exc:
                result.fail(rel, f"cannot read page module: {type(exc).__name__}")
                continue
            if not (direct_export_re.search(text) or export_list_re.search(text)):
                result.fail(rel, "page module does not export render()")

    def check_placeholder_stubs(self, result: CheckResult) -> None:
        self_file = (self.root / "tools/check_repository.py").resolve()
        for path in self.iter_files():
            if path.resolve() == self_file:
                continue
            rel = self.relative(path)
            if rel.startswith("tests/"):
                continue
            if path.suffix.lower() not in PLACEHOLDER_SUFFIXES:
                continue
            if not any(rel.startswith(prefix) for prefix in PLACEHOLDER_ROOTS):
                continue
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            matches: list[str] = []
            for label, pattern in PLACEHOLDER_PATTERNS:
                match = pattern.search(text)
                if match:
                    matches.append(f"{label} (line {_line_at(text, match.start())})")
            if matches:
                result.fail(rel, "placeholder/stub remnant: " + "; ".join(matches))

    def check_json_syntax(self, result: CheckResult) -> None:
        for path in self.files_with_suffix({".json"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
                json.loads(text)
            except json.JSONDecodeError as exc:
                result.fail(rel, f"invalid JSON at line {exc.lineno}: {exc.msg}")
            except (OSError, UnicodeDecodeError) as exc:
                result.fail(rel, f"cannot read JSON: {type(exc).__name__}")

    def check_python_syntax(self, result: CheckResult) -> None:
        for path in self.files_with_suffix({".py"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
                compile(text, rel, "exec")
            except SyntaxError as exc:
                result.fail(rel, f"Python SyntaxError at line {exc.lineno}: {exc.msg}")
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                result.fail(rel, f"cannot compile Python: {type(exc).__name__}: {exc}")

    def check_js_syntax(self, result: CheckResult) -> None:
        paths = self.files_with_suffix({".js", ".mjs"})
        if not paths:
            result.warn(".", "no JavaScript files found to syntax-check")
            return
        if not self.node:
            result.fail(".", "node executable not found; JavaScript syntax cannot be verified (fail closed)")
            return
        env = dict(os.environ)
        for key in ("NODE_OPTIONS", "NODE_PATH"):
            env.pop(key, None)
        for path in paths:
            rel = self.relative(path)
            try:
                proc = subprocess.run(
                    [self.node, "--check", str(path)],
                    cwd=self.root,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                result.fail(rel, f"node --check could not run: {type(exc).__name__}")
                continue
            if proc.returncode != 0:
                result.fail(rel, f"node --check failed with exit={proc.returncode}; rerun: node --check {rel}")

    def check_html_syntax(self, result: CheckResult) -> None:
        for path in self.files_with_suffix({".html", ".htm"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError) as exc:
                result.fail(rel, f"cannot read HTML: {type(exc).__name__}")
                continue
            parser = _HtmlIntegrityParser()
            try:
                parser.feed(text)
                parser.close()
            except Exception as exc:  # noqa: BLE001
                result.fail(rel, f"HTML parser failed: {type(exc).__name__}")
                continue
            errors = parser.finish()
            if errors:
                result.fail(rel, "; ".join(errors[:8]))

    def check_inline_scripts(self, result: CheckResult) -> None:
        for path in self.files_with_suffix({".html", ".htm"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
                parser = _HtmlInlineParser()
                parser.feed(text)
                parser.close()
            except Exception as exc:  # noqa: BLE001
                result.fail(rel, f"cannot scan inline scripts: {type(exc).__name__}")
                continue
            if parser.errors:
                result.fail(rel, "; ".join(parser.errors[:8]))
        for path in self.files_with_suffix({".js", ".mjs"}):
            rel = self.relative(path)
            if not rel.startswith("web/"):
                continue
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            if JS_INLINE_HANDLER_RE.search(text):
                result.fail(rel, "JavaScript template contains an inline event handler attribute")
            if JS_INLINE_SCRIPT_RE.search(text):
                result.fail(rel, "JavaScript template contains an inline script without src")
            if JS_JAVASCRIPT_URL_RE.search(text):
                result.fail(rel, "JavaScript source contains a javascript: URL")
        for path in self.files_with_suffix({".svg"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            if re.search(r"<script\b", text, re.I):
                result.fail(rel, "SVG contains a script element")
            if re.search(r"\son[a-z]+\s*=", text, re.I):
                result.fail(rel, "SVG contains an inline event handler attribute")

    def check_external_resources(self, result: CheckResult) -> None:
        for path in self.files_with_suffix({".html", ".htm"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
                parser = _HtmlResourceParser()
                parser.feed(text)
                parser.close()
            except Exception as exc:  # noqa: BLE001
                result.fail(rel, f"cannot scan external resources: {type(exc).__name__}")
                continue
            for item in parser.external_resources:
                result.fail(rel, f"third-party runtime resource: {item}")
            if parser.external_links:
                result.warn(rel, f"{len(parser.external_links)} external hyperlink(s) in HTML (not fetched)")
            for css in parser.css_texts:
                for item in _external_css_evidence(css):
                    result.fail(rel, item)
        for path in self.files_with_suffix({".css"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            for item in _external_css_evidence(text):
                result.fail(rel, item)
        for path in self.files_with_suffix({".svg"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            try:
                svg_root = ET.fromstring(text)
            except ET.ParseError:
                continue
            for element in svg_root.iter():
                local = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
                for raw_name, raw_value in element.attrib.items():
                    name = raw_name.rsplit("}", 1)[-1].lower()
                    value = str(raw_value)
                    if name in {"href", "src"} and _is_absolute_network_url(value):
                        result.fail(rel, f"third-party runtime resource in SVG <{local} {name}>")
                    if name == "style":
                        for item in _external_css_evidence(value):
                            result.fail(rel, item)
                if local == "style" and element.text:
                    for item in _external_css_evidence(element.text):
                        result.fail(rel, item)
        for path in self.files_with_suffix({".js", ".mjs"}):
            rel = self.relative(path)
            # Test harnesses intentionally embed hostile remote-resource
            # fixtures to prove that the browser/external-request detector can
            # go red. They are never loaded by the product runtime.
            if rel.startswith(("tests/", "tools/")):
                continue
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            for label, pattern in JS_SINK_PATTERNS:
                if pattern.search(text):
                    result.fail(rel, f"third-party runtime network sink: {label}")
            for specifier in self._js_imports(text):
                if _is_absolute_network_url(specifier):
                    result.fail(rel, "external module import URL in JavaScript")
                    break
        for path in self.files_with_suffix({".json"}):
            rel = self.relative(path)
            try:
                data = json.loads(self.read_text(path))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            for key in self._json_external_asset_fields(data):
                result.fail(rel, f"external URL in JSON asset field {key!r}")

    def _json_external_asset_fields(self, value: Any, key: str = "") -> list[str]:
        findings: list[str] = []
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                findings.extend(self._json_external_asset_fields(child_value, str(child_key)))
        elif isinstance(value, list):
            for child_value in value:
                findings.extend(self._json_external_asset_fields(child_value, key))
        elif isinstance(value, str) and key.lower() in ASSET_JSON_KEYS and _is_absolute_network_url(value):
            findings.append(key)
        return findings

    def check_secret_patterns(self, result: CheckResult) -> None:
        for path in self.iter_files():
            if path.suffix.lower() in BINARY_SUFFIXES:
                continue
            rel = self.relative(path)
            try:
                if path.stat().st_size > MAX_TEXT_BYTES:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for label, pattern in SECRET_PATTERNS:
                match = pattern.search(text)
                if match:
                    result.fail(rel, f"possible {label} at line {_line_at(text, match.start())} (value not printed)")
            if rel.startswith("tests/"):
                continue
            if path.suffix.lower() in SECRET_SOURCE_SUFFIXES or path.name.startswith(".env"):
                match = SECRET_ASSIGNMENT_RE.search(text)
                if match:
                    result.fail(rel, f"credential-shaped assignment at line {_line_at(text, match.start())} (value not printed)")

    def _validate_browser_ref(self, result: CheckResult, ref_raw: str, origin: str, base_dir: Path, *, context: str) -> Path | None:
        value = ref_raw.strip().strip("\"'")
        if not value or value.startswith(("#", "data:", "blob:", "javascript:", "mailto:", "tel:")):
            return None
        if _is_absolute_network_url(value):
            return None
        if "{" in value or value.startswith("var("):
            return None
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme:
            return None
        path_part = urllib.parse.unquote(parsed.path)
        if not path_part:
            return None
        base = self.root if path_part.startswith("/") else base_dir
        target = (base / path_part.lstrip("/")).resolve() if path_part.startswith("/") else (base / path_part).resolve()
        web_root = (self.root / "web").resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            result.fail(origin, f"{context} escapes the repository: {path_part}")
            return None
        try:
            target.relative_to(web_root)
        except ValueError:
            result.fail(origin, f"{context} must resolve under web/: {path_part}")
            return None
        if not target.is_file():
            result.fail(origin, f"broken local asset reference ({context}): {path_part}")
            return None
        try:
            if target.stat().st_size == 0:
                result.fail(origin, f"empty local asset referenced by {context}: {path_part}")
        except OSError:
            result.fail(origin, f"cannot stat asset: {path_part}")
        return target

    def check_asset_references(self, result: CheckResult) -> None:
        referenced: set[Path] = set()
        assets_root = (self.root / "web/assets").resolve()
        for path in self.files_with_suffix({".html", ".htm"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
                parser = _HtmlResourceParser()
                parser.feed(text)
                parser.close()
            except Exception as exc:  # noqa: BLE001
                result.fail(rel, f"cannot scan HTML assets: {type(exc).__name__}")
                continue
            for ref in parser.local_refs:
                target = self._validate_browser_ref(result, ref, rel, path.parent, context="HTML resource")
                if target:
                    referenced.add(target)
            for css in parser.css_texts:
                for ref in _css_urls(css):
                    target = self._validate_browser_ref(result, ref, rel, path.parent, context="inline CSS url()")
                    if target:
                        referenced.add(target)
        for path in self.files_with_suffix({".css"}):
            rel = self.relative(path)
            try:
                css = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            for ref in _css_urls(css):
                target = self._validate_browser_ref(result, ref, rel, path.parent, context="CSS url()/@import")
                if target:
                    referenced.add(target)
        for path in self.files_with_suffix({".js", ".mjs"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            literal_asset_re = re.compile(
                r"/web/[A-Za-z0-9_./%+-]+\.(?:svg|png|jpe?g|webp|gif|ico|css|js|mjs|woff2?|ttf|otf|webmanifest)"
            )
            for match in literal_asset_re.finditer(text):
                target = self._validate_browser_ref(result, match.group(0), rel, path.parent, context="JavaScript asset literal")
                if target:
                    referenced.add(target)
            # Import resolution is a browser-asset constraint only for the
            # runtime web tree. Node test harnesses import sibling helpers via
            # relative paths that are not under web/ and must not be treated as
            # missing browser assets.
            if rel.startswith("web/"):
                for specifier in self._js_imports(text):
                    if specifier.startswith(".") or specifier.startswith("/"):
                        target = self._validate_browser_ref(result, specifier, rel, path.parent, context="JavaScript module import")
                        if target:
                            referenced.add(target)
        for path in self.files_with_suffix({".json"}):
            rel = self.relative(path)
            try:
                data = json.loads(self.read_text(path))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            for value in self._json_web_paths(data):
                target = self._validate_browser_ref(result, value, rel, path.parent, context="JSON asset path")
                if target:
                    referenced.add(target)
        for path in self.files_with_suffix({".svg"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            try:
                svg_root = ET.fromstring(text)
            except ET.ParseError as exc:
                result.fail(rel, f"malformed SVG: {exc}")
                continue
            if svg_root.tag.rsplit("}", 1)[-1].lower() != "svg":
                result.fail(rel, "SVG file does not have an svg root element")
                continue
            for element in svg_root.iter():
                for raw_name, raw_value in element.attrib.items():
                    name = raw_name.rsplit("}", 1)[-1].lower()
                    value = str(raw_value).strip()
                    if name == "href" and value and not value.startswith("#") and not _is_absolute_network_url(value):
                        target = self._validate_browser_ref(result, value, rel, path.parent, context="SVG href")
                        if target:
                            referenced.add(target)
                    elif name == "style":
                        for ref in _css_urls(value):
                            target = self._validate_browser_ref(result, ref, rel, path.parent, context="SVG inline CSS url()")
                            if target:
                                referenced.add(target)
                local_tag = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
                if local_tag == "style" and element.text:
                    for ref in _css_urls(element.text):
                        target = self._validate_browser_ref(result, ref, rel, path.parent, context="SVG CSS url()")
                        if target:
                            referenced.add(target)
        if assets_root.is_dir():
            asset_files = [
                path.resolve() for path in assets_root.rglob("*")
                if path.is_file() and path.suffix.lower() in WEB_ASSET_SUFFIXES
            ]
            for asset in sorted(asset_files):
                if asset not in referenced:
                    result.warn(self.relative(asset), "asset is not referenced by HTML/CSS/JS/JSON/SVG")

    def _json_web_paths(self, value: Any) -> list[str]:
        paths: list[str] = []
        if isinstance(value, dict):
            for child in value.values():
                paths.extend(self._json_web_paths(child))
        elif isinstance(value, list):
            for child in value:
                paths.extend(self._json_web_paths(child))
        elif isinstance(value, str) and value.startswith("/web/"):
            paths.append(value)
        return paths

    def check_markdown_links(self, result: CheckResult) -> None:
        link_re = re.compile(
            r"(?P<image>!?)\[(?P<label>[^\]\n]*)\]\(\s*(?P<target><[^>\n]*>|[^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)"
        )
        image_ref_re = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\[(?P<label>[^\]\n]*)\]")
        ref_def_re = re.compile(r"(?m)^[ \t]{0,3}\[(?P<label>[^\]\n]+)\]:[ \t]*(?P<target><[^>\n]*>|\S+)")
        for path in self.files_with_suffix({".md", ".markdown"}):
            rel = self.relative(path)
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError) as exc:
                result.fail(rel, f"cannot read Markdown: {type(exc).__name__}")
                continue
            scan = _strip_fenced_and_inline_code(text)
            external_count = 0

            def check_target(raw_target: str, *, image: bool) -> None:
                nonlocal external_count
                target = raw_target.strip()
                if target.startswith("<") and target.endswith(">"):
                    target = target[1:-1].strip()
                if not target:
                    return
                parsed = urllib.parse.urlsplit(target)
                scheme = parsed.scheme.lower()
                if scheme in {"http", "https", "ws", "wss", "ftp"} or target.startswith("//"):
                    if image:
                        result.fail(rel, "external Markdown image is a third-party resource")
                    else:
                        external_count += 1
                    return
                if scheme in {"mailto", "tel"}:
                    if image:
                        result.fail(rel, "Markdown image uses a non-resource URL scheme")
                    else:
                        external_count += 1
                    return
                if scheme in {"data", "blob"}:
                    if image:
                        return
                    result.fail(rel, "Markdown link uses an unsupported inline-data scheme")
                    return
                if scheme:
                    result.fail(rel, "Markdown link uses an unsupported URL scheme")
                    return
                if not parsed.path:
                    if parsed.fragment:
                        anchors = _markdown_anchors(text)
                        fragment = urllib.parse.unquote(parsed.fragment)
                        if fragment not in anchors:
                            result.fail(rel, f"dead Markdown anchor: #{fragment}")
                    return
                path_part = urllib.parse.unquote(parsed.path)
                if path_part.startswith("/"):
                    candidate = (self.root / path_part.lstrip("/")).resolve()
                else:
                    candidate = (path.parent / path_part).resolve()
                try:
                    candidate.relative_to(self.root)
                except ValueError:
                    result.fail(rel, f"Markdown link escapes the repository: {path_part}")
                    return
                if not candidate.exists():
                    result.fail(rel, f"dead local Markdown link: {path_part}")
                    return
                if parsed.fragment and candidate.suffix.lower() in {".md", ".markdown"} and candidate.is_file():
                    try:
                        target_text = self.read_text(candidate)
                    except (OSError, UnicodeDecodeError):
                        return
                    fragment = urllib.parse.unquote(parsed.fragment)
                    if fragment not in _markdown_anchors(target_text):
                        result.fail(rel, f"dead Markdown anchor in {path_part}: #{fragment}")

            for match in link_re.finditer(scan):
                check_target(match.group("target").strip(), image=match.group("image") == "!")
            definitions: dict[str, str] = {}
            for match in ref_def_re.finditer(scan):
                target = match.group("target").strip()
                definitions[match.group("label").strip().lower()] = target
                check_target(target, image=False)
            for match in image_ref_re.finditer(scan):
                label = match.group("label").strip().lower() or match.group("alt").strip().lower()
                target = definitions.get(label)
                if target and _is_absolute_network_url(target):
                    result.fail(rel, "external Markdown image (reference style) is a third-party resource")
            for match in re.finditer(r"<img\b[^>]*>", scan, re.I):
                src_match = re.search(r"""\bsrc\s*=\s*["']([^"']+)["']""", match.group(0), re.I)
                if src_match:
                    check_target(src_match.group(1), image=True)
            if external_count:
                result.warn(rel, f"{external_count} external hyperlink(s) not fetched (offline gate)")

    def check_css_tokens(self, result: CheckResult) -> None:
        definitions: dict[str, list[tuple[str, str]]] = {}
        root_re = re.compile(r"(?s):root\s*\{([^{}]*)\}")
        token_re = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+)")
        for path in self.files_with_suffix({".css"}):
            rel = self.relative(path)
            try:
                css = _strip_css_comments(self.read_text(path))
            except (OSError, UnicodeDecodeError):
                continue
            for block in root_re.finditer(css):
                for token_match in token_re.finditer(block.group(1)):
                    token = token_match.group(1)
                    value = " ".join(token_match.group(2).split())
                    definitions.setdefault(token, []).append((rel, value))
        for token, entries in sorted(definitions.items()):
            if len(entries) > 1:
                locations = ", ".join(f"{rel}={value}" for rel, value in entries)
                result.fail(entries[0][0], f"duplicate :root design token {token}: {locations}")

    def check_ci_policy(self, result: CheckResult) -> None:
        path = self.root / ".github/workflows/ci.yml"
        if not path.is_file():
            result.fail(".github/workflows/ci.yml", "CI workflow is missing")
            return
        text = self.read_text(path)
        required = (
            ("permissions block", "permissions:"),
            ("read-only contents permission", "contents: read"),
            ("pull_request trigger", "pull_request"),
            ("push trigger", "push"),
            ("job runner", "runs-on:"),
            ("checkout credentials disabled", "persist-credentials: false"),
            ("unittest tests command", "unittest discover -s tests"),
            ("gate selftest command", "tools/check_repository.py --selftest"),
            ("gate check command", "tools/check_repository.py --check"),
        )
        for label, needle in required:
            if needle not in text:
                result.fail(".github/workflows/ci.yml", f"missing CI requirement: {label}")
        if "\t" in text:
            result.fail(".github/workflows/ci.yml", "tab character found; workflow YAML requires spaces")
        forbidden = (
            ("write permission", re.compile(r"(?m)^\s*(?:permissions:\s*write-all|(?:contents|actions|checks|deployments|id-token|issues|packages|pull-requests|security-events|statuses):\s*write\b)")),
            ("credential access", re.compile(r"\bsecrets\b|\bgithub\.token\b", re.I)),
            ("pull_request_target", re.compile(r"\bpull_request_target\b")),
            ("sudo", re.compile(r"(?m)^\s*(?:-\s*)?sudo\s+")),
            ("package installation", re.compile(r"(?i)(?:\bpip3?\s+install\b|\bpython3?\s+-m\s+pip\b|\bnpm\s+(?:install|ci)\b|\bpnpm\s+(?:install|add)\b|\byarn\s+(?:install|add)\b|\bapt(?:-get)?\s+(?:install|update)\b|\bbrew\s+install\b)")),
            ("remote shell pipeline", re.compile(r"(?i)\bcurl\b[^\n]*\|\s*(?:sh|bash)\b")),
        )
        for label, pattern in forbidden:
            if pattern.search(text):
                result.fail(".github/workflows/ci.yml", f"forbidden CI pattern: {label}")
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*(\S+)", text)
        for action in uses:
            if "@" not in action:
                result.fail(".github/workflows/ci.yml", f"action reference has no version: {action}")
            elif not re.search(r"@[0-9a-f]{40}$", action):
                result.warn(".github/workflows/ci.yml", f"action is tag-pinned rather than SHA-pinned: {action}")

    def check_deploy_policy(self, result: CheckResult) -> None:
        unit_path = self.root / "deploy/systemd/openshop.service"
        readme_path = self.root / "deploy/README.md"
        health_path = self.root / "deploy/healthcheck.py"
        for rel, path in (
            ("deploy/systemd/openshop.service", unit_path),
            ("deploy/README.md", readme_path),
            ("deploy/healthcheck.py", health_path),
        ):
            if not path.is_file():
                result.fail(rel, "deployment template file is missing")
        if not unit_path.is_file():
            return
        text = self.read_text(unit_path)
        sections: dict[str, list[tuple[str, str]]] = {}
        current = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line
                sections.setdefault(current, [])
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                sections.setdefault(current, []).append((key.strip(), value.strip()))
        service = sections.get("[Service]", [])
        install = sections.get("[Install]", [])
        if not sections.get("[Unit]"):
            result.fail("deploy/systemd/openshop.service", "unit is missing the [Unit] section")
        directives: dict[str, list[str]] = {}
        for key, value in service:
            directives.setdefault(key, []).append(value)

        def first(key: str) -> str:
            values = directives.get(key, [])
            return values[0] if values else ""

        user = first("User")
        if not user or user in {"root", "0"}:
            result.fail("deploy/systemd/openshop.service", "service must set a non-root User=")
        if not first("Group"):
            result.fail("deploy/systemd/openshop.service", "service must set Group=")
        exec_start = first("ExecStart")
        try:
            argv = shlex.split(exec_start)
        except ValueError:
            argv = []
            result.fail("deploy/systemd/openshop.service", "ExecStart cannot be parsed")
        if not argv or not argv[0].startswith("/"):
            result.fail("deploy/systemd/openshop.service", "ExecStart must use an absolute executable")
        if argv and argv[0] != "/usr/bin/python3":
            result.warn("deploy/systemd/openshop.service", "ExecStart does not use /usr/bin/python3")

        def option(args: Sequence[str], name: str) -> str:
            for index, item in enumerate(args):
                if item == name and index + 1 < len(args):
                    return args[index + 1]
                if item.startswith(name + "="):
                    return item.split("=", 1)[1]
            return ""

        host = option(argv, "--host")
        port_raw = option(argv, "--port")
        data_dir = option(argv, "--data-dir")
        # Two reviewed network profiles: the original loopback-only template and
        # the deployed LAN-edge + AI profile. The latter is not an open listener:
        # systemd BPF allow-lists only the two edge hosts and the outbound model
        # gateway/DNS peers, and the app trusts X-Forwarded-For only from them.
        public_edge = host == "0.0.0.0"
        if host not in {FIXED_HOST, "0.0.0.0"}:
            result.fail("deploy/systemd/openshop.service", f"service must bind {FIXED_HOST} only or the reviewed public-edge 0.0.0.0 profile")
        expected_port = 8790 if public_edge else FIXED_PORT
        try:
            port = int(port_raw)
        except ValueError:
            port = 0
        if port != expected_port:
            result.fail("deploy/systemd/openshop.service", f"service must use the fixed non-zero port {expected_port} for this network profile")
        if data_dir != FIXED_DATA_DIR:
            result.fail("deploy/systemd/openshop.service", f"service must use external data dir {FIXED_DATA_DIR}")
        if data_dir.startswith("/opt/openshop") or ".." in data_dir:
            result.fail("deploy/systemd/openshop.service", "data directory must be outside the release tree")
        if first("WorkingDirectory") != "/opt/openshop/current":
            result.fail("deploy/systemd/openshop.service", "WorkingDirectory must be the fixed /opt/openshop/current symlink")
        if first("StateDirectory") != "openshop":
            result.fail("deploy/systemd/openshop.service", "StateDirectory=openshop must create external writable data")
        if first("StateDirectoryMode") != "0700":
            result.fail("deploy/systemd/openshop.service", "StateDirectoryMode must be 0700")
        if first("StandardOutput") != "journal" or first("StandardError") != "journal":
            result.fail("deploy/systemd/openshop.service", "stdout/stderr must go to the journal")
        if first("Restart") != "on-failure":
            result.fail("deploy/systemd/openshop.service", "Restart=on-failure is required")
        hardening = (
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "ProtectKernelTunables=true",
            "ProtectKernelModules=true",
            "ProtectKernelLogs=true",
            "ProtectControlGroups=true",
            "ProtectClock=true",
            "ProtectHostname=true",
            "RestrictNamespaces=true",
            "RestrictRealtime=true",
            "RestrictSUIDSGID=true",
            "LockPersonality=true",
            "UMask=0077",
            "SystemCallArchitectures=native",
            "SystemCallFilter=@system-service",
            "IPAddressDeny=any",
        )
        for directive in hardening:
            key, value = directive.split("=", 1)
            if value not in directives.get(key, []):
                result.fail("deploy/systemd/openshop.service", f"missing hardening directive {directive}")
        if not directives.get("CapabilityBoundingSet") or any(value != "" for value in directives["CapabilityBoundingSet"]):
            result.fail("deploy/systemd/openshop.service", "CapabilityBoundingSet must be explicitly emptied")
        address_families = first("RestrictAddressFamilies")
        if not {"AF_UNIX", "AF_INET"}.issubset(set(address_families.split())):
            result.fail("deploy/systemd/openshop.service", "RestrictAddressFamilies must include AF_UNIX and AF_INET")
        ip_allow = first("IPAddressAllow")
        if "127.0.0.1" not in ip_allow:
            result.fail("deploy/systemd/openshop.service", "IPAddressAllow must include loopback")
        if public_edge:
            for required_peer in ("192.168.1.183/32", "192.168.1.184/32", "192.168.208.213/32", "192.168.208.223/32"):
                if required_peer not in ip_allow:
                    result.fail("deploy/systemd/openshop.service", f"public-edge IPAddressAllow must include {required_peer}")
            for token in ip_allow.split():
                if token in {"any", "all"} or token.endswith("/0"):
                    result.fail("deploy/systemd/openshop.service", f"public-edge IPAddressAllow is open to all addresses: {token}")
            env_flat = " ".join(directives.get("Environment", []))
            if "OPENSHOP_TRUSTED_PROXIES=" not in env_flat or "192.168.1.183" not in env_flat or "192.168.1.184" not in env_flat:
                result.fail("deploy/systemd/openshop.service", "public-edge profile must trust X-Forwarded-For only from the two edge hosts")
        for key in ("ReadWritePaths", "ReadOnlyPaths"):
            for value in directives.get(key, []):
                if value.startswith("/opt/openshop"):
                    result.fail("deploy/systemd/openshop.service", "release tree must remain read-only")
        exec_post = first("ExecStartPost")
        health_url = f"http://127.0.0.1:{expected_port}/healthz"
        if "deploy/healthcheck.py" not in exec_post or health_url not in exec_post:
            result.fail("deploy/systemd/openshop.service", "ExecStartPost must run the loopback health check for the configured port")
        if not any(key == "WantedBy" and value == "multi-user.target" for key, value in install):
            result.fail("deploy/systemd/openshop.service", "install section must be enabled for multi-user.target")
        if health_path.is_file():
            try:
                health_text = self.read_text(health_path)
            except (OSError, UnicodeDecodeError):
                health_text = ""
            for needle in ("http://127.0.0.1:8780/healthz", "urllib.request", "verdict=PASS"):
                if needle not in health_text:
                    result.fail("deploy/healthcheck.py", f"health check is missing expected content: {needle}")
        if readme_path.is_file():
            try:
                readme = self.read_text(readme_path)
            except (OSError, UnicodeDecodeError):
                readme = ""
            for needle, label in (("回滚", "rollback sample"), ("授权", "authorization warning"), ("systemctl", "systemctl install sample")):
                if needle not in readme:
                    result.fail("deploy/README.md", f"missing documented {label}")

    def check_no_third_party(self, result: CheckResult) -> None:
        package_json = self.root / "package.json"
        if package_json.is_file():
            try:
                package = json.loads(self.read_text(package_json))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                result.fail("package.json", "cannot inspect package metadata")
                package = {}
            for field_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                deps = package.get(field_name)
                if deps:
                    result.fail("package.json", f"third-party {field_name} is not allowed")
        js_imports: dict[str, set[str]] = {}
        for path in self.files_with_suffix({".js", ".mjs"}):
            rel = self.relative(path)
            if not rel.startswith("web/"):
                continue
            try:
                text = self.read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            for specifier in self._js_imports(text):
                if not specifier.startswith((".", "/")) and not _is_absolute_network_url(specifier):
                    js_imports.setdefault(specifier, set()).add(rel)
        for specifier, files in sorted(js_imports.items()):
            result.fail(sorted(files)[0], f"bare JavaScript import is a third-party dependency: {specifier}")
        local_roots = {
            path.name for path in self.root.iterdir()
            if path.is_dir() and (path / "__init__.py").is_file()
        }
        local_roots.update({"apps", "packages"})
        allowed_modules = set(getattr(sys, "stdlib_module_names", ())) | local_roots | {"__future__"}
        runtime_prefixes = ("apps/", "packages/", "deploy/")
        third_party: dict[str, set[str]] = {}
        for path in self.files_with_suffix({".py"}):
            rel = self.relative(path)
            if not rel.startswith(runtime_prefixes):
                continue
            try:
                tree = ast.parse(self.read_text(path), filename=rel)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.split(".", 1)[0]]
                for name in names:
                    if name and name not in allowed_modules:
                        third_party.setdefault(name, set()).add(rel)
        for module, files in sorted(third_party.items()):
            result.fail(sorted(files)[0], f"non-stdlib Python import in runtime code: {module}")


def print_report(report: GateReport) -> None:
    print(f"REPOSITORY_GATE root={report.root} checks={len(report.checks)}")
    for check in report.checks:
        verdict = "PASS" if check.passed else "FAIL"
        print(
            f"{verdict} check={check.check_id} failures={len(check.failures)} "
            f"warnings={len(check.warnings)}"
        )
        for failure in check.failures[:MAX_FINDINGS_PRINTED]:
            print(f"  FAIL {failure.path}: {failure.message}")
        if len(check.failures) > MAX_FINDINGS_PRINTED:
            print(f"  FAIL ... {len(check.failures) - MAX_FINDINGS_PRINTED} more failure(s)")
        for warning in check.warnings[:10]:
            print(f"  WARN {warning.path}: {warning.message}")
        if len(check.warnings) > 10:
            print(f"  WARN ... {len(check.warnings) - 10} more warning(s)")
    verdict = "PASS" if report.passed else "FAIL"
    print(
        f"REPOSITORY_GATE verdict={verdict} checks={len(report.checks)} "
        f"failures={len(report.failures)} warnings={len(report.warnings)}"
    )


FIXTURE_CSS = """:root {
  --ink: #221b16;
  --paper: #f6f1e8;
}
body { color: var(--ink); background: var(--paper); }
"""

FIXTURE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>OpenShop fixture</title>
  <link rel="stylesheet" href="/web/css/base.css">
</head>
<body>
  <h1>OpenShop fixture</h1>
  <img src="/web/assets/brand/logo.svg" alt="OpenShop mark">
  <script type="module" src="/web/js/home.js"></script>
</body>
</html>
"""

FIXTURE_README = """# OpenShop 开集

OpenShop 开集 is a fictional marketplace fixture. Runtime prerequisite: Python 3.10+.

python3 -B apps/storefront/server.py --host 127.0.0.1 --port 8780 --data-dir /tmp/openshop-fixture

Demo accounts: demo_buyer and demo_seller. 官方自营、品牌旗舰与源头拼团三种供给。
所有商品与人物均为虚构；支付为模拟；本项目为非生产级演示。

Tests: python3 -B -m unittest discover -s tests -v
Gate: python3 -B tools/check_repository.py --check
License: [Apache-2.0](LICENSE).
"""

FIXTURE_CI = """# SPDX-License-Identifier: Apache-2.0
name: ci

on:
  push:
    branches: [main, master]
  pull_request:

permissions:
  contents: read

jobs:
  verify:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - name: tests
        run: python3 -B -m unittest discover -s tests -v
      - name: gate selftest
        run: python3 -B tools/check_repository.py --selftest
      - name: gate
        run: python3 -B tools/check_repository.py --check
"""

FIXTURE_UNIT = """[Unit]
Description=OpenShop fixture (template only)
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=openshop
Group=openshop
WorkingDirectory=/opt/openshop/current
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=OPENSHOP_SECURE_COOKIES=1
StateDirectory=openshop
StateDirectoryMode=0700
ExecStart=/usr/bin/python3 -B apps/storefront/server.py --host 127.0.0.1 --port 8780 --data-dir /var/lib/openshop
ExecStartPost=/usr/bin/python3 -B deploy/healthcheck.py --url http://127.0.0.1:8780/healthz --wait-seconds 10
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
KillSignal=SIGTERM
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
IPAddressAllow=127.0.0.1/32 ::1/128
IPAddressDeny=any
StandardOutput=journal
StandardError=journal
SyslogIdentifier=openshop
TasksMax=64
LimitNOFILE=4096

[Install]
WantedBy=multi-user.target
"""

FIXTURE_HEALTHCHECK = """#!/usr/bin/env python3
from __future__ import annotations

import urllib.request


def main() -> int:
    with urllib.request.urlopen("http://127.0.0.1:8780/healthz", timeout=3) as response:
        if response.status == 200 and response.read(64).strip() == b"ok":
            print("HEALTHCHECK verdict=PASS")
            return 0
    print("HEALTHCHECK verdict=FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
"""

FIXTURE_ROUTE = """from __future__ import annotations


def register(router) -> None:
    @router.add("GET", "/api/fixture", auth="public")
    def fixture(ctx):
        return {"ok": True}
"""

FIXTURE_PAGE = """export async function render(ctx) {
  ctx.setTitle("OpenShop fixture");
  ctx.root.textContent = "OpenShop fixture";
}
"""

FIXTURE_INDEXED_PAGE = """export async function render(ctx) {
  ctx.setTitle("OpenShop fixture home");
  ctx.root.textContent = "OpenShop fixture home";
}
"""


def _fixture_write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_fixture(root: Path) -> None:
    files = {
        "README.md": FIXTURE_README,
        "CONTRIBUTING.md": "# Contributing\n\nKeep runtime dependencies empty.\n",
        "SECURITY.md": "# Security\n\nReport privately to maintainers.\n",
        "CODE_OF_CONDUCT.md": "# Code of Conduct\n\nBe respectful.\n",
        "CHANGELOG.md": "# Changelog\n\n## [0.1.0] - unreleased\n",
        "LICENSE": (
            "Apache License\nVersion 2.0, January 2004\n"
            "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n"
            "END OF TERMS AND CONDITIONS\n"
        ),
        "package.json": json.dumps(
            {"name": "openshop-fixture", "version": "0.1.0", "private": True, "type": "module"},
            indent=2,
        ) + "\n",
        ".github/workflows/ci.yml": FIXTURE_CI,
        "deploy/README.md": (
            "# Deploy fixture\n\n"
            "Template install/rollback sample. 所有安装动作需要明确授权。\n"
            "Use systemctl only after approval; 回滚前先停服务并备份数据。\n"
        ),
        "deploy/healthcheck.py": FIXTURE_HEALTHCHECK,
        "deploy/systemd/openshop.service": FIXTURE_UNIT,
        "docs/INSTALL.md": "# Install fixture\n\nSee [deploy](../deploy/README.md) for the template.\n",
        "docs/OPEN-SOURCE.md": "# Open source fixture\n\nApache-2.0.\n",
        "docs/RELEASE.md": "# Release fixture\n\nNot released.\n",
        "docs/architecture.md": "# Architecture fixture\n\nStdlib and SQLite.\n",
        "docs/product.md": "# Product fixture\n\nFictional commerce.\n",
        "docs/design.md": "# Design fixture\n\nOriginal visuals.\n",
        "docs/api-contract.md": "# API fixture\n\nSame-origin JSON.\n",
        "docs/data-contract.md": "# Data fixture\n\nFictional catalog.\n",
        "apps/storefront/server.py": (
            "#!/usr/bin/env python3\nfrom __future__ import annotations\n\n"
            "APP_VERSION = \"0.1.0\"\n\n\n"
            "def main() -> int:\n    return 0\n\n\n"
            "if __name__ == \"__main__\":\n    raise SystemExit(main())\n"
        ),
        "apps/storefront/routes/catalog.py": FIXTURE_ROUTE,
        "apps/storefront/routes/trade.py": FIXTURE_ROUTE,
        "apps/storefront/routes/seller.py": FIXTURE_ROUTE,
        "web/index.html": FIXTURE_HTML,
        "web/css/base.css": FIXTURE_CSS,
        "web/js/home.js": FIXTURE_INDEXED_PAGE,
        "web/js/pages/home.js": FIXTURE_PAGE,
        "web/js/pages/catalog.js": FIXTURE_PAGE,
        "web/js/pages/product.js": FIXTURE_PAGE,
        "web/js/pages/cart.js": FIXTURE_PAGE,
        "web/js/pages/checkout.js": FIXTURE_PAGE,
        "web/js/pages/account.js": FIXTURE_PAGE,
        "web/js/pages/order.js": FIXTURE_PAGE,
        "web/js/pages/group.js": FIXTURE_PAGE,
        "web/js/pages/seller.js": FIXTURE_PAGE,
        "web/js/pages/about.js": FIXTURE_PAGE,
        "web/js/pages/help.js": FIXTURE_PAGE,
        "web/assets/brand/logo.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<rect width="10" height="10" fill="#d7462c"/></svg>\n'
        ),
        "data/catalog.json": json.dumps(
            {
                "version": "0.1.0",
                "currency": "CNY",
                "products": [{"slug": "fixture", "cover": "/web/assets/brand/logo.svg"}],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        "tests/test_smoke.py": (
            "from __future__ import annotations\n\nimport unittest\n\n\n"
            "class SmokeTest(unittest.TestCase):\n"
            "    def test_fixture(self) -> None:\n        self.assertTrue(True)\n"
        ),
    }
    for rel, content in files.items():
        _fixture_write(root, rel, content)


def _replace_file(root: Path, rel: str, old: str, new: str, *, count: int = -1) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"fixture mutation anchor not found in {rel}: {old!r}")
    _fixture_write(root, rel, text.replace(old, new, count))


def _mutate_aws_key(root: Path) -> None:
    fake_key = "AKIA" + "ABCDEFGHIJKLMNOP"
    _fixture_write(root, "apps/storefront/leak_probe.py", 'AWS_ACCESS_KEY_ID = "' + fake_key + '"\n')


NEGATIVE_CONTROL_BUILDERS: tuple[tuple[str, str, Callable[[Path], None]], ...] = (
    (
        "required_files",
        "remove CONTRIBUTING.md",
        lambda root: (root / "CONTRIBUTING.md").unlink(),
    ),
    (
        "test_suite",
        "remove the unittest module",
        lambda root: (root / "tests/test_smoke.py").unlink(),
    ),
    (
        "readme_contract",
        "remove the explicit --data-dir flag",
        lambda root: _replace_file(root, "README.md", "--data-dir", "--db-dir"),
    ),
    (
        "license",
        "replace Apache license text",
        lambda root: _fixture_write(root, "LICENSE", "MIT License\n"),
    ),
    (
        "route_surface",
        "route module without router.add",
        lambda root: _fixture_write(root, "apps/storefront/routes/trade.py", "def register(router):\n    return None\n"),
    ),
    (
        "placeholder_stubs",
        "add a TODO marker to a page",
        lambda root: _fixture_write(root, "web/js/pages/checkout.js", FIXTURE_PAGE + "\n// TODO: finish checkout\n"),
    ),
    (
        "json_syntax",
        "corrupt catalog JSON",
        lambda root: _fixture_write(root, "data/catalog.json", '{"products": [}\n'),
    ),
    (
        "python_syntax",
        "introduce a Python syntax error",
        lambda root: _fixture_write(
            root,
            "apps/storefront/server.py",
            (root / "apps/storefront/server.py").read_text(encoding="utf-8") + "\ndef broken(:\n",
        ),
    ),
    (
        "js_syntax",
        "introduce a JS syntax error",
        lambda root: _fixture_write(root, "web/js/pages/checkout.js", FIXTURE_PAGE + "\nconst broken = ;\n"),
    ),
    (
        "html_syntax",
        "remove a closing body tag",
        lambda root: _replace_file(root, "web/index.html", "</body>", ""),
    ),
    (
        "inline_scripts",
        "add an inline script",
        lambda root: _replace_file(
            root,
            "web/index.html",
            "</body>",
            "  <script>window.fixture = 1;</script>\n</body>",
        ),
    ),
    (
        "external_resources",
        "load a remote script",
        lambda root: _replace_file(
            root,
            "web/index.html",
            "</body>",
            '  <script src="https://cdn.example.invalid/fixture.js"></script>\n</body>',
        ),
    ),
    (
        "secret_patterns",
        "insert a fake AWS access key",
        _mutate_aws_key,
    ),
    (
        "asset_references",
        "reference a missing local image",
        lambda root: _replace_file(root, "web/index.html", "logo.svg", "missing.svg"),
    ),
    (
        "markdown_links",
        "add a dead local documentation link",
        lambda root: _fixture_write(
            root,
            "README.md",
            (root / "README.md").read_text(encoding="utf-8") + "\n[missing](docs/NOT-FOUND.md)\n",
        ),
    ),
    (
        "css_tokens",
        "duplicate a :root design token",
        lambda root: _fixture_write(root, "web/css/conflict.css", ":root {\n  --ink: #000000;\n}\n"),
    ),
    (
        "ci_policy",
        "grant contents write permission",
        lambda root: _replace_file(root, ".github/workflows/ci.yml", "contents: read", "contents: write", count=1),
    ),
    (
        "deploy_policy",
        "run the service as root",
        lambda root: _replace_file(root, "deploy/systemd/openshop.service", "User=openshop", "User=root"),
    ),
    (
        "no_third_party",
        "import a third-party Python module",
        lambda root: _fixture_write(root, "apps/storefront/third_party_probe.py", "import requests\n"),
    ),
)


def selftest() -> int:
    failures: list[str] = []
    negative_ids = {check_id for check_id, _, _ in NEGATIVE_CONTROL_BUILDERS}
    if len(negative_ids) != len(NEGATIVE_CONTROL_BUILDERS):
        print("SELFTEST verdict=FAIL reason=duplicate-negative-check-ids")
        return 1
    with tempfile.TemporaryDirectory(prefix="openshop-gate-selftest-") as temp_dir:
        base = Path(temp_dir) / "positive"
        base.mkdir()
        _build_fixture(base)
        baseline = RepositoryGate(base).run()
        baseline_ids = {check.check_id for check in baseline.checks}
        uncovered = sorted(negative_ids - baseline_ids)
        print(f"POSITIVE_CONTROL fixture={base} checks={len(baseline.checks)}")
        if not baseline.passed:
            print("POSITIVE_CONTROL verdict=FAIL")
            for check in baseline.checks:
                for finding in check.failures:
                    print(f"  POSITIVE_FAIL check={check.check_id} {finding.path}: {finding.message}")
            failures.append("positive fixture did not pass")
        elif uncovered:
            print(f"POSITIVE_CONTROL verdict=FAIL reason=uncovered-check-ids:{','.join(uncovered)}")
            failures.append("selftest does not cover all registered checks")
        else:
            print(f"POSITIVE_CONTROL verdict=PASS warnings={len(baseline.warnings)}")
        if failures:
            print(f"SELFTEST verdict=FAIL cases={len(NEGATIVE_CONTROL_BUILDERS) + 1}")
            return 1

        for check_id, description, mutate in NEGATIVE_CONTROL_BUILDERS:
            case_root = Path(temp_dir) / f"negative-{check_id}"
            shutil.copytree(base, case_root)
            try:
                mutate(case_root)
                report = RepositoryGate(case_root).run()
            except Exception as exc:  # noqa: BLE001
                print(
                    f"NEGATIVE_CONTROL check={check_id} mutation={description!r} "
                    f"verdict=FAIL reason=fixture-error:{type(exc).__name__}"
                )
                failures.append(f"{check_id}: fixture error")
                continue
            if check_id in report.failure_checks:
                others = sorted(report.failure_checks - {check_id})
                suffix = f" other_failures={','.join(others)}" if others else ""
                print(
                    f"NEGATIVE_CONTROL check={check_id} mutation={description!r} "
                    f"verdict=PASS detected={check_id}{suffix}"
                )
            else:
                print(
                    f"NEGATIVE_CONTROL check={check_id} mutation={description!r} "
                    f"verdict=FAIL detected=none failures={sorted(report.failure_checks)}"
                )
                failures.append(f"{check_id}: negative control was not detected")

    cases = len(NEGATIVE_CONTROL_BUILDERS) + 1
    if failures:
        for failure in failures:
            print(f"SELFTEST_FAIL {failure}")
        print(f"SELFTEST verdict=FAIL cases={cases}")
        return 1
    print(
        f"SELFTEST verdict=PASS cases={cases} "
        f"negatives={len(NEGATIVE_CONTROL_BUILDERS)}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenShop fail-closed repository gate")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="audit the repository")
    mode.add_argument("--selftest", action="store_true", help="run positive/negative fixture controls")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="repository root for --check")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    report = RepositoryGate(args.root).run()
    print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
