#!/usr/bin/env node
// Real-Chromium OpenShop acceptance. The backend is started by the caller
// (tools/qa/run_acceptance.py). This script never mocks API responses.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  GuardViolation,
  assertNoConsoleErrors,
  assertNoExternalRequests,
  assertNoHorizontalOverflow,
  assertSinglePageHeading,
  firstVisible,
  guardEvidence,
  makeGuard,
  scanExternalResourceRefs,
  screenshot as captureScreenshot,
  waitForRoute,
  waitUntil,
  writeJson,
} from "./browser_guard.mjs";
import {
  bodyText,
  clickAddToCart,
  escapeRegex,
  fetchJsonNode,
  fillPreferredField,
  formScopeFor,
  getApiData,
  maybeClickDialogButton,
  maybeDismissTransientOverlay,
  nowIso,
  openDemoLogin,
  sleep,
  waitForApiEvent,
} from "./e2e_support.mjs";

const ALLOWED_ROOT = "/hf3fs-jd/data/tmp/openshop";
const DEFAULT_PLAYWRIGHT = "/opt/dsh/app-master-0.1.6-alpha.2/node_modules/.pnpm/node_modules/playwright/index.mjs";
const DEFAULT_CHROMIUM = "/home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome";
const REQUIRED_SHOTS = [
  "01-home-desktop.png",
  "02-search-desktop.png",
  "03-product-desktop.png",
  "04-cart-desktop.png",
  "05-checkout-desktop.png",
  "06-order-paid-desktop.png",
  "07-seller-orders-desktop.png",
  "08-seller-shipped-desktop.png",
  "09-buyer-after-ship-desktop.png",
  "10-buyer-completed-desktop.png",
  "11-buyer-reviewed-desktop.png",
  "12-mobile-home-390.png",
  "13-mobile-order-390.png",
];
const JOURNEY_STAGES = [
  { id: "E2E-01-home", title: "home renders real catalog, no stub, no overflow", fn: stageHomeBrowse },
  { id: "E2E-02-search", title: "search from header -> result card -> product detail", fn: stageSearchAndOpenProduct },
  { id: "E2E-03-product", title: "product detail offer/SKU + add to cart", fn: stageAddToCart },
  { id: "E2E-04-login-cart", title: "one-click buyer login merges cart and cart DOM", fn: stageBuyerLoginAndCart },
  { id: "E2E-05-checkout", title: "checkout quote + new address + coupon via UI", fn: stageCheckoutAddressCoupon },
  { id: "E2E-06-pay", title: "single idempotent order + simulated demo payment + order DOM", fn: stageCreateOrderAndPay },
  { id: "E2E-07-seller", title: "one-click seller login + real ship interaction", fn: stageSellerShip },
  { id: "E2E-08-confirm", title: "buyer sees carrier/tracking then confirms receipt", fn: stageBuyerVisibilityConfirm },
  { id: "E2E-09-review", title: "buyer submits review and aggregate/review state changes", fn: stageReview },
  { id: "E2E-10-mobile", title: "real 390px mobile screenshots without overflow", fn: stageMobile },
];

const C = {
  startedAt: null,
  finishedAt: null,
  args: null,
  baseUrl: "",
  outDir: "",
  screenshotsDir: "",
  summaryPath: "",
  playwrightPath: DEFAULT_PLAYWRIGHT,
  chromiumPath: DEFAULT_CHROMIUM,
  browser: null,
  buyerContext: null,
  sellerContext: null,
  mobileContext: null,
  buyerPage: null,
  sellerPage: null,
  mobilePage: null,
  guards: [],
  stageResults: [],
  journeyAbortedAt: null,
  fatalError: null,
  currentStage: null,
  fixture: null,
  fixtureError: null,
  orderNo: null,
  buyerOrderPath: null,
  trackingNo: null,
  carrier: "青鸟速运",
  addressReceiver: "验收收货人",
  addressPhone: "13800000999",
  appliedCouponCode: null,
  appliedQuote: null,
  simulatedPaymentSeen: false,
  paymentDisclosureText: "",
  mobileViewportWidth: null,
  apiCalls: [],
  quoteEvents: [],
  orderCreateEvents: [],
  payEvents: [],
  addressEvents: [],
  shipEvents: [],
  reviewEvents: [],
  dialogs: [],
  screenshots: [],
  overflowReports: [],
  externalRefs: [],
  verdict: "FAIL",
  rc: 1,
  auditIssues: [],
  auditStage: null,
  requiredActions: {},
};

function oneLine(value, maxLength = 1600) {
  return String(value == null ? "" : value).replace(/\s+/g, " ").slice(0, maxLength);
}

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
  if (!args["base-url"]) throw new Error("--base-url is required");
  if (!args["out-dir"]) throw new Error("--out-dir is required");
  return args;
}

function assertLocalBaseUrl(baseUrl) {
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    throw new Error("invalid --base-url: " + baseUrl);
  }
  if (parsed.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(parsed.hostname)) {
    throw new Error("browser acceptance only targets a local QA server, got " + baseUrl);
  }
  return parsed.origin;
}

function prepareOutputDir(outDir) {
  if (!path.isAbsolute(outDir) || !outDir.startsWith(ALLOWED_ROOT + path.sep)) {
    throw new Error("out-dir must be under " + ALLOWED_ROOT + "/, got " + outDir);
  }
  fs.mkdirSync(outDir, { recursive: true });
  const realOut = fs.realpathSync(outDir);
  const realAllowed = fs.realpathSync(ALLOWED_ROOT);
  if (realOut !== realAllowed && !realOut.startsWith(realAllowed + path.sep)) {
    throw new Error("out-dir resolves outside 3FS tmp: " + realOut);
  }
  const entries = fs.readdirSync(outDir);
  if (entries.length > 0) {
    throw new Error("out-dir must be new or empty for fresh evidence; existing entries: " + entries.slice(0, 8).join(", "));
  }
  const screenshotsDir = path.join(outDir, "screenshots");
  fs.mkdirSync(screenshotsDir, { recursive: true });
  return { screenshotsDir, summaryPath: path.join(outDir, "browser-summary.json") };
}

async function fetchCatalogFixture(baseUrl) {
  const response = await fetchJsonNode(baseUrl + "/api/products?page_size=24");
  if (response.status !== 200) {
    throw new Error("GET /api/products -> HTTP " + response.status + "; body=" + oneLine(response.text, 300));
  }
  const data = response.json?.data;
  const items = data && Array.isArray(data.items) ? data.items : null;
  if (!items || items.length === 0) {
    throw new Error("GET /api/products returned no items; cannot run data-driven browser journey");
  }
  // The demo merchant can only ship offers from their own shop. Pick a
  // product whose serialized default offer is official self-operated so the
  // seller stage exercises real cross-session fulfilment rather than a
  // marketplace offer owned by a different shop.
  const candidates = items.filter((candidate) => candidate && typeof candidate.slug === "string" && typeof candidate.title === "string");
  for (const item of candidates) {
    const detailResponse = await fetchJsonNode(baseUrl + "/api/products/" + encodeURIComponent(item.slug));
    if (detailResponse.status !== 200) continue;
    const detail = detailResponse.json?.data;
    const offers = detail && Array.isArray(detail.offers) ? detail.offers : [];
    const defaultOffer = offers[0];
    const skus = detail && Array.isArray(detail.skus) ? detail.skus : [];
    if (
      defaultOffer && defaultOffer.shop && defaultOffer.shop.slug === "openshop-self" &&
      Number(defaultOffer.stock || 0) > 0 && skus.length > 0
    ) {
      return {
        id: item.id ?? null,
        slug: item.slug,
        title: item.title,
        query: item.title,
        price_cents: item.price_cents ?? null,
        self_offer_id: defaultOffer.id ?? null,
        self_shop_slug: defaultOffer.shop.slug,
      };
    }
  }
  throw new Error("no browsable product has an in-stock official self default offer for the merchant journey");
}

function normalizeAvailableCoupons(couponData) {
  const defs = new Map();
  for (const item of (couponData && couponData.available) || []) {
    if (item && typeof item.code === "string") defs.set(item.code, item);
  }
  const result = [];
  for (const raw of (couponData && couponData.mine) || []) {
    if (!raw || raw.status !== "AVAILABLE" || typeof raw.code !== "string") continue;
    const def = defs.get(raw.code) || {};
    const threshold = Number(raw.threshold_cents ?? def.threshold_cents ?? 0);
    const discount = Number(raw.discount_cents ?? def.discount_cents ?? 0);
    if (!Number.isFinite(threshold) || !Number.isFinite(discount) || discount <= 0) continue;
    result.push({
      code: raw.code,
      title: typeof raw.title === "string" && raw.title ? raw.title : (typeof def.title === "string" ? def.title : raw.code),
      threshold_cents: threshold,
      discount_cents: discount,
      scope: typeof raw.scope === "string" ? raw.scope : (typeof def.scope === "string" ? def.scope : "all"),
      user_coupon_id: raw.user_coupon_id ?? null,
    });
  }
  return result.sort((left, right) => right.discount_cents - left.discount_cents);
}

function latestSuccessfulQuote() {
  for (let i = C.quoteEvents.length - 1; i >= 0; i -= 1) {
    const event = C.quoteEvents[i];
    if (event && event.status === 200 && event.quote) return event;
  }
  return null;
}

function readOrderNoFromData(data) {
  if (!data || typeof data !== "object") return null;
  if (typeof data.order_no === "string") return data.order_no;
  if (data.order && typeof data.order.order_no === "string") return data.order.order_no;
  return null;
}

