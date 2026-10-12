// Shared real-browser guards for OpenShop QA.
// No API mocking lives here: only page instrumentation, DOM assertions and
// aborting attempted external network requests.
import fs from "node:fs";
import path from "node:path";

export class GuardViolation extends Error {
  constructor(message) {
    super(message);
    this.name = "GuardViolation";
  }
}

export function isExternalUrl(rawUrl, baseUrl) {
  let url;
  let base;
  try {
    url = new URL(rawUrl);
    base = new URL(baseUrl);
  } catch {
    return false;
  }
  if (!["http:", "https:"].includes(url.protocol)) return false;
  return !(url.protocol === base.protocol && url.hostname === base.hostname && url.port === base.port);
}

export function makeGuard(page, { baseUrl, label }) {
  const guard = {
    label,
    consoleErrors: [],
    pageErrors: [],
    externalRequests: [],
    requestFailures: [],
  };
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    let location = {};
    try {
      location = message.location() || {};
    } catch {}
    guard.consoleErrors.push({
      page: label,
      text: message.text().slice(0, 2000),
      url: location.url || "",
      line: location.lineNumber ?? null,
      column: location.columnNumber ?? null,
    });
  });
  page.on("pageerror", (error) => {
    guard.pageErrors.push({
      page: label,
      message: String((error && (error.stack || error.message)) || error).slice(0, 4000),
    });
  });
  page.on("requestfailed", (request) => {
    const url = request.url();
    if (!url.startsWith("http://") && !url.startsWith("https://")) return;
    if (isExternalUrl(url, baseUrl)) return;
    guard.requestFailures.push({
      page: label,
      url,
      resourceType: request.resourceType(),
      failure: request.failure()?.errorText || "unknown",
    });
  });
  page.route("**/*", async (route) => {
    const request = route.request();
    const url = request.url();
    if (isExternalUrl(url, baseUrl)) {
      guard.externalRequests.push({
        page: label,
        url,
        method: request.method(),
        resourceType: request.resourceType(),
      });
      await route.abort("blockedbyclient").catch(() => {});
      return;
    }
    await route.continue().catch(() => {});
  });
  return guard;
}

export function assertNoExternalRequests(guardOrGuards) {
  const guards = Array.isArray(guardOrGuards) ? guardOrGuards : [guardOrGuards];
  const hits = guards.flatMap((g) => g.externalRequests || []);
  if (hits.length > 0) {
    throw new GuardViolation(
      "external network request(s) detected and blocked: " +
        hits.map((h) => h.url).join(", ").slice(0, 1200),
    );
  }
}

export function assertNoConsoleErrors(guardOrGuards) {
  const guards = Array.isArray(guardOrGuards) ? guardOrGuards : [guardOrGuards];
  const consoleErrors = guards.flatMap((g) => g.consoleErrors || []);
  const pageErrors = guards.flatMap((g) => g.pageErrors || []);
  if (consoleErrors.length || pageErrors.length) {
    const details = [
      ...pageErrors.slice(0, 3).map((e) => "pageerror: " + e.message.split("\n")[0]),
      ...consoleErrors.slice(0, 5).map((e) => "console.error: " + e.text),
    ];
    throw new GuardViolation(
      "browser console/page errors present: " + details.join(" | ").slice(0, 2000),
    );
  }
}

export async function bodySnippet(page, maxChars = 500) {
  try {
    const text = await page.locator("body").innerText({ timeout: 1500 });
    return text.replace(/\s+/g, " ").slice(0, maxChars);
  } catch {
    return "<body unavailable>";
  }
}

