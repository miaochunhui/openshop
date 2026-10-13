#!/usr/bin/env node
// Live Chromium acceptance for the OpenShop AI mediator.
// Requires a running server with a real configured model. It never mocks API
// responses and never creates an order or payment.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const DEFAULT_PLAYWRIGHT = "/opt/dsh/app-master-0.1.6-alpha.2/node_modules/.pnpm/node_modules/playwright/index.mjs";
const DEFAULT_CHROMIUM = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome";

function arg(name, fallback) {
  const index = process.argv.indexOf("--" + name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function stamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return String(d.getFullYear()) + pad(d.getMonth() + 1) + pad(d.getDate()) + "T" + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
}

async function overflow(page) {
  return await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    overflow: document.documentElement.scrollWidth > window.innerWidth + 1,
  }));
}

async function main() {
  const baseUrl = arg("base-url", "http://127.0.0.1:8895");
  const outRoot = arg("out-root", "/hf3fs-jd/data/tmp/openshop/ai-e2e/browser-live-" + stamp());
  const playwrightPath = arg("playwright", DEFAULT_PLAYWRIGHT);
  const chromiumPath = arg("chromium", DEFAULT_CHROMIUM);
  fs.mkdirSync(outRoot, { recursive: true });
  const screenshots = path.join(outRoot, "screenshots");
  fs.mkdirSync(screenshots, { recursive: true });
  const summaryPath = path.join(outRoot, "browser-summary.json");
  const errors = { console: [], page: [], failedRequests: [], externalRequests: [] };
  let mediatePayload = null;
  let adoptPayload = null;
  const summary = {
    started_at: new Date().toISOString(),
    base_url: baseUrl,
    browser: chromiumPath,
    real_api: true,
    mocked_api: false,
    stages: [],
    screenshots: [],
    errors,
    verdict: "FAIL",
    current_stage: "boot",
  };
  let browser = null;
  let page = null;
  const writeSummary = () => {
    summary.finished_at = new Date().toISOString();
    summary.errors = errors;
    fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2) + "\n", "utf8");
  };
  try {
    const { chromium } = await import(pathToFileURL(playwrightPath).href);
    browser = await chromium.launch({ executablePath: chromiumPath, headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN", timezoneId: "Asia/Shanghai" });
    page = await context.newPage();
    page.on("console", (message) => {
      if (message.type() === "error") errors.console.push(message.text().slice(0, 500));
    });
    page.on("pageerror", (error) => errors.page.push(String(error && error.message || error).slice(0, 500)));
    page.on("requestfailed", (request) => errors.failedRequests.push({ url: request.url(), failure: request.failure()?.errorText || "unknown" }));
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.origin !== new URL(baseUrl).origin) errors.externalRequests.push(request.url());
    });
    page.on("response", async (response) => {
      try {
        if (response.url().includes("/api/ai/mediate") && response.status() === 200) {
          const json = await response.json();
          mediatePayload = json && json.data ? json.data : null;
        }
        if (response.url().includes("/api/ai/plans/") && response.url().endsWith("/adopt") && response.status() === 200) {
          const json = await response.json();
          adoptPayload = json && json.data ? json.data : null;
        }
      } catch (_) {
        // Response parsing is evidence-only; UI assertions remain authoritative.
      }
    });

    summary.current_stage = "open-ai-page";
    await page.goto(baseUrl.replace(/\/$/, "") + "/ai", { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForSelector('[data-testid="ai-page"]', { timeout: 30000 });
    await page.waitForSelector('[data-testid="ai-mode"]', { timeout: 30000 });
    const modeText = (await page.locator('[data-testid="ai-mode"]').innerText()).trim();
    if (!modeText.includes("在线")) throw new Error("AI status is not live: " + modeText);
    const h1 = (await page.locator("h1").first().innerText()).trim();
    summary.stages.push({ id: "AI-01-page", status: "PASS", mode: modeText, heading: h1 });

    summary.current_stage = "login";
    await page.waitForSelector('[data-testid="ai-login"]', { timeout: 20000 });
    await page.locator('[data-testid="ai-login"]').first().click();
    await page.waitForSelector('[data-testid="demo-buyer"]', { timeout: 15000 });
    await page.locator('[data-testid="demo-buyer"]').first().click();
    await page.waitForSelector('#modal-root .modal', { state: "detached", timeout: 20000 }).catch(() => {});
    await page.waitForSelector('[data-testid="ai-quota"]', { timeout: 20000 });
    await page.waitForSelector('#ai-input:not([disabled])', { timeout: 20000 });
    const sessionCookie = (await context.cookies(baseUrl)).find((cookie) => cookie.name === "openshop_session");
    if (!sessionCookie || !sessionCookie.secure || !sessionCookie.httpOnly) {
      throw new Error("public HTTPS session cookie must be Secure and HttpOnly");
    }
    const healthResponse = await context.request.get(baseUrl.replace(/\/$/, "") + "/api/health");
    const publicHeaders = healthResponse.headers();
    const hsts = String(publicHeaders["strict-transport-security"] || "");
    const csp = String(publicHeaders["content-security-policy"] || "");
    if (!hsts.includes("max-age=") || !csp.includes("default-src 'self'")) {
      throw new Error("HSTS or CSP header missing on public HTTPS");
    }
    summary.security = {
      https: new URL(baseUrl).protocol === "https:",
      hsts,
      csp_present: csp.includes("default-src 'self'"),
      session_cookie_secure: Boolean(sessionCookie && sessionCookie.secure),
      session_cookie_http_only: Boolean(sessionCookie && sessionCookie.httpOnly),
    };
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(250);
    await page.screenshot({ path: path.join(screenshots, "01-ai-page-desktop-1440.png") });
    summary.screenshots.push(path.join(screenshots, "01-ai-page-desktop-1440.png"));
    summary.stages.push({ id: "AI-02-login", status: "PASS", user_surface: "demo-buyer modal" });
    summary.stages.push({ id: "AI-02b-security", status: "PASS", hsts: Boolean(hsts), secure_cookie: Boolean(sessionCookie && sessionCookie.secure) });

    summary.current_stage = "request-real-ai";
    const brief = "我想买一副降噪耳机，预算 1000 元以内，希望两天内到，帮我比较自营和品牌店，并给一个现在就能加入购物车的方案。";
    await page.locator('[data-testid="ai-input"]').fill(brief);
    const enabled = await page.locator('[data-testid="ai-submit"]').isEnabled();
    if (!enabled) throw new Error("AI submit button is not enabled after login/status");
    await page.locator('[data-testid="ai-submit"]').click();
    await page.waitForSelector('[data-testid="ai-loading"]', { timeout: 10000 }).catch(() => {});
    await page.waitForSelector('[data-testid="ai-answer"]', { timeout: 120000 });
    await page.waitForSelector('[data-testid="ai-recommendation"]', { timeout: 120000 });
    if (!mediatePayload) throw new Error("No successful /api/ai/mediate response was observed");
    if (!mediatePayload.recommendation || !mediatePayload.recommendation.plan_id) throw new Error("AI response has no adoptable plan");
    const reasonText = await page.locator('[data-testid="ai-reason"]').innerText();
    const recText = await page.locator('[data-testid="ai-recommendation"]').innerText();
    const priceText = await page.locator('[data-testid="ai-recommendation"] .price').first().innerText();
    if (!reasonText.includes("为什么是它")) throw new Error("Grounded reason is not rendered");
    if (errors.console.length || errors.page.length) throw new Error("Console/page errors before adoption");
    await page.evaluate(() => document.getElementById("ai-result-region")?.scrollIntoView({ block: "start" }));
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(screenshots, "02-ai-result-desktop-1440.png") });
    summary.screenshots.push(path.join(screenshots, "02-ai-result-desktop-1440.png"));
    summary.stages.push({
      id: "AI-03-real-mediation",
      status: "PASS",
      model_label: mediatePayload.model_label,
      latency_ms: mediatePayload.latency_ms,
      product: mediatePayload.recommendation.product.title,
      shop: mediatePayload.recommendation.shop.name,
      offer_id: mediatePayload.recommendation.offer_id,
      sku_id: mediatePayload.recommendation.sku_id,
      buy_mode: mediatePayload.recommendation.buy_mode,
      unit_price_cents: mediatePayload.recommendation.unit_price_cents,
      server_price_rendered: priceText,
      grounded_reason_present: true,
    });

    summary.current_stage = "adopt-plan";
    const urlBeforeAdopt = page.url();
    await page.locator('[data-testid="ai-adopt"]').first().click();
    await page.waitForSelector('[data-testid="ai-adopted"]', { timeout: 30000 });
    if (!adoptPayload) throw new Error("No successful adopt response was observed");
    const adoptedText = (await page.locator('[data-testid="ai-adopted"]').innerText()).trim();
    if (page.url() !== urlBeforeAdopt) throw new Error("Page unexpectedly navigated during adoption");
    const cartBadge = await page.locator("#cart-badge").innerText();
    await page.waitForTimeout(4200);
    await page.evaluate(() => document.getElementById("ai-result-region")?.scrollIntoView({ block: "start" }));
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(screenshots, "03-ai-adopted-desktop-1440.png") });
    summary.screenshots.push(path.join(screenshots, "03-ai-adopted-desktop-1440.png"));
    summary.stages.push({ id: "AI-04-adopt", status: "PASS", confirmation: adoptedText.slice(0, 160), cart_badge: cartBadge, page_stayed_on_ai: true });

    summary.current_stage = "mobile-390";
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(250);
    const mobileTopOverflow = await overflow(page);
    await page.screenshot({ path: path.join(screenshots, "04-ai-page-mobile-390.png") });
    summary.screenshots.push(path.join(screenshots, "04-ai-page-mobile-390.png"));
    await page.evaluate(() => document.getElementById("ai-result-region")?.scrollIntoView({ block: "start" }));
    await page.waitForTimeout(250);
    const mobileResultOverflow = await overflow(page);
    await page.screenshot({ path: path.join(screenshots, "05-ai-adopted-mobile-390.png") });
    summary.screenshots.push(path.join(screenshots, "05-ai-adopted-mobile-390.png"));
    summary.mobile = { top: mobileTopOverflow, result: mobileResultOverflow };
    if (mobileTopOverflow.overflow || mobileResultOverflow.overflow) throw new Error("Horizontal overflow at 390px");
    summary.stages.push({ id: "AI-05-mobile", status: "PASS", viewport: "390x844", overflow_top: mobileTopOverflow, overflow_result: mobileResultOverflow });

    summary.current_stage = "final-guards";
    if (errors.console.length) throw new Error("Console errors: " + errors.console.join(" | "));
    if (errors.page.length) throw new Error("Page errors: " + errors.page.join(" | "));
    if (errors.externalRequests.length) throw new Error("Unexpected external requests: " + errors.externalRequests.join(" | "));
    if (errors.failedRequests.length) throw new Error("Failed requests: " + JSON.stringify(errors.failedRequests));
    const errorVisible = await page.locator('[data-testid="ai-error"]:not([hidden])').count();
    if (errorVisible) throw new Error("Form error is visible after successful flow");
    summary.verdict = "PASS";
    summary.current_stage = "done";
    summary.mediate_plan_offer = mediatePayload.recommendation.offer_id;
    summary.adopt_cart_count = adoptPayload && adoptPayload.cart && adoptPayload.cart.summary ? adoptPayload.cart.summary.item_count : null;
    await context.close();
    await browser.close();
    browser = null;
    writeSummary();
    console.log(JSON.stringify(summary, null, 2));
    return 0;
  } catch (error) {
    summary.error = String(error && error.stack || error).slice(0, 4000);
    if (page) {
      try { await page.screenshot({ path: path.join(screenshots, "99-failure.png"), fullPage: true }); summary.screenshots.push(path.join(screenshots, "99-failure.png")); } catch (_) {}
    }
    writeSummary();
    console.log(JSON.stringify(summary, null, 2));
    return 1;
  } finally {
    if (browser) {
      try { await browser.close(); } catch (_) {}
    }
  }
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error(String(error && error.stack || error));
  process.exit(1);
});