function installResponseJournal(page, label) {
  page.on("response", (response) => {
    (async () => {
      let parsedUrl;
      try {
        parsedUrl = new URL(response.url());
      } catch {
        return;
      }
      const request = response.request();
      const method = request.method();
      const pathName = parsedUrl.pathname;
      if (!pathName.startsWith("/api/")) return;
      const status = response.status();
      const call = { at: Date.now(), page: label, method, path: pathName, status };
      C.apiCalls.push(call);
      const interesting = method === "POST" && (
        pathName === "/api/orders" ||
        pathName === "/api/checkout/quote" ||
        pathName === "/api/addresses" ||
        /^\/api\/orders\/[^/]+\/pay$/.test(pathName) ||
        /^\/api\/orders\/[^/]+\/reviews$/.test(pathName) ||
        /^\/api\/seller\/orders\/[^/]+\/ship$/.test(pathName)
      );
      if (!interesting) return;
      let envelope = null;
      try {
        envelope = await response.json();
      } catch {
        envelope = null;
      }
      const data = envelope && envelope.data ? envelope.data : null;
      const error = envelope && envelope.error ? envelope.error : null;
      const entry = {
        at: Date.now(),
        page: label,
        method,
        path: pathName,
        status,
        error_code: error && error.code ? error.code : null,
        error_message: error && error.message ? String(error.message) : null,
      };
      if (pathName === "/api/checkout/quote") {
        if (data) {
          entry.quote = {
            subtotal_cents: data.subtotal_cents ?? null,
            shipping_cents: data.shipping_cents ?? null,
            discount_cents: data.discount_cents ?? null,
            total_cents: data.total_cents ?? null,
            has_group: data.has_group ?? null,
            address_required: data.address_required ?? null,
            coupon: data.coupon && typeof data.coupon === "object" ? {
              code: data.coupon.code ?? null,
              title: data.coupon.title ?? null,
              discount_cents: data.coupon.discount_cents ?? null,
            } : null,
          };
        }
        C.quoteEvents.push(entry);
        return;
      }
      if (pathName === "/api/orders") {
        entry.order_no = readOrderNoFromData(data);
        entry.order = data && typeof data === "object" ? {
          order_no: data.order_no ?? (data.order ? data.order.order_no : null),
          status: data.status ?? (data.order ? data.order.status : null),
          total_cents: data.total_cents ?? (data.order ? data.order.total_cents : null),
        } : null;
        C.orderCreateEvents.push(entry);
        return;
      }
      if (/^\/api\/orders\/[^/]+\/pay$/.test(pathName)) {
        entry.order_code = decodeURIComponent(pathName.split("/")[3] || "");
        entry.order_status = data && data.status ? data.status : null;
        C.payEvents.push(entry);
        return;
      }
      if (pathName === "/api/addresses") {
        entry.address_id = data && data.id ? data.id : null;
        entry.receiver = data && data.receiver ? data.receiver : null;
        C.addressEvents.push(entry);
        return;
      }
      if (/^\/api\/orders\/[^/]+\/reviews$/.test(pathName)) {
        entry.review_count = data && Array.isArray(data.reviews) ? data.reviews.length : null;
        C.reviewEvents.push(entry);
        return;
      }
      if (/^\/api\/seller\/orders\/[^/]+\/ship$/.test(pathName)) {
        entry.ship_status = data && data.status ? data.status : null;
        C.shipEvents.push(entry);
      }
    })().catch(() => {});
  });
}

async function preparePage(context, label) {
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.setDefaultNavigationTimeout(28000);
  C.guards.push(makeGuard(page, { baseUrl: C.baseUrl, label }));
  installResponseJournal(page, label);
  page.on("dialog", (dialog) => {
    C.dialogs.push({ page: label, type: dialog.type(), message: oneLine(dialog.message(), 300) });
    dialog.accept().catch(() => {});
  });
  return page;
}

function pageForStage(stageId) {
  if (stageId.startsWith("E2E-07")) return C.sellerPage || C.buyerPage;
  if (stageId.startsWith("E2E-10")) return C.mobilePage || C.buyerPage;
  return C.buyerPage;
}

async function takeShot(page, name) {
  const record = await captureScreenshot(page, C.screenshotsDir, name, { fullPage: true });
  C.screenshots.push(record);
  console.log("BROWSER_SCREENSHOT " + name + " path=" + record.path + " bytes=" + record.bytes);
  return record;
}

function dedupeExternalRefs(pageLabel, refs) {
  for (const ref of refs) {
    const key = pageLabel + "|" + ref.tag + "|" + ref.url;
    if (!C.externalRefs.some((item) => item.key === key)) {
      C.externalRefs.push(Object.assign({ key, page: pageLabel }, ref));
    }
  }
}

async function assertPageReady(page, label, { contains = "" } = {}) {
  await waitForRoute(page, { label, contains, timeoutMs: 22000 });
  await assertSinglePageHeading(page, label);
  if ((await page.locator(".route-placeholder").count()) > 0) {
    const text = await page.locator(".route-placeholder").first().innerText().catch(() => "");
    throw new GuardViolation(label + ": placeholder route markup still rendered: " + oneLine(text, 300));
  }
  let overflow;
  try {
    overflow = await assertNoHorizontalOverflow(page, label);
  } catch (error) {
    C.overflowReports.push({ label, error: oneLine(error.message, 1000), horizontalOverflow: true });
    throw error;
  }
  C.overflowReports.push(overflow);
  try {
    const refs = await scanExternalResourceRefs(page, C.baseUrl);
    dedupeExternalRefs(label, refs);
  } catch {
    // Static scan is supplementary; request monitor remains authoritative.
  }
}

async function waitForBodyMatch(page, regex, timeoutMs, description) {
  return waitUntil(async () => {
    const text = await bodyText(page);
    return regex.test(text);
  }, { timeoutMs, intervalMs: 250, description });
}

function visibleErrorText(page) {
  return page.locator(".toast--error, #app .form-error, #app [role=alert], [role=dialog] .form-error, [role=dialog] [role=alert]").first().innerText().catch(() => "");
}

async function ensureApiOkEvent(event, description) {
  if (!event) throw new GuardViolation(description + ": no response event captured");
  if (event.status !== 200) {
    throw new GuardViolation(description + ": HTTP " + event.status + " code=" + (event.error_code || "?") + " message=" + (event.error_message || "?"));
  }
}

async function failureScreenshot(stageId) {
  const page = pageForStage(stageId);
  if (!page) return;
  const stamp = new Date().toISOString().replace(/[^0-9]/g, "").slice(0, 17);
  const name = "failure-" + stageId + "-" + stamp + ".png";
  try {
    const record = await captureScreenshot(page, C.screenshotsDir, name, { fullPage: true });
    C.screenshots.push(record);
    console.log("BROWSER_FAILURE_SCREENSHOT " + name + " path=" + record.path + " bytes=" + record.bytes);
  } catch (error) {
    console.log("BROWSER_FAILURE_SCREENSHOT_FAIL stage=" + stageId + " reason=" + oneLine(error.message, 300));
  }
}

async function runStage(stage) {
  if (C.journeyAbortedAt) {
    throw new GuardViolation("stage " + stage.id + " not run because journey already aborted at " + C.journeyAbortedAt);
  }
  C.currentStage = stage.id;
  const started = Date.now();
  try {
    await stage.fn();
    const result = { id: stage.id, title: stage.title, status: "PASS", duration_ms: Date.now() - started };
    C.stageResults.push(result);
    console.log("E2E " + stage.id + " verdict=PASS title=" + stage.title + " ms=" + result.duration_ms);
  } catch (error) {
    const detail = oneLine(error && (error.message || String(error)), 1800);
    const result = {
      id: stage.id,
      title: stage.title,
      status: "FAIL",
      duration_ms: Date.now() - started,
      detail,
      stack: oneLine(error && error.stack, 5000),
    };
    C.stageResults.push(result);
    C.journeyAbortedAt = stage.id;
    await failureScreenshot(stage.id);
    console.log("E2E " + stage.id + " verdict=FAIL title=" + stage.title + " detail=" + detail);
    throw error;
  }
}

function collectBlockedStages(reason) {
  const recorded = new Set(C.stageResults.map((result) => result.id));
  for (const stage of JOURNEY_STAGES) {
    if (recorded.has(stage.id)) continue;
    const record = { id: stage.id, title: stage.title, status: "BLOCKED", detail: reason };
    C.stageResults.push(record);
    console.log("E2E " + stage.id + " verdict=BLOCKED title=" + stage.title + " reason=" + oneLine(reason, 500));
  }
}

async function closeResources() {
  if (C.browser) {
    const browser = C.browser;
    C.browser = null;
    try {
      await browser.close();
    } catch {
      // best effort cleanup; the verdict does not depend on browser teardown
    }
  }
}

