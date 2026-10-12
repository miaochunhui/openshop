#!/usr/bin/env python3
"""Formal tests for tools/rehearse_release.py negative controls.

These run without a release tag, Node or Chromium. They invoke the tool's
temporary-fixture self-test and verify that dirty trees, bad tags, unsafe
archives, and unhealthy endpoints are each detected rather than auto-passed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "rehearse_release.py"
REQUIRED_CONTROLS = {
    "clean_tree_accepted",
    "dirty_tree_rejected",
    "annotated_tag_accepted",
    "tag_not_at_head_rejected",
    "lightweight_tag_rejected",
    "missing_tag_rejected",
    "safe_archive_accepted",
    "runtime_artifact_rejected",
    "traversal_archive_rejected",
    "unhealthy_endpoint_rejected",
}


class ReleaseRehearsalTests(unittest.TestCase):
    def test_selftest_rejects_unhealthy_release_fixtures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openshop-release-rehearsal-test-") as tmp:
            out_dir = Path(tmp) / "evidence"
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(TOOL),
                    "--selftest",
                    "--out-dir",
                    str(out_dir),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            self.assertEqual(
                result.returncode,
                0,
                "release rehearsal selftest failed\n"
                + f"stdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}",
            )
            summary_path = out_dir / "release-selftest-summary.json"
            self.assertTrue(summary_path.is_file(), "selftest summary was not written")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertFalse(summary.get("published"))
            self.assertEqual(summary.get("verdict"), "PASS")
            controls = {row["name"]: row for row in summary.get("controls", [])}
            self.assertEqual(set(controls), REQUIRED_CONTROLS)
            for name in REQUIRED_CONTROLS:
                row = controls[name]
                self.assertEqual(row.get("verdict"), "PASS", name)
                expected = row.get("expected")
                observed = row.get("observed")
                self.assertIn(expected, {"accept", "reject"})
                self.assertEqual(observed, "accepted" if expected == "accept" else "rejected", name)
            rejected = {
                name for name, row in controls.items() if row.get("expected") == "reject"
            }
            self.assertGreaterEqual(len(rejected), 6)
            self.assertIn("dirty_tree_rejected", rejected)
            self.assertIn("runtime_artifact_rejected", rejected)
            self.assertIn("unhealthy_endpoint_rejected", rejected)


if __name__ == "__main__":
    unittest.main()