export async function waitForRoute(page, { label, contains = "", timeoutMs = 22000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastState = "";
  while (Date.now() < deadline) {
    if (contains && !page.url().includes(contains)) {
      lastState = "url=" + page.url();
      await page.waitForTimeout(150);
      continue;
    }
    const errors = page.locator("#app .boot-error, #app .error-page");
    if ((await errors.count().catch(() => 0)) > 0) {
      const visible = await errors.first().isVisible().catch(() => false);
      if (visible) {
        const text = (await errors.first().innerText().catch(() => "")).replace(/\s+/g, " ").slice(0, 500);
        throw new GuardViolation(label + ": application rendered an error state: " + text);
      }
    }
    const skeleton = await page.locator("#app .route-loading").count().catch(() => 0);
    const headings = page.locator("#app h1");
    const headingCount = await headings.count().catch(() => 0);
    let visibleHeading = false;
    for (let i = 0; i < headingCount; i += 1) {
      if (await headings.nth(i).isVisible().catch(() => false)) {
        visibleHeading = true;
        break;
      }
    }
    if (visibleHeading && skeleton === 0) {
      await page.waitForTimeout(200);
      const skeletonAfter = await page.locator("#app .route-loading").count().catch(() => 0);
      const headingStill = await headings.first().isVisible().catch(() => false);
      if (skeletonAfter === 0 && headingStill) return;
    }
    lastState = "url=" + page.url() + " h1=" + headingCount + " skeleton=" + skeleton;
    await page.waitForTimeout(200);
  }
  const snippet = await bodySnippet(page, 700);
  throw new GuardViolation(label + ": route did not become ready within " + timeoutMs + "ms; " + lastState + "; body=" + snippet);
}

export async function firstVisible(candidates, { timeoutMs = 12000, description = "element", page = null, required = true } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    for (const locator of candidates) {
      let count = 0;
      try {
        count = await locator.count();
      } catch (error) {
        lastError = String(error);
        continue;
      }
      for (let i = 0; i < count; i += 1) {
        const item = locator.nth(i);
        try {
          if (await item.isVisible()) return item;
        } catch (error) {
          lastError = String(error);
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!required) return null;
  const state = page ? "; url=" + page.url() + "; body=" + (await bodySnippet(page, 400)) : "";
  throw new GuardViolation(
    "could not find visible " + description + " within " + timeoutMs + "ms" + state +
      (lastError ? "; lastError=" + lastError.slice(0, 300) : ""),
  );
}

export async function waitUntil(fn, { timeoutMs = 12000, intervalMs = 200, description = "condition" } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new GuardViolation(
    "timed out waiting for " + description + " after " + timeoutMs + "ms" +
      (lastError ? ": " + String(lastError).slice(0, 500) : ""),
  );
}

export async function assertNoHorizontalOverflow(page, label) {
  const info = await page.evaluate((pageLabel) => {
    const width = Math.max(
      document.documentElement ? document.documentElement.scrollWidth : 0,
      document.body ? document.body.scrollWidth : 0,
    );
    const viewport = window.innerWidth;
    const offenders = [];
    if (document.body && width > viewport + 1) {
      for (const el of Array.from(document.body.querySelectorAll("*"))) {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = window.getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") continue;
        if (rect.right > viewport + 1 || rect.left < -1) {
          offenders.push({
            tag: el.tagName.toLowerCase(),
            id: el.id || "",
            className: typeof el.className === "string" ? el.className.slice(0, 120) : "",
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
          });
          if (offenders.length >= 8) break;
        }
      }
    }
    return {
      label: pageLabel,
      viewportWidth: viewport,
      documentWidth: width,
      bodyScrollWidth: document.body ? document.body.scrollWidth : null,
      horizontalOverflow: width > viewport + 1,
      offenders,
    };
  }, label);
  if (info.horizontalOverflow) {
    throw new GuardViolation(
      label + ": horizontal overflow detected: " + JSON.stringify(info).slice(0, 1200),
    );
  }
  return info;
}

export async function assertSinglePageHeading(page, label) {
  const headings = page.locator("#app h1");
  const count = await headings.count();
  let visible = 0;
  for (let i = 0; i < count; i += 1) {
    if (await headings.nth(i).isVisible().catch(() => false)) visible += 1;
  }
  if (visible !== 1) {
    throw new GuardViolation(label + ": expected exactly one visible #app h1, got " + visible + " (total=" + count + ")");
  }
}

export async function assertPageText(page, regex, description) {
  const text = await page.locator("body").innerText({ timeout: 3000 });
  if (!regex.test(text)) {
    throw new GuardViolation(description + ": page text does not match " + String(regex) + "; body=" + text.replace(/\s+/g, " ").slice(0, 500));
  }
  return text;
}

export async function screenshot(page, screenshotsDir, name, { fullPage = true } = {}) {
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*\.png$/.test(name)) {
    throw new Error("unsafe screenshot filename: " + name);
  }
  fs.mkdirSync(screenshotsDir, { recursive: true });
  const target = path.join(screenshotsDir, name);
  await page.screenshot({ path: target, fullPage });
  const stat = fs.statSync(target);
  if (!stat.isFile() || stat.size <= 0) {
    throw new GuardViolation("screenshot was not written or is empty: " + target);
  }
  return { name, path: target, bytes: stat.size };
}

export async function scanExternalResourceRefs(page, baseUrl) {
  return page.evaluate((base) => {
    const baseUrl = new URL(base);
    const attributes = [
      ["img", "src"], ["script", "src"], ["iframe", "src"], ["source", "src"],
      ["video", "src"], ["audio", "src"], ["link", "href"],
    ];
    const results = [];
    for (const [tag, attr] of attributes) {
      for (const el of Array.from(document.querySelectorAll(tag + "[" + attr + "]"))) {
        const raw = el.getAttribute(attr);
        if (!raw) continue;
        if (tag === "link") {
          const rel = (el.getAttribute("rel") || "").toLowerCase();
          if (!["stylesheet", "preload", "modulepreload", "icon", "manifest"].includes(rel)) continue;
        }
        let url;
        try {
          url = new URL(raw, window.location.href);
        } catch {
          continue;
        }
        if (!["http:", "https:"].includes(url.protocol)) continue;
        if (url.origin !== baseUrl.origin && url.origin !== window.location.origin) {
          results.push({ tag, attr, url: url.href });
        }
      }
    }
    return results;
  }, baseUrl);
}

export function guardEvidence(guards) {
  return {
    console_errors: guards.flatMap((g) => g.consoleErrors),
    page_errors: guards.flatMap((g) => g.pageErrors),
    external_requests: guards.flatMap((g) => g.externalRequests),
    request_failures: guards.flatMap((g) => g.requestFailures),
  };
}

export function writeJson(targetPath, payload) {
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.writeFileSync(targetPath, JSON.stringify(payload, null, 2) + "\n", { encoding: "utf-8" });
}