async function finalizeRun() {
  C.finishedAt = nowIso();
  const blockedReason = C.fatalError
    ? "bootstrap failure: " + oneLine(C.fatalError, 500)
    : (C.journeyAbortedAt ? "journey aborted at " + C.journeyAbortedAt : "stage not executed");
  collectBlockedStages(blockedReason);
  let issues = [];
  try {
    issues = await computeAuditIssues();
  } catch (error) {
    issues = ["final audit crashed: " + oneLine(error && (error.stack || error.message || error), 2000)];
  }
  C.auditIssues = issues;
  C.auditStage = {
    id: "E2E-11-final-audit",
    title: "console/network/API evidence/screenshot final audit",
    status: issues.length === 0 ? "PASS" : "FAIL",
    detail: issues.length === 0 ? "" : oneLine(issues.join(" | "), 3000),
  };
  C.stageResults.push(C.auditStage);
  if (issues.length === 0) {
    console.log("E2E E2E-11-final-audit verdict=PASS title=" + C.auditStage.title);
  } else {
    console.log("E2E E2E-11-final-audit verdict=FAIL detail=" + oneLine(issues.join(" | "), 1800));
  }
  const stats = { PASS: 0, FAIL: 0, BLOCKED: 0 };
  for (const result of C.stageResults) stats[result.status] = (stats[result.status] || 0) + 1;
  C.verdict = stats.FAIL === 0 && stats.BLOCKED === 0 && stats.PASS === JOURNEY_STAGES.length + 1 && !C.fatalError ? "PASS" : "FAIL";
  C.rc = C.verdict === "PASS" ? 0 : 1;
  const evidence = guardEvidence(C.guards);
  const summary = {
    suite: "qa-browser",
    verdict: C.verdict,
    rc: C.rc,
    started_at: C.startedAt,
    finished_at: C.finishedAt,
    worktree: "/data/dev/openshop-wt-qa-browser",
    base_url: C.baseUrl,
    out_dir: C.outDir,
    chromium: C.chromiumPath,
    playwright: C.playwrightPath,
    fixture: C.fixture,
    fixture_error: C.fixtureError,
    order_no: C.orderNo,
    buyer_order_path: C.buyerOrderPath,
    tracking_no: C.trackingNo,
    carrier: C.carrier,
    address_receiver: C.addressReceiver,
    applied_coupon_code: C.appliedCouponCode,
    applied_quote: C.appliedQuote,
    simulated_payment_seen: C.simulatedPaymentSeen,
    payment_disclosure_text: C.paymentDisclosureText,
    mobile_viewport_width: C.mobileViewportWidth,
    stats,
    stages: C.stageResults,
    audit_issues: issues,
    fatal_error: C.fatalError,
    journey_aborted_at: C.journeyAbortedAt,
    api_call_count: C.apiCalls.length,
    api_calls: C.apiCalls,
    quote_events: C.quoteEvents,
    order_create_events: C.orderCreateEvents,
    pay_events: C.payEvents,
    address_events: C.addressEvents,
    ship_events: C.shipEvents,
    review_events: C.reviewEvents,
    dialogs: C.dialogs,
    overflow_reports: C.overflowReports,
    external_resource_refs: C.externalRefs,
    screenshots: C.screenshots,
    required_screenshots: REQUIRED_SHOTS,
    console_errors: evidence.console_errors,
    page_errors: evidence.page_errors,
    external_requests: evidence.external_requests,
    request_failures: evidence.request_failures,
  };
  try {
    await writeJson(path.join(C.outDir, "console-errors.json"), {
      console_errors: evidence.console_errors,
      page_errors: evidence.page_errors,
    });
    await writeJson(path.join(C.outDir, "external-requests.json"), {
      external_requests: evidence.external_requests,
      external_resource_refs: C.externalRefs,
      request_failures: evidence.request_failures,
    });
    await writeJson(path.join(C.outDir, "horizontal-overflow.json"), {
      overflow_reports: C.overflowReports,
      mobile_viewport_width: C.mobileViewportWidth,
    });
    await writeJson(path.join(C.outDir, "api-journal.json"), {
      api_calls: C.apiCalls,
      quote_events: C.quoteEvents,
      order_create_events: C.orderCreateEvents,
      pay_events: C.payEvents,
      address_events: C.addressEvents,
      ship_events: C.shipEvents,
      review_events: C.reviewEvents,
    });
    await writeJson(C.summaryPath, summary);
  } catch (error) {
    C.verdict = "FAIL";
    C.rc = 1;
    console.log("OPENSHOP_BROWSER verdict=FAIL rc=1 reason=summary_write_failed detail=" + oneLine(error.message, 800));
    await closeResources();
    process.exitCode = 1;
    return;
  }
  await closeResources();
  console.log(
    "OPENSHOP_BROWSER verdict=" + C.verdict + " rc=" + C.rc +
    " pass=" + stats.PASS + " fail=" + stats.FAIL + " blocked=" + stats.BLOCKED +
    " console_errors=" + evidence.console_errors.length +
    " page_errors=" + evidence.page_errors.length +
    " external_requests=" + evidence.external_requests.length +
    " api_errors=" + C.apiCalls.filter((call) => call.status >= 400).length +
    " screenshots=" + C.screenshots.length +
    " summary=" + C.summaryPath +
    " out_dir=" + C.outDir
  );
  process.exitCode = C.rc;
}

async function computeAuditIssues() {
  const issues = [];
  if (C.fatalError) issues.push("bootstrap failed: " + oneLine(C.fatalError, 600));
  if (C.fixtureError) issues.push("catalog API fixture failed: " + oneLine(C.fixtureError, 500));
  if (C.guards.length === 0) issues.push("no Chromium page was instrumented");
  try {
    assertNoConsoleErrors(C.guards);
  } catch (error) {
    issues.push(oneLine(error.message, 1200));
  }
  try {
    assertNoExternalRequests(C.guards);
  } catch (error) {
    issues.push(oneLine(error.message, 1200));
  }
  if (C.externalRefs.length) {
    issues.push("external subresource reference(s) in DOM: " + C.externalRefs.map((item) => item.url).join(", ").slice(0, 1000));
  }
  const missingShots = [];
  for (const name of REQUIRED_SHOTS) {
    const record = C.screenshots.find((item) => item.name === name);
    if (!record || !fs.existsSync(record.path) || fs.statSync(record.path).size <= 0) missingShots.push(name);
  }
  if (missingShots.length) issues.push("missing screenshots: " + missingShots.join(", "));
  if (!C.mobileViewportWidth) issues.push("no 390px mobile viewport measurement recorded");
  if (C.mobileViewportWidth && Math.abs(Number(C.mobileViewportWidth) - 390) > 1) {
    issues.push("mobile viewport was " + C.mobileViewportWidth + "px, expected 390px");
  }
  const successful = (predicate) => C.apiCalls.filter((call) => call.status === 200 && predicate(call));
  const demoLogins = successful((call) => call.method === "POST" && call.path === "/api/auth/demo");
  if (demoLogins.length < 2) issues.push("expected one-click buyer and seller demo logins via real HTTP, got " + demoLogins.length + " successful demo POSTs");
  if (!successful((call) => call.method === "POST" && call.path === "/api/cart/items").length) {
    issues.push("no successful real POST /api/cart/items recorded");
  }
  if (!successful((call) => call.method === "POST" && call.path === "/api/addresses").length) {
    issues.push("no successful real POST /api/addresses recorded");
  }
  const successfulQuoteEvents = C.quoteEvents.filter((event) => event.status === 200 && event.quote);
  if (!successfulQuoteEvents.length) issues.push("no successful server quote response captured");
  const couponQuotes = successfulQuoteEvents.filter((event) => event.quote && event.quote.coupon);
  if (!couponQuotes.length) issues.push("no server quote with an applied coupon captured");
  const orderPosts = C.apiCalls.filter((call) => call.method === "POST" && call.path === "/api/orders");
  if (orderPosts.length !== 1) issues.push("expected exactly one POST /api/orders from the UI, got " + orderPosts.length);
  if (orderPosts.some((call) => call.status !== 200)) issues.push("POST /api/orders did not return 200");
  const payPosts = C.apiCalls.filter((call) => call.method === "POST" && /^\/api\/orders\/[^/]+\/pay$/.test(call.path));
  if (payPosts.length !== 1) issues.push("expected exactly one simulated payment POST, got " + payPosts.length);
  if (payPosts.some((call) => call.status !== 200)) issues.push("payment POST did not return 200");
  const shipPosts = C.apiCalls.filter((call) => call.method === "POST" && /^\/api\/seller\/orders\/[^/]+\/ship$/.test(call.path));
  if (shipPosts.length !== 1) issues.push("expected exactly one seller ship POST, got " + shipPosts.length);
  if (shipPosts.some((call) => call.status !== 200)) issues.push("seller ship POST did not return 200");
  const reviewPosts = C.apiCalls.filter((call) => call.method === "POST" && /^\/api\/orders\/[^/]+\/reviews$/.test(call.path));
  if (reviewPosts.length !== 1) issues.push("expected exactly one review POST, got " + reviewPosts.length);
  if (reviewPosts.some((call) => call.status !== 200)) issues.push("review POST did not return 200");
  const apiErrors = C.apiCalls.filter((call) => call.status >= 400);
  if (apiErrors.length) {
    issues.push("API responses >=400 observed by browser: " + apiErrors.slice(0, 12).map((call) => call.method + " " + call.path + " -> " + call.status).join(", "));
  }
  if (!C.simulatedPaymentSeen) issues.push("no visible simulated-payment evidence was observed");
  if (!C.orderNo) issues.push("no order number captured from the real create-order response");
  if (!C.trackingNo) issues.push("no seller tracking number recorded");
  if (!C.buyerOrderPath || !C.buyerOrderPath.startsWith("/orders/")) issues.push("buyer never reached a real order-detail route");
  if (!C.addressEvents.some((event) => event.status === 200 && event.receiver === C.addressReceiver)) {
    issues.push("new checkout address did not come from a successful POST /api/addresses with receiver " + C.addressReceiver);
  }
  if (!C.orderCreateEvents.some((event) => event.status === 200 && event.order_no === C.orderNo)) {
    issues.push("order create response did not match captured order number");
  }
  if (!C.payEvents.some((event) => event.status === 200)) issues.push("no successful payment response event captured");
  if (!C.shipEvents.some((event) => event.status === 200)) issues.push("no successful seller ship response event captured");
  if (!C.reviewEvents.some((event) => event.status === 200)) issues.push("no successful review response event captured");
  return issues;
}

