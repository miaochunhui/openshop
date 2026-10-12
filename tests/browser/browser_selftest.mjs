#!/usr/bin/env node
// Prove the external-request guard can go red in a real Chromium browser.
// The dirty page attempts only a blocked external subresource; network access
// is intercepted before it leaves the browser.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  GuardViolation,
  assertNoExternalRequests,
  isExternalUrl,
  makeGuard,
  writeJson,
} from "./browser_guard.mjs";

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) throw new Error("unexpected argument: " + token);
    const key = token.slice(2);
    const value = argv[i + 1];
    if (!value || value.startsWith("--")) throw new Error("missing value for --" + key);
    args[key] = value;
    i += 1;
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv);
  const chromiumPath = args.chromium || "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome";
  const playwrightPath = args.playwright || "/opt/dsh/app-master-0.1.6-alpha.2/node_modules/.pnpm/node_modules/playwright/index.mjs";
  const outDir = args["out-dir"] || "/hf3fs-jd/data/tmp/openshop/qa/selftest-browser";
  if (!outDir.startsWith("/hf3fs-jd/data/tmp/openshop/")) {
    throw new Error("selftest output must be under /hf3fs-jd/data/tmp/openshop/, got " + outDir);
  }
  fs.mkdirSync(outDir, { recursive: true });
  if (!fs.existsSync(playwrightPath)) throw new Error("playwright module missing: " + playwrightPath);
  if (!fs.existsSync(chromiumPath)) throw new Error("chromium binary missing: " + chromiumPath);
  const { chromium } = await import(pathToFileURL(playwrightPath).href);
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromiumPath,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const baseUrl = "http://127.0.0.1:65535";
  const cases = [];
  let verdict = "FAIL";
  try {
    const clean = await browser.newPage();
    const cleanGuard = makeGuard(clean, { baseUrl, label: "clean" });
    await clean.setContent("<!doctype html><html><body><h1>clean</h1></body></html>");
    await clean.waitForTimeout(300);
    assertNoExternalRequests(cleanGuard);
    cases.push({ case: "clean_local_page", expected: "GREEN", observed: "GREEN" });
    await clean.close();

    const dirty = await browser.newPage();
    const dirtyGuard = makeGuard(dirty, { baseUrl, label: "dirty-external-probe" });
    const probeUrl = "https://example.invalid/openshop-qa-external-probe.png";
    if (!isExternalUrl(probeUrl, baseUrl)) throw new Error("guard classifier failed to flag external probe URL");
    await dirty.setContent(
      "<!doctype html><html><body><h1>dirty</h1><img id=\"probe\" alt=\"probe\" src=\"" + probeUrl + "\"></body></html>",
    );
    const deadline = Date.now() + 5000;
    while (dirtyGuard.externalRequests.length === 0 && Date.now() < deadline) {
      await dirty.waitForTimeout(100);
    }
    if (dirtyGuard.externalRequests.length === 0) {
      throw new Error("external request detector did not see the synthetic external image request");
    }
    let wentRed = false;
    let redDetail = "";
    try {
      assertNoExternalRequests(dirtyGuard);
    } catch (error) {
      if (!(error instanceof GuardViolation)) throw error;
      wentRed = true;
      redDetail = error.message;
    }
    if (!wentRed) throw new Error("external request guard stayed green on a dirty page");
    cases.push({
      case: "synthetic_external_request",
      expected: "RED",
      observed: "RED",
      detected_url: dirtyGuard.externalRequests[0].url,
      detail: redDetail.slice(0, 500),
    });
    await dirty.close();
    cases.push({
      case: "same_origin_allowed",
      expected: "GREEN",
      observed: isExternalUrl("http://127.0.0.1:8871/web/js/app.js", "http://127.0.0.1:8871") ? "RED" : "GREEN",
    });
    verdict = "PASS";
  } finally {
    await browser.close();
  }
  const summary = {
    suite: "selftest-external-request-detector",
    verdict,
    chromium: chromiumPath,
    playwright: playwrightPath,
    base_url: baseUrl,
    cases,
    finished_at: new Date().toISOString(),
  };
  await writeJson(path.join(outDir, "summary.json"), summary);
  if (verdict === "PASS") {
    console.log("SELFTEST external_request_detector verdict=PASS detail=synthetic external request was blocked and flagged RED");
    for (const item of cases) console.log("SELFTEST external_request_detector case=" + item.case + " expected=" + item.expected + " observed=" + item.observed);
  } else {
    console.log("SELFTEST external_request_detector verdict=FAIL");
  }
  console.log("SELFTEST external_request_detector summary=" + path.join(outDir, "summary.json"));
  process.exitCode = verdict === "PASS" ? 0 : 1;
}

main().catch((error) => {
  console.log("SELFTEST external_request_detector verdict=FAIL reason=" + error.name + ": " + String(error.message).slice(0, 1500));
  process.exitCode = 1;
});