async function setupAndRun() {
  const startedAt = nowIso();
  let args;
  try {
    args = parseArgs(process.argv);
  } catch (error) {
    console.log("OPENSHOP_BROWSER verdict=FAIL rc=1 reason=arguments detail=" + oneLine(error.message, 800));
    process.exitCode = 1;
    return;
  }
  C.startedAt = startedAt;
  C.args = args;
  C.playwrightPath = args.playwright || DEFAULT_PLAYWRIGHT;
  C.chromiumPath = args.chromium || DEFAULT_CHROMIUM;
  try {
    C.baseUrl = assertLocalBaseUrl(args["base-url"]);
    const prepared = prepareOutputDir(args["out-dir"]);
    C.outDir = args["out-dir"];
    C.screenshotsDir = prepared.screenshotsDir;
    C.summaryPath = prepared.summaryPath;
  } catch (error) {
    console.log("OPENSHOP_BROWSER verdict=FAIL rc=1 reason=output_or_base_url detail=" + oneLine(error.message, 1000));
    process.exitCode = 1;
    return;
  }
  console.log(
    "BROWSER_RUN base_url=" + C.baseUrl +
    " out_dir=" + C.outDir +
    " chromium=" + C.chromiumPath +
    " playwright=" + C.playwrightPath
  );
  try {
    const health = await fetchJsonNode(C.baseUrl + "/api/health");
    if (health.status !== 200 || !health.json || health.json.ok !== true) {
      throw new Error("local real backend health check failed: status=" + health.status + " body=" + oneLine(health.text, 300));
    }
    if (!fs.existsSync(C.playwrightPath)) throw new Error("Playwright module missing: " + C.playwrightPath);
    if (!fs.existsSync(C.chromiumPath)) throw new Error("Chromium executable missing: " + C.chromiumPath);
    const playwright = await import(pathToFileURL(C.playwrightPath).href);
    C.browser = await playwright.chromium.launch({
      headless: true,
      executablePath: C.chromiumPath,
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    });
    try {
      C.fixture = await fetchCatalogFixture(C.baseUrl);
      console.log("BROWSER_FIXTURE slug=" + C.fixture.slug + " title=" + oneLine(C.fixture.title, 120));
    } catch (error) {
      C.fixtureError = oneLine(error && (error.message || String(error)), 800);
      console.log("BROWSER_FIXTURE_FAIL " + C.fixtureError);
    }
    C.buyerContext = await C.browser.newContext({
      viewport: { width: 1440, height: 960 },
      locale: "zh-CN",
      timezoneId: "Asia/Shanghai",
    });
    C.buyerPage = await preparePage(C.buyerContext, "buyer-desktop");
    try {
      for (const stage of JOURNEY_STAGES) {
        await runStage(stage);
      }
    } catch (stageError) {
      console.log("BROWSER_JOURNEY_ABORTED stage=" + C.journeyAbortedAt + " detail=" + oneLine(stageError && (stageError.message || stageError), 900));
    }
  } catch (error) {
    C.fatalError = oneLine(error && (error.stack || error.message || String(error)), 5000);
    console.log("BROWSER_FATAL " + oneLine(error && (error.message || String(error)), 1200));
  }
  await finalizeRun();
}

setupAndRun().catch(async (error) => {
  console.log("OPENSHOP_BROWSER verdict=FAIL rc=1 reason=uncaught_harness_error detail=" + oneLine(error && (error.stack || error.message || error), 2000));
  await closeResources();
  process.exitCode = 1;
});

// Stage implementations follow. They are regular function declarations so that
// the journey table above can be declared before the detailed code.

async function stageHomeBrowse() {
  const page = C.buyerPage;
  if (!page) throw new GuardViolation("buyer page was not created");
  await page.goto(C.baseUrl + "/", { waitUntil: "domcontentloaded" });
  try {
    await assertPageReady(page, "home-desktop");
  } catch (error) {
    let message = oneLine(error && (error.message || error), 900);
    if (C.fixtureError) message += "; catalog_fixture_error=" + C.fixtureError;
    throw new GuardViolation(message);
  }
  const notes = [];
  if (C.fixtureError) notes.push("catalog API fixture failed: " + C.fixtureError);
  const cards = page.locator('#app a[href^="/p/"]');
  const firstCard = await firstVisible([cards], {
    timeoutMs: 7000,
    description: "home product card link",
    page,
    required: false,
  });
  if (!firstCard) notes.push("home has no visible a[href^=\"/p/\"] product cards");
  const search = await firstVisible([page.locator("#global-search")], {
    timeoutMs: 3000,
    description: "header search input",
    page,
    required: false,
  });
  if (!search) notes.push("header search input #global-search is not visible");
  if (notes.length) throw new GuardViolation(notes.join("; "));
  await takeShot(page, "01-home-desktop.png");
}

async function stageSearchAndOpenProduct() {
  const page = C.buyerPage;
  if (!page) throw new GuardViolation("buyer page was not created");
  if (!C.fixture) {
    throw new GuardViolation("catalog fixture unavailable; cannot perform data-driven search. fixture_error=" + (C.fixtureError || "none"));
  }
  const search = page.locator("#global-search");
  await search.waitFor({ state: "visible", timeout: 8000 });
  await search.fill(C.fixture.query);
  await search.press("Enter");
  await page.waitForURL(function (url) {
    return url.pathname === "/search" || url.pathname === "/search/";
  }, { timeout: 15000 });
  await assertPageReady(page, "search-desktop", { contains: "/search" });
  const parsed = new URL(page.url());
  const actualQuery = parsed.searchParams.get("q") || "";
  if (actualQuery !== C.fixture.query) {
    throw new GuardViolation(
      "search URL did not preserve query: expected " + JSON.stringify(C.fixture.query) +
      " actual " + JSON.stringify(actualQuery) + " url=" + page.url()
    );
  }
  const targetLink = page.locator('a[href="/p/' + C.fixture.slug + '"]');
  const found = await firstVisible([targetLink], {
    timeoutMs: 10000,
    description: "search result card for " + C.fixture.slug,
    page,
    required: false,
  });
  if (!found) {
    const hrefs = await page.locator('#app a[href^="/p/"]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
    const snippet = oneLine(await bodyText(page), 500);
    throw new GuardViolation(
      "search for exact title " + JSON.stringify(C.fixture.query) +
      " did not render target product " + C.fixture.slug +
      "; visible product links=" + JSON.stringify(hrefs.slice(0, 12)) +
      "; body=" + snippet
    );
  }
  await takeShot(page, "02-search-desktop.png");
  await found.click({ timeout: 8000 });
  await page.waitForURL(function (url) {
    return url.pathname.startsWith("/p/");
  }, { timeout: 15000 });
  await assertPageReady(page, "product-from-search", { contains: "/p/" });
}

async function stageAddToCart() {
  const page = C.buyerPage;
  if (!page) throw new GuardViolation("buyer page was not created");
  if (!C.fixture) throw new GuardViolation("catalog fixture unavailable");
  if (!new URL(page.url()).pathname.startsWith("/p/")) {
    throw new GuardViolation("expected product detail route before add-to-cart, got " + page.url());
  }
  await waitUntil(async () => {
    const text = await bodyText(page);
    return text.includes(C.fixture.title);
  }, { timeoutMs: 12000, intervalMs: 250, description: "product title in detail DOM" });
  await clickAddToCart(page);
  await maybeDismissTransientOverlay(page).catch(() => {});
  await takeShot(page, "03-product-desktop.png");
}

async function stageBuyerLoginAndCart() {
  const page = C.buyerPage;
  if (!page) throw new GuardViolation("buyer page was not created");
  if (!C.fixture) throw new GuardViolation("catalog fixture unavailable");
  await maybeDismissTransientOverlay(page).catch(() => {});
  const login = await openDemoLogin(page, "buyer");
  if (!login.accountLabel || !(login.accountLabel.includes("林澄") || login.accountLabel.includes("你好"))) {
    throw new GuardViolation("buyer demo login account label missing expected identity: " + JSON.stringify(login.accountLabel));
  }
  const cartLink = page.locator("#cart-action");
  await cartLink.waitFor({ state: "visible", timeout: 8000 });
  await cartLink.click();
  await page.waitForURL(function (url) {
    return url.pathname === "/cart" || url.pathname === "/cart/";
  }, { timeout: 15000 });
  await assertPageReady(page, "cart-desktop", { contains: "/cart" });
  try {
    await waitForBodyMatch(page, new RegExp(escapeRegex(C.fixture.title)), 12000, "cart DOM containing " + C.fixture.title);
  } catch (error) {
    throw new GuardViolation("guest cart item disappeared after one-click buyer login; expected title " + JSON.stringify(C.fixture.title) + " in cart DOM");
  }
  const checkoutCandidates = [
    page.locator('[data-testid="checkout-link"]'),
    page.locator('a[href="/checkout"]'),
    page.getByRole("link", { name: /去结算|结算|结账/ }),
    page.getByRole("button", { name: /去结算|结算|结账/ }),
  ];
  let checkout = await firstVisible(checkoutCandidates, {
    timeoutMs: 8000,
    description: "cart checkout control",
    page,
    required: false,
  });
  if (!checkout) {
    throw new GuardViolation("cart has no visible checkout control; body=" + oneLine(await bodyText(page), 700));
  }
  if (!(await checkout.isEnabled().catch(() => true))) {
    const selectAll = await firstVisible([
      page.getByRole("button", { name: /全选/ }),
      page.getByRole("checkbox", { name: /全选/ }),
      page.getByText("全选", { exact: true }),
    ], { timeoutMs: 1500, description: "select-all control", page, required: false });
    if (selectAll) {
      await selectAll.click({ timeout: 4000 }).catch(() => {});
    } else {
      const boxes = page.locator("#app input[type=checkbox]");
      const count = await boxes.count().catch(() => 0);
      for (let i = 0; i < count; i += 1) {
        const box = boxes.nth(i);
        if (await box.isVisible().catch(() => false) && !(await box.isChecked().catch(() => true))) {
          await box.click({ timeout: 4000 }).catch(() => {});
        }
      }
    }
    await sleep(400);
    checkout = await firstVisible(checkoutCandidates, {
      timeoutMs: 4000,
      description: "enabled checkout control after item selection",
      page,
      required: false,
    });
  }
  if (!checkout) throw new GuardViolation("cart checkout control never became available");
  if (!(await checkout.isEnabled().catch(() => true))) {
    throw new GuardViolation("cart checkout control is disabled after attempting to select items");
  }
  await takeShot(page, "04-cart-desktop.png");
  await checkout.click();
  await page.waitForURL(function (url) {
    return url.pathname === "/checkout" || url.pathname === "/checkout/";
  }, { timeout: 15000 });
  await assertPageReady(page, "checkout-desktop", { contains: "/checkout" });
}

async function stageCheckoutAddressCoupon() {
  const page = C.buyerPage;
  if (!page) throw new GuardViolation("buyer page was not created");
  if (!C.fixture) throw new GuardViolation("catalog fixture unavailable");
  const addAddressCandidates = [
    page.locator('[data-testid="address-add"]'),
    page.locator('[data-testid="address-add-empty"]'),
    page.locator('[data-testid="add-address"]'),
    page.locator('[data-testid="new-address"]'),
    page.getByRole("button", { name: /新增地址|添加地址|新建地址|使用新地址/ }),
    page.getByRole("link", { name: /新增地址|添加地址|新建地址|使用新地址/ }),
  ];
  const receiverCandidates = [page.locator('input[name="receiver"]')];
  let receiverField = await firstVisible(receiverCandidates, {
    timeoutMs: 1200,
    description: "visible receiver field on checkout",
    page,
    required: false,
  });
  if (!receiverField) {
    const addTrigger = await firstVisible(addAddressCandidates, {
      timeoutMs: 5000,
      description: "checkout add-address control",
      page,
      required: false,
    });
    if (!addTrigger) {
      throw new GuardViolation("checkout address step has no visible address form or add-address control; body=" + oneLine(await bodyText(page), 900));
    }
    await addTrigger.click({ timeout: 8000 });
    receiverField = await firstVisible(receiverCandidates, {
      timeoutMs: 6000,
      description: "address form receiver field after opening add-address",
      page,
      required: false,
    });
  }
  if (!receiverField) {
    throw new GuardViolation("add-address interaction did not reveal a visible receiver field");
  }
  const scope = await formScopeFor(page, "receiver");
  await fillPreferredField(page, {
    names: ["receiver"],
    labels: ["收货人|收件人|联系人|姓名"],
    placeholders: ["收货人", "收件人", "姓名"],
  }, C.addressReceiver, { scope, description: "address receiver", timeoutMs: 4000 });
  await fillPreferredField(page, {
    names: ["phone"],
    labels: ["手机|电话"],
    placeholders: ["手机号", "手机", "电话"],
  }, C.addressPhone, { scope, description: "address phone", timeoutMs: 4000 });
  await fillPreferredField(page, {
    names: ["province"],
    labels: ["省份|省"],
    placeholders: ["省"],
  }, "上海市", { scope, description: "address province", timeoutMs: 3000 });
  await fillPreferredField(page, {
    names: ["city"],
    labels: ["城市|市"],
    placeholders: ["市"],
  }, "上海市", { scope, description: "address city", timeoutMs: 3000 });
  await fillPreferredField(page, {
    names: ["district"],
    labels: ["区县|区"],
    placeholders: ["区"],
  }, "徐汇区", { scope, description: "address district", timeoutMs: 3000 });
  await fillPreferredField(page, {
    names: ["detail"],
    labels: ["详细地址|街道地址|门牌号|街道"],
    placeholders: ["详细地址", "街道", "门牌"],
  }, "验收路 88 号 6 栋 501 室", { scope, description: "address detail", timeoutMs: 4000 });
  await fillPreferredField(page, {
    names: ["label"],
    labels: ["地址标签|标签"],
    placeholders: ["标签"],
  }, "验收", { scope, description: "optional address label", timeoutMs: 1200, required: false }).catch(() => null);
  const defaultBox = scope.getByRole("checkbox", { name: /默认/ }).first();
  if ((await defaultBox.count().catch(() => 0)) > 0 && (await defaultBox.isVisible().catch(() => false))) {
    if (!(await defaultBox.isChecked().catch(() => false))) {
      await defaultBox.check({ timeout: 3000 }).catch(() => {});
    }
  }
  const saveCandidates = [
    scope.locator('[data-testid="address-save"]'),
    scope.locator('[data-testid="save-address"]'),
    scope.getByRole("button", { name: /保存地址|保存并使用|保存|添加地址|确认添加|使用此地址/ }),
    scope.locator('button[type="submit"]'),
    page.locator('[role="dialog"] [data-testid="address-save"]'),
  ];
  const saveButton = await firstVisible(saveCandidates, {
    timeoutMs: 5000,
    description: "address save button",
    page,
    required: false,
  });
  if (!saveButton) {
    throw new GuardViolation("address form has no visible save button; scope text=" + oneLine(await bodyText(page), 800));
  }
  const addressEventStart = C.addressEvents.length;
  await saveButton.click({ timeout: 8000 });
  const addressEvent = await waitForApiEvent(C.addressEvents, addressEventStart, {
    page,
    timeoutMs: 15000,
    description: "real POST /api/addresses after save",
    maybeClickConfirm: async () => maybeClickDialogButton(page, /确认|确定|保存/),
  });
  await ensureApiOkEvent(addressEvent, "checkout save address");
  const addresses = await getApiData(page, "/api/addresses");
  const savedAddress = (addresses.items || []).find((item) => item && item.receiver === C.addressReceiver && item.phone === C.addressPhone);
  if (!savedAddress) {
    throw new GuardViolation("saved address not found in GET /api/addresses for " + C.addressReceiver + "/" + C.addressPhone);
  }
  await waitUntil(async () => {
    const text = await bodyText(page);
    return text.includes(C.addressReceiver);
  }, { timeoutMs: 6000, intervalMs: 250, description: "new address receiver in checkout DOM" });
  const newlyCreatedChoice = await firstVisible([
    page.locator("label").filter({ hasText: C.addressReceiver }),
    page.getByRole("radio", { name: new RegExp(escapeRegex(C.addressReceiver)) }),
    page.locator('[role="radio"]').filter({ hasText: C.addressReceiver }),
  ], { timeoutMs: 1800, description: "selectable new address card", page, required: false });
  if (newlyCreatedChoice) {
    await newlyCreatedChoice.click({ timeout: 3000 }).catch(() => {});
    await sleep(300);
  }
  let initialQuote = null;
  await waitUntil(async () => {
    initialQuote = latestSuccessfulQuote();
    return Boolean(initialQuote);
  }, { timeoutMs: 15000, intervalMs: 250, description: "successful POST /api/checkout/quote from checkout" }).catch((error) => {
    throw new GuardViolation("checkout never obtained a real server quote: " + oneLine(error.message, 500));
  });
  const couponData = await getApiData(page, "/api/coupons");
  const availableCoupons = normalizeAvailableCoupons(couponData);
  if (availableCoupons.length === 0) {
    throw new GuardViolation("GET /api/coupons returned no AVAILABLE user coupons after demo buyer login; body=" + oneLine(await bodyText(page), 500));
  }
  let applied = latestSuccessfulQuote();
  if (!applied || !applied.quote || !applied.quote.coupon) {
    const subtotal = applied && applied.quote ? Number(applied.quote.subtotal_cents || 0) : 0;
    const usable = availableCoupons.filter((item) => item.threshold_cents <= subtotal);
    // The seed cart item may belong to a category that excludes a scoped
    // coupon. Use an unrestricted coupon for the main journey; scoped-coupon
    // rejection remains covered by the API suite and the quote error state.
    const journeyCoupons = usable.filter((item) => item.scope === "all");
    if (journeyCoupons.length === 0) {
      throw new GuardViolation("no AVAILABLE unrestricted coupon meets quote subtotal " + subtotal + "; coupons=" + JSON.stringify(availableCoupons));
    }
    const chosenCoupon = journeyCoupons[0];
    let appliedVia = null;
    const couponSelectCandidates = [
      page.locator('[data-testid="coupon-select"]'),
      page.locator('select[name="coupon_code"]'),
      page.locator('select[name="coupon"]'),
      page.locator('#coupon-code'),
      page.locator('[data-testid="coupon-select"] select'),
    ];
    const couponSelect = await firstVisible(couponSelectCandidates, {
      timeoutMs: 1500,
      description: "coupon select control",
      page,
      required: false,
    });
    if (couponSelect) {
      const options = await couponSelect.locator("option").evaluateAll((nodes) => nodes.map((node) => ({ value: node.value, text: (node.textContent || "").trim() })));
      const match = options.find((item) => item.value === chosenCoupon.code || item.text.includes(chosenCoupon.code) || item.text.includes(chosenCoupon.title));
      if (match) {
        await couponSelect.selectOption(match.value);
        appliedVia = "select";
      }
    }
    if (!appliedVia) {
      const couponInput = await firstVisible([
        page.locator('input[name="coupon_code"]'),
        page.locator('input[name="coupon"]'),
        page.locator('input[placeholder*="优惠码"]'),
        page.locator('input[placeholder*="优惠券"]'),
        page.locator('[data-testid="coupon-input"]'),
      ], { timeoutMs: 1800, description: "coupon code input", page, required: false });
      if (couponInput) {
        await couponInput.fill(chosenCoupon.code);
        const applyButton = await firstVisible([
          page.getByRole("button", { name: /使用|应用|兑换|确定/ }),
          page.locator('[data-testid="apply-coupon"]'),
        ], { timeoutMs: 2500, description: "coupon apply button", page, required: false });
        if (applyButton) {
          await applyButton.click({ timeout: 4000 });
          appliedVia = "input";
        }
      }
    }
    const triggerCandidates = [
      page.locator('[data-testid="coupon-select"]'),
      page.locator('[data-testid="coupon-picker"]'),
      page.getByRole("button", { name: /优惠券|选择优惠券|优惠码/ }),
      page.getByRole("link", { name: /优惠券|选择优惠券/ }),
    ];
    const findCouponMarker = async () => {
      return page.evaluate(function (codes) {
        const nodes = Array.from(document.querySelectorAll("body *")).filter(function (el) {
          if (el.children.length > 0) return false;
          const text = el.textContent || "";
          return codes.some(function (code) { return text.includes(code); });
        });
        for (const node of nodes) {
          let current = node;
          for (let depth = 0; current && current !== document.body && depth < 8; depth += 1, current = current.parentElement) {
            const tag = current.tagName ? current.tagName.toLowerCase() : "";
            if (tag === "button" || tag === "label" || current.getAttribute("role") === "radio" || current.getAttribute("role") === "option" || current.hasAttribute("data-coupon-code")) {
              current.setAttribute("data-qa-coupon-target", "1");
              return true;
            }
          }
        }
        return false;
      }, [chosenCoupon.code]);
    };
    let marked = await findCouponMarker().catch(() => false);
    if (!marked) {
      for (const locator of triggerCandidates) {
        const trigger = await firstVisible([locator], {
          timeoutMs: 700,
          description: "coupon picker trigger",
          page,
          required: false,
        });
        if (!trigger) continue;
        await trigger.click({ timeout: 4000 }).catch(() => {});
        await sleep(500);
        marked = await findCouponMarker().catch(() => false);
        if (marked) break;
      }
    }
    if (marked) {
      const target = page.locator('[data-qa-coupon-target="1"]');
      if (await target.isVisible().catch(() => false)) {
        await target.click({ timeout: 5000 });
        await sleep(400);
        await maybeClickDialogButton(page, /确认使用|使用优惠券|使用|确定/).catch(() => null);
        appliedVia = appliedVia || "picker";
      }
    }
    if (!marked && !appliedVia) {
      throw new GuardViolation(
        "coupon UI could not be driven; available coupons=" + JSON.stringify(availableCoupons.map((item) => item.code)) +
        "; body=" + oneLine(await bodyText(page), 700)
      );
    }
    await waitUntil(() => {
      const current = latestSuccessfulQuote();
      return Boolean(current && current.quote && current.quote.coupon);
    }, { timeoutMs: 15000, intervalMs: 300, description: "server quote with applied coupon after coupon UI interaction" }).catch(async (error) => {
      throw new GuardViolation(
        "coupon selection did not result in a real server quote with coupon; via=" + String(appliedVia) +
        "; available=" + JSON.stringify(availableCoupons.map((item) => item.code)) +
        "; body=" + oneLine(await bodyText(page), 700)
      );
    });
    applied = latestSuccessfulQuote();
  }
  if (!applied || !applied.quote || !applied.quote.coupon) {
    throw new GuardViolation("checkout quote still has no applied coupon after UI interaction");
  }
  C.appliedQuote = applied.quote;
  C.appliedCouponCode = applied.quote.coupon.code || null;
  const pageText = await bodyText(page);
  if (!/优惠/.test(pageText)) {
    throw new GuardViolation("checkout DOM does not visibly mention coupon/discount after server applied coupon; body=" + oneLine(pageText, 700));
  }
  await takeShot(page, "05-checkout-desktop.png");
}

async function stageCreateOrderAndPay() {
  const page = C.buyerPage;
  if (!page) throw new GuardViolation("buyer page was not created");
  if (!C.appliedQuote) throw new GuardViolation("no coupon-applied server quote available before order creation");
  const createCandidates = [
    page.locator('[data-testid="create-order"]'),
    page.getByRole("button", { name: /提交订单|创建订单|确认下单|提交并支付/ }),
    page.getByRole("link", { name: /提交订单|确认下单/ }),
  ];
  const createButton = await firstVisible(createCandidates, {
    timeoutMs: 10000,
    description: "create-order control",
    page,
    required: true,
  });
  if (!(await createButton.isEnabled().catch(() => true))) {
    throw new GuardViolation("create-order control is disabled; body=" + oneLine(await bodyText(page), 800));
  }
  const orderEventStart = C.orderCreateEvents.length;
  await createButton.click({ timeout: 8000 });
  const orderEvent = await waitForApiEvent(C.orderCreateEvents, orderEventStart, {
    page,
    timeoutMs: 22000,
    description: "real POST /api/orders after checkout submit",
    maybeClickConfirm: async () => maybeClickDialogButton(page, /确认下单|确认提交|提交订单|确认|确定/),
  });
  await ensureApiOkEvent(orderEvent, "create order");
  const orderNo = orderEvent.order_no || (orderEvent.order && orderEvent.order.order_no);
  if (!orderNo) throw new GuardViolation("create-order response has no order_no: " + JSON.stringify(orderEvent));
  C.orderNo = orderNo;
  await sleep(350);
  const createdOrder = await getApiData(page, "/api/orders/" + encodeURIComponent(orderNo));
  if (createdOrder.total_cents !== C.appliedQuote.total_cents) {
    throw new GuardViolation(
      "created order total " + createdOrder.total_cents + " does not equal server quote total " + C.appliedQuote.total_cents
    );
  }
  if (!createdOrder.address || createdOrder.address.receiver !== C.addressReceiver) {
    throw new GuardViolation(
      "created order did not use the address created through the UI: expected receiver " +
      C.addressReceiver + " actual " + JSON.stringify(createdOrder.address && createdOrder.address.receiver)
    );
  }
  const payButtonCandidates = [
    page.locator('[data-testid="pay-demo"]'),
    page.getByRole("button", { name: /模拟支付|立即支付|去支付|支付订单|确认支付/ }),
    page.getByRole("link", { name: /去支付|立即支付|模拟支付/ }),
  ];
  const payButton = await firstVisible(payButtonCandidates, {
    timeoutMs: 15000,
    description: "simulated payment control",
    page,
    required: true,
  });
  const payLabel = (await payButton.innerText().catch(() => "")).trim();
  const paymentDialog = await visibleDialog(page).catch(() => null);
  const appPaymentText = await page.locator("#app").innerText({ timeout: 3000 }).catch(() => "");
  const dialogPaymentText = paymentDialog ? await paymentDialog.innerText().catch(() => "") : "";
  let nearbyPaymentText = "";
  try {
    nearbyPaymentText = await payButton.evaluate(function (element) {
      let node = element;
      let collected = "";
      for (let depth = 0; node && depth < 3; depth += 1, node = node.parentElement) {
        collected = (node.innerText || "") + " " + collected;
      }
      return collected;
    });
  } catch {
    nearbyPaymentText = "";
  }
  const paymentText = appPaymentText + " " + dialogPaymentText + " " + nearbyPaymentText + " " + payLabel;
  C.paymentDisclosureText = oneLine(paymentText, 700);
  if (!/模拟|演示|DEMO/.test(paymentText)) {
    throw new GuardViolation("payment UI does not visibly disclose simulated/demo payment; scoped_payment_text=" + oneLine(paymentText, 700));
  }
  C.simulatedPaymentSeen = true;
  if (!(await payButton.isEnabled().catch(() => true))) {
    throw new GuardViolation("simulated payment control is disabled");
  }
  const payEventStart = C.payEvents.length;
  await payButton.click({ timeout: 8000 });
  const payEvent = await waitForApiEvent(C.payEvents, payEventStart, {
    page,
    timeoutMs: 22000,
    description: "real simulated pay POST",
    maybeClickConfirm: async () => maybeClickDialogButton(page, /确认支付|确认付款|支付|确定/),
  });
  await ensureApiOkEvent(payEvent, "simulated payment");
  let paidOrder = null;
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    paidOrder = await getApiData(page, "/api/orders/" + encodeURIComponent(orderNo));
    if (paidOrder.status === "PAID") break;
    await sleep(300);
  }
  if (!paidOrder || paidOrder.status !== "PAID") {
    throw new GuardViolation("order did not become PAID after simulated payment; status=" + JSON.stringify(paidOrder && paidOrder.status));
  }
  await waitForBodyMatch(page, /待发货|已支付|支付成功/, 15000, "payment success or paid status in DOM").catch(() => null);
  let pathName = new URL(page.url()).pathname;
  if (!pathName.startsWith("/orders/")) {
    const orderLink = await firstVisible([
      page.getByRole("link", { name: /查看订单|订单详情|查看详情/ }),
      page.getByRole("button", { name: /查看订单|订单详情|查看详情/ }),
      page.locator('a[href^="/orders/"]'),
    ], { timeoutMs: 8000, description: "order detail navigation after payment", page, required: false });
    if (!orderLink) {
      throw new GuardViolation("after payment there is no order-detail route or visible order link; url=" + page.url() + " body=" + oneLine(await bodyText(page), 700));
    }
    await orderLink.click({ timeout: 5000 });
    await page.waitForURL(function (url) {
      return url.pathname.startsWith("/orders/");
    }, { timeout: 15000 });
    pathName = new URL(page.url()).pathname;
  }
  await assertPageReady(page, "order-paid-desktop", { contains: "/orders/" });
  const finalOrder = await getApiData(page, "/api/orders/" + encodeURIComponent(orderNo));
  if (finalOrder.status !== "PAID") {
    throw new GuardViolation("order detail API is not PAID after payment; got " + JSON.stringify(finalOrder.status));
  }
  const orderPageText = await bodyText(page);
  if (!orderPageText.includes(finalOrder.status_label || "待发货") && !/待发货|已支付/.test(orderPageText)) {
    throw new GuardViolation("order DOM does not show PAID/status label; body=" + oneLine(orderPageText, 700));
  }
  if (!new URL(page.url()).pathname.includes(encodeURIComponent(orderNo)) && !orderPageText.includes(orderNo)) {
    throw new GuardViolation("order detail DOM/URL does not identify order_no " + orderNo);
  }
  C.buyerOrderPath = new URL(page.url()).pathname;
  await takeShot(page, "06-order-paid-desktop.png");
}

async function stageSellerShip() {
  if (!C.orderNo) throw new GuardViolation("no buyer order_no captured; cannot target seller package");
  if (!C.sellerContext) {
    C.sellerContext = await C.browser.newContext({
      viewport: { width: 1440, height: 960 },
      locale: "zh-CN",
      timezoneId: "Asia/Shanghai",
    });
    C.sellerPage = await preparePage(C.sellerContext, "seller-desktop");
  }
  const page = C.sellerPage;
  await page.goto(C.baseUrl + "/", { waitUntil: "domcontentloaded" });
  await assertPageReady(page, "seller-login-home");
  const login = await openDemoLogin(page, "seller");
  if (!login.accountLabel || !(login.accountLabel.includes("商家") || login.accountLabel.includes("周屿"))) {
    throw new GuardViolation("seller demo login chrome did not show seller identity: " + JSON.stringify(login.accountLabel));
  }
  await page.goto(C.baseUrl + "/seller", { waitUntil: "domcontentloaded" });
  await assertPageReady(page, "seller-console", { contains: "/seller" });
  let sellerText = await bodyText(page);
  if (!sellerText.includes(C.orderNo)) {
    const tabCandidates = [
      page.getByRole("tab", { name: /订单|待发货|发货/ }),
      page.getByRole("button", { name: /^订单$|订单管理|待发货|全部订单/ }),
      page.getByRole("link", { name: /^订单$|订单管理|待发货|全部订单/ }),
    ];
    for (const locator of tabCandidates) {
      const tab = await firstVisible([locator], {
        timeoutMs: 700,
        description: "seller orders tab",
        page,
        required: false,
      });
      if (!tab) continue;
      await tab.click({ timeout: 4000 }).catch(() => {});
      await sleep(600);
      sellerText = await bodyText(page);
      if (sellerText.includes(C.orderNo)) break;
    }
  }
  if (!sellerText.includes(C.orderNo)) {
    await waitUntil(async () => {
      sellerText = await bodyText(page);
      return sellerText.includes(C.orderNo);
    }, { timeoutMs: 12000, intervalMs: 300, description: "seller order " + C.orderNo + " in DOM" }).catch((error) => {
      throw new GuardViolation("seller console does not show buyer order " + C.orderNo + "; url=" + page.url() + "; body=" + oneLine(sellerText, 800));
    });
  }
  await takeShot(page, "07-seller-orders-desktop.png");
  const marked = await page.evaluate(function (orderNo) {
    function isVisible(el) {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    }
    const leaves = Array.from(document.querySelectorAll("body *")).filter(function (el) {
      return el.children.length === 0 && (el.textContent || "").includes(orderNo);
    });
    for (const leaf of leaves) {
      let node = leaf;
      for (let depth = 0; node && node !== document.body && depth < 10; depth += 1, node = node.parentElement) {
        const buttons = Array.from(node.querySelectorAll("button, a")).filter(function (button) {
          return /发货/.test((button.textContent || "").trim()) && isVisible(button);
        });
        if (buttons.length > 0) {
          buttons[0].setAttribute("data-qa-ship-target", "1");
          return true;
        }
      }
    }
    return false;
  }, C.orderNo).catch(() => false);
  if (!marked) {
    throw new GuardViolation(
      "could not find a ship button in the ancestor row of seller order " + C.orderNo +
      "; body=" + oneLine(await bodyText(page), 800)
    );
  }
  const shipButton = page.locator('[data-qa-ship-target="1"]').first();
  await firstVisible([shipButton], {
    timeoutMs: 5000,
    description: "marked seller ship button",
    page,
  });
  await shipButton.click({ timeout: 8000 });
  await sleep(500);
  const dialog = await visibleDialog(page).catch(() => null);
  const formRoot = dialog || page;
  const trackingSpec = {
    names: ["tracking_no", "trackingNo", "tracking"],
    labels: ["运单号|物流单号|快递单号|物流编号"],
    placeholders: ["运单号", "物流单号", "快递单号"],
  };
  const trackingField = await waitUntil(async () => {
    return firstVisible([
      formRoot.locator('[data-testid="ship-tracking"]'),
      formRoot.locator('input[name="tracking_no"]'),
      formRoot.locator('input[name="trackingNo"]'),
      formRoot.getByLabel(/运单号|物流单号|快递单号/),
      formRoot.locator('input[placeholder*="运单号"]'),
    ], { timeoutMs: 200, description: "tracking number field", page, required: false });
  }, { timeoutMs: 10000, intervalMs: 250, description: "seller tracking-number form" }).catch((error) => {
    throw new GuardViolation("seller ship form did not expose a tracking-number field: " + oneLine(error.message, 500));
  });
  const carrierFilled = await fillPreferredField(page, {
    names: ["carrier"],
    labels: ["物流公司|承运商|快递公司|快递"],
    placeholders: ["物流公司", "承运商", "快递"],
  }, C.carrier, { scope: formRoot, description: "carrier selector", timeoutMs: 2500, required: false }).catch(() => null);
  if (!carrierFilled) {
    const carrierButtons = await firstVisible([
      formRoot.getByRole("button", { name: /青鸟速运/ }),
      formRoot.getByRole("radio", { name: /青鸟速运/ }),
      formRoot.locator('[data-carrier="qingniao"]'),
      formRoot.getByText("青鸟速运", { exact: true }),
    ], { timeoutMs: 2000, description: "carrier choice", page, required: false });
    if (carrierButtons) {
      await carrierButtons.click({ timeout: 3000 }).catch(() => {});
    } else {
      const visibleCarrier = (await bodyText(page)).includes(C.carrier);
      if (!visibleCarrier) {
        throw new GuardViolation("seller shipping form exposes neither carrier field nor carrier choice; body=" + oneLine(await bodyText(page), 700));
      }
    }
  }
  const tracking = "QA" + String(Date.now()).slice(-10) + "XY";
  C.trackingNo = tracking;
  await trackingField.fill(tracking, { timeout: 5000 });
  const submitCandidates = [
    formRoot.locator('[data-testid="ship-submit"]'),
    formRoot.getByRole("button", { name: /确认发货|提交发货|确定发货|保存发货/ }),
    formRoot.getByRole("button", { name: /^发货$/ }),
  ];
  const submit = await firstVisible(submitCandidates, {
    timeoutMs: 5000,
    description: "ship submit button",
    page,
  });
  const shipEventStart = C.shipEvents.length;
  await submit.click({ timeout: 8000 });
  const shipEvent = await waitForApiEvent(C.shipEvents, shipEventStart, {
    page,
    timeoutMs: 22000,
    description: "real seller ship POST",
    maybeClickConfirm: async () => maybeClickDialogButton(page, /确认发货|确认|确定/),
  });
  await ensureApiOkEvent(shipEvent, "seller ship");
  await sleep(600);
  await waitForBodyMatch(page, /发货成功|已发货|运输中/, 8000, "seller shipping success state").catch(() => null);
  await takeShot(page, "08-seller-shipped-desktop.png");
}

async function stageBuyerVisibilityConfirm() {
  const page = C.buyerPage;
  if (!page) throw new GuardViolation("buyer page was not created");
  if (!C.orderNo) throw new GuardViolation("no order_no captured");
  if (!C.trackingNo) throw new GuardViolation("no tracking number was submitted by seller stage");
  if (!C.buyerOrderPath) throw new GuardViolation("no buyer order path captured");
  await page.goto(C.baseUrl + C.buyerOrderPath, { waitUntil: "domcontentloaded" });
  await assertPageReady(page, "buyer-order-after-ship", { contains: "/orders/" });
  const order = await getApiData(page, "/api/orders/" + encodeURIComponent(C.orderNo));
  if (order.status !== "SHIPPED") {
    throw new GuardViolation("buyer order API status is " + JSON.stringify(order.status) + ", expected SHIPPED after seller action");
  }
  const textAfterShip = await bodyText(page);
  if (!textAfterShip.includes(C.trackingNo)) {
    throw new GuardViolation("buyer DOM does not show tracking number " + C.trackingNo + "; body=" + oneLine(textAfterShip, 800));
  }
  if (!textAfterShip.includes(C.carrier)) {
    throw new GuardViolation("buyer DOM does not show carrier " + C.carrier + "; body=" + oneLine(textAfterShip, 800));
  }
  await takeShot(page, "09-buyer-after-ship-desktop.png");
  const confirmCandidates = [
    page.locator('[data-testid="confirm-receipt"]'),
    page.getByRole("button", { name: /确认收货/ }),
    page.getByRole("link", { name: /确认收货/ }),
  ];
  const confirm = await firstVisible(confirmCandidates, {
    timeoutMs: 8000,
    description: "confirm-receipt control",
    page,
  });
  await confirm.click({ timeout: 8000 });
  await sleep(300);
  await maybeClickDialogButton(page, /确认收货|确认|确定/).catch(() => null);
  let completed = null;
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    completed = await getApiData(page, "/api/orders/" + encodeURIComponent(C.orderNo));
    if (completed.status === "COMPLETED") break;
    await sleep(300);
  }
  if (!completed || completed.status !== "COMPLETED") {
    throw new GuardViolation("order did not become COMPLETED after buyer confirm; status=" + JSON.stringify(completed && completed.status));
  }
  await waitForBodyMatch(page, /已完成|交易完成|评价/, 15000, "completed status in buyer DOM").catch(async () => {
    throw new GuardViolation("buyer order DOM does not show completion; body=" + oneLine(await bodyText(page), 800));
  });
  await takeShot(page, "10-buyer-completed-desktop.png");
}

async function stageReview() {
  const page = C.buyerPage;
  if (!page) throw new GuardViolation("buyer page was not created");
  if (!C.orderNo) throw new GuardViolation("no order_no captured");
  const orderBefore = await getApiData(page, "/api/orders/" + encodeURIComponent(C.orderNo));
  if (!orderBefore.actions || orderBefore.actions.review !== true) {
    throw new GuardViolation("server actions.review is not true after completion; actions=" + JSON.stringify(orderBefore.actions));
  }
  const openCandidates = [
    page.getByRole("button", { name: /去评价|发表评价|^评价$/ }),
    page.getByRole("link", { name: /去评价|发表评价|^评价$/ }),
    page.locator('[data-testid="review-open"]'),
  ];
  const openButton = await firstVisible(openCandidates, {
    timeoutMs: 8000,
    description: "review-open control",
    page,
  });
  await openButton.click({ timeout: 8000 });
  await waitUntil(async () => {
    const fields = page.locator('textarea, input[name="title"], select[name="rating"]');
    const count = await fields.count().catch(() => 0);
    for (let i = 0; i < count; i += 1) {
      if (await fields.nth(i).isVisible().catch(() => false)) return true;
    }
    return false;
  }, { timeoutMs: 10000, intervalMs: 250, description: "visible review form" }).catch((error) => {
    throw new GuardViolation("review form did not appear after clicking review action: " + oneLine(error.message, 500));
  });
  const scope = await formScopeFor(page, "body");
  const ratingTitle = "验收评价" + String(Date.now()).slice(-6);
  const reviewBody = "这是一条真实浏览器验收评价，商品做工细致，包装完整，符合页面描述。" + String(Date.now()).slice(-4);
  let ratingDone = false;
  const ratingCandidates = [
    scope.locator('select[name="rating"]'),
    scope.locator('[data-testid="rating-5"]'),
    scope.locator('[data-testid="review-rating-5"]'),
    scope.locator('[data-rating="5"]'),
    scope.locator('[data-value="5"]'),
    scope.getByRole("radio", { name: /5|非常满意/ }),
    scope.getByRole("button", { name: /5.*星|非常满意|五星/ }),
    scope.locator('label').filter({ hasText: /5星|非常满意|五星/ }),
    scope.locator('input[name="rating"][value="5"]'),
  ];
  const ratingControl = await firstVisible(ratingCandidates, {
    timeoutMs: 2500,
    description: "5-star rating control",
    page,
    required: false,
  });
  if (ratingControl) {
    const controlInfo = await ratingControl.evaluate(function (element) {
      return { tag: element.tagName ? element.tagName.toLowerCase() : "", type: element.getAttribute("type") || "" };
    }).catch(() => ({ tag: "", type: "" }));
    if (controlInfo.tag === "select") {
      const options = await ratingControl.locator("option").evaluateAll((nodes) => nodes.map((node) => ({ value: node.value, text: (node.textContent || "").trim() })));
      const five = options.find((item) => item.value === "5" || item.text === "5" || item.text.includes("5 星") || item.text.includes("5星"));
      if (five) {
        await ratingControl.selectOption(five.value).catch(() => {});
        ratingDone = true;
      }
    } else if (controlInfo.tag === "input" && controlInfo.type === "radio") {
      await ratingControl.check({ force: true }).catch(() => {});
      ratingDone = await ratingControl.isChecked().catch(() => false);
    } else {
      try {
        await ratingControl.click({ timeout: 3000 });
        ratingDone = true;
      } catch {
        ratingDone = false;
      }
    }
  }
  if (!ratingDone) {
    const hiddenRadio = scope.locator('input[type="radio"][name="rating"][value="5"]').first();
    if ((await hiddenRadio.count().catch(() => 0)) > 0) {
      await hiddenRadio.check({ force: true }).catch(() => {});
      ratingDone = await hiddenRadio.isChecked().catch(() => false);
    }
  }
  if (!ratingDone) {
    throw new GuardViolation("no interactable 5-star rating control in review form; body=" + oneLine(await bodyText(page), 700));
  }
  await fillPreferredField(page, {
    names: ["title"],
    labels: ["评价标题|标题"],
    placeholders: ["标题", "一句话"],
  }, ratingTitle, { scope, description: "review title", timeoutMs: 2000, required: false }).catch(() => null);
  await fillPreferredField(page, {
    names: ["body"],
    labels: ["评价内容|使用感受|评价|感受"],
    placeholders: ["评价", "感受", "分享"],
  }, reviewBody, { scope, description: "review body", timeoutMs: 4000, required: true });
  const submitCandidates = [
    scope.locator('[data-testid="review-submit"]'),
    scope.getByRole("button", { name: /提交评价|发布评价|提交|发布/ }),
    page.locator('[data-testid="review-submit"]'),
  ];
  const submit = await firstVisible(submitCandidates, {
    timeoutMs: 6000,
    description: "review submit control",
    page,
  });
  const reviewStart = C.reviewEvents.length;
  await submit.click({ timeout: 8000 });
  const reviewEvent = await waitForApiEvent(C.reviewEvents, reviewStart, {
    page,
    timeoutMs: 22000,
    description: "real review POST",
    maybeClickConfirm: async () => maybeClickDialogButton(page, /确认|提交|确定/),
  });
  await ensureApiOkEvent(reviewEvent, "submit review");
  await waitUntil(async () => {
    const detail = await getApiData(page, "/api/orders/" + encodeURIComponent(C.orderNo));
    const items = [];
    for (const pkg of detail.packages || []) {
      for (const item of pkg.items || []) items.push(item);
    }
    return items.some((item) => item.reviewed === true);
  }, { timeoutMs: 15000, intervalMs: 300, description: "server order item reviewed=true" });
  await waitForBodyMatch(page, /已评价|评价成功|感谢|谢谢/, 10000, "review success in DOM").catch(() => null);
  await page.goto(C.baseUrl + C.buyerOrderPath, { waitUntil: "domcontentloaded" });
  await assertPageReady(page, "buyer-order-reviewed", { contains: "/orders/" });
  let reviews;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    reviews = await getApiData(page, "/api/products/" + encodeURIComponent(C.fixture.slug) + "/reviews?sort=recent&page_size=24");
    const hit = (reviews.items || []).some((item) => item && item.body === reviewBody);
    if (hit) break;
    await sleep(300);
  }
  const reviewHit = (reviews.items || []).some((item) => item && item.body === reviewBody);
  if (!reviewHit) {
    throw new GuardViolation("submitted review body was not returned by real product reviews API after submit");
  }
  const orderTextAfterReview = await bodyText(page);
  if (!orderTextAfterReview.includes(reviewBody) && !orderTextAfterReview.includes(ratingTitle) && !/已评价/.test(orderTextAfterReview)) {
    throw new GuardViolation("buyer order DOM after reload does not show reviewed state or review content; body=" + oneLine(orderTextAfterReview, 800));
  }
  C.reviewTitle = ratingTitle;
  C.reviewBody = reviewBody;
  await takeShot(page, "11-buyer-reviewed-desktop.png");
}

async function stageMobile() {
  if (!C.orderNo) throw new GuardViolation("no order_no captured for mobile order page");
  if (!C.buyerContext) throw new GuardViolation("buyer context unavailable for mobile storage state");
  const storageState = await C.buyerContext.storageState();
  C.mobileContext = await C.browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 1,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    storageState,
  });
  C.mobilePage = await preparePage(C.mobileContext, "buyer-mobile-390");
  const page = C.mobilePage;
  await page.goto(C.baseUrl + "/", { waitUntil: "domcontentloaded" });
  await assertPageReady(page, "mobile-home-390");
  const viewportWidth = await page.evaluate(() => window.innerWidth);
  C.mobileViewportWidth = viewportWidth;
  if (Math.abs(Number(viewportWidth) - 390) > 1) {
    throw new GuardViolation("mobile viewport innerWidth=" + viewportWidth + ", expected 390");
  }
  const productLink = await firstVisible([page.locator('#app a[href^="/p/"]')], {
    timeoutMs: 6000,
    description: "mobile home product link",
    page,
    required: false,
  });
  if (!productLink) throw new GuardViolation("mobile home has no product links");
  await takeShot(page, "12-mobile-home-390.png");
  await page.goto(C.baseUrl + C.buyerOrderPath, { waitUntil: "domcontentloaded" });
  await assertPageReady(page, "mobile-order-390", { contains: "/orders/" });
  const mobileOrder = await getApiData(page, "/api/orders/" + encodeURIComponent(C.orderNo));
  if (mobileOrder.status !== "COMPLETED") {
    throw new GuardViolation("mobile order page API status is " + JSON.stringify(mobileOrder.status) + ", expected COMPLETED");
  }
  const mobileText = await bodyText(page);
  if (!/已完成|评价/.test(mobileText)) {
    throw new GuardViolation("mobile order DOM lacks completed/review state; body=" + oneLine(mobileText, 700));
  }
  await takeShot(page, "13-mobile-order-390.png");
}



