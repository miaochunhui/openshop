// UI helpers for the real OpenShop browser acceptance flow.
// No API mocking; API reads in this module use the real backend only.
import { GuardViolation, firstVisible } from "./browser_guard.mjs";

export function escapeRegex(value) {
  return String(value).replace(/[\\^$.*+?()[\]{}|]/g, "\\$&");
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function nowIso() {
  return new Date().toISOString();
}

export async function fetchJsonNode(url, { timeoutMs = 10000 } = {}) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await response.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }
  return { status: response.status, json, text };
}

export async function getApiData(page, apiPath) {
  const result = await page.evaluate(async (target) => {
    const response = await fetch(target, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      credentials: "same-origin",
    });
    const text = await response.text();
    let json = null;
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
    return { status: response.status, json, text: text.slice(0, 1200) };
  }, apiPath);
  if (result.status !== 200) {
    const code = result.json?.error?.code || "HTTP_" + result.status;
    const message = result.json?.error?.message || result.text;
    throw new GuardViolation(
      "API read " + apiPath + " failed with HTTP " + result.status + " " + code + ": " + message,
    );
  }
  if (!result.json || !Object.prototype.hasOwnProperty.call(result.json, "data")) {
    throw new GuardViolation("API read " + apiPath + " is not a data envelope");
  }
  if (result.json.meta?.demo !== true) {
    throw new GuardViolation("API read " + apiPath + " did not declare meta.demo=true");
  }
  return result.json.data;
}

export async function visibleDialog(page) {
  const dialogs = page.locator('[role="dialog"]');
  const count = await dialogs.count().catch(() => 0);
  for (let i = count - 1; i >= 0; i -= 1) {
    const dialog = dialogs.nth(i);
    if (await dialog.isVisible().catch(() => false)) return dialog;
  }
  return null;
}

export async function maybeClickDialogButton(page, nameRegex) {
  const dialog = await visibleDialog(page);
  if (!dialog) return null;
  const buttons = dialog.getByRole("button", { name: nameRegex });
  const count = await buttons.count().catch(() => 0);
  for (let i = 0; i < count; i += 1) {
    const button = buttons.nth(i);
    if (await button.isVisible().catch(() => false)) {
      const text = (await button.innerText().catch(() => "")).trim();
      if (await button.isEnabled().catch(() => false)) {
        await button.click({ timeout: 5000 });
        return text;
      }
    }
  }
  return null;
}

export async function chooseSelectOption(selectLocator, desired) {
  const options = await selectLocator.locator("option").evaluateAll((nodes) =>
    nodes.map((node) => ({ text: (node.textContent || "").trim(), value: node.value, disabled: node.disabled })),
  );
  const viable = options.filter((option) => !option.disabled && option.value !== "" && option.text);
  let match =
    viable.find((option) => option.text === desired) ||
    viable.find((option) => option.text.includes(desired)) ||
    viable.find((option) => desired.includes(option.text) && option.text.length >= 2) ||
    viable.find((option) => option.text.replace(/[省市区]/g, "") === desired.replace(/[省市区]/g, "")) ||
    viable.find((option) => option.text.replace(/[省市区]/g, "").includes(desired.replace(/[省市区]/g, "")));
  if (!match) match = viable[0];
  if (!match) throw new GuardViolation("select has no usable non-empty option while setting " + desired);
  await selectLocator.selectOption(match.value);
  return match.text;
}

export async function fillPreferredField(
  page,
  spec,
  value,
  { scope = null, description = "form field", timeoutMs = 4000, required = true } = {},
) {
  const root = scope || page;
  const candidates = [];
  for (const name of spec.names || []) {
    candidates.push(root.locator('input[name="' + name + '"], textarea[name="' + name + '"], select[name="' + name + '"]'));
  }
  for (const label of spec.labels || []) {
    candidates.push(root.getByLabel(new RegExp(label)));
  }
  for (const placeholder of spec.placeholders || []) {
    candidates.push(
      root.locator('input[placeholder*="' + placeholder + '"], textarea[placeholder*="' + placeholder + '"]'),
    );
  }
  const field = await firstVisible(candidates, { timeoutMs, description, page, required });
  if (!field) return null;
  const tag = await field.evaluate((el) => el.tagName.toLowerCase()).catch(() => "");
  if (tag === "select") {
    await chooseSelectOption(field, String(value));
    return field;
  }
  await field.fill(String(value), { timeout: 5000 });
  return field;
}

export async function formScopeFor(page, receiverName = "receiver") {
  const candidates = [
    page.locator('form:has(input[name="' + receiverName + '"])'),
    page.locator('[role="dialog"]:has(input[name="' + receiverName + '"])'),
    page.locator("form:has(textarea[name=body])"),
    page.locator('[role="dialog"]:has(textarea[name=body])'),
  ];
  for (const candidate of candidates) {
    const count = await candidate.count().catch(() => 0);
    for (let i = count - 1; i >= 0; i -= 1) {
      if (await candidate.nth(i).isVisible().catch(() => false)) return candidate.nth(i);
    }
  }
  const dialog = await visibleDialog(page);
  if (dialog) return dialog;
  return page;
}

export async function maybeDismissTransientOverlay(page) {
  const dialog = await visibleDialog(page);
  if (!dialog) return false;
  const close = dialog.getByRole("button", { name: /关闭|继续购物|取消/ });
  const count = await close.count().catch(() => 0);
  for (let i = 0; i < count; i += 1) {
    const button = close.nth(i);
    if (await button.isVisible().catch(() => false)) {
      await button.click({ timeout: 4000 }).catch(() => {});
      return true;
    }
  }
  return false;
}

export async function bodyText(page) {
  return page.locator("body").innerText({ timeout: 3000 });
}

export async function waitForApiEvent(events, startIndex, {
  page,
  timeoutMs = 20000,
  description = "API write response",
  maybeClickConfirm = null,
} = {}) {
  const started = Date.now();
  const deadline = started + timeoutMs;
  let clickedConfirm = false;
  while (Date.now() < deadline) {
    if (events.length > startIndex) return events[startIndex];
    if (!clickedConfirm && maybeClickConfirm && Date.now() - started > 700) {
      const label = await maybeClickConfirm().catch(() => null);
      if (label) clickedConfirm = true;
    }
    await sleep(200);
  }
  const snippet = await bodyText(page).then((text) => text.replace(/\s+/g, " ").slice(0, 800)).catch(() => "<body unavailable>");
  throw new GuardViolation("timed out waiting for " + description + "; url=" + page.url() + "; body=" + snippet);
}

export async function openDemoLogin(page, role) {
  const account = page.locator("#account-action");
  await account.waitFor({ state: "visible", timeout: 8000 });
  await account.click();
  const demoButtons = page.locator('[data-demo="' + role + '"]');
  const button = await firstVisible([demoButtons], {
    timeoutMs: 8000,
    description: "one-click demo " + role + " button",
    page,
  });
  const label = (await button.innerText().catch(() => "")).replace(/\s+/g, " ").slice(0, 160);
  await button.click({ timeout: 5000 });
  const expectedLabel = role === "buyer" ? /你好|林澄/ : /商家|周屿/;
  const deadline = Date.now() + 15000;
  let last = "";
  while (Date.now() < deadline) {
    const scrims = page.locator(".modal-scrim");
    const modalOpen = (await scrims.count().catch(() => 0)) > 0 &&
      (await scrims.last().isVisible().catch(() => false));
    const text = (await page.locator("#account-label").innerText().catch(() => "")).trim();
    last = "accountLabel=" + JSON.stringify(text) + " modalOpen=" + String(modalOpen);
    if (!modalOpen && expectedLabel.test(text)) return { label, accountLabel: text };
    await sleep(200);
  }
  throw new GuardViolation(
    "one-click " + role + " demo login did not update the account chrome within 15s; " + last,
  );
}

export async function clickAddToCart(page) {
  const addCandidates = [
    page.locator('[data-testid="add-to-cart"]'),
    page.getByRole("button", { name: /加入购物车|加入购物袋|加购/ }),
    page.getByRole("link", { name: /加入购物车|加入购物袋/ }),
  ];
  let button = await firstVisible(addCandidates, {
    timeoutMs: 4000,
    description: "add-to-cart control",
    page,
    required: false,
  });
  if (!button || !(await button.isEnabled().catch(() => false))) {
    const selectors = [
      page.locator('[data-testid^="sku-option"]'),
      page.locator("[data-sku-id]"),
      page.locator(".sku-option"),
      page.locator(".sku-choice"),
      page.locator(".offer-option"),
      page.locator("[data-offer-id]"),
      page.locator('[role="radio"]'),
    ];
    const option = await firstVisible(selectors, {
      timeoutMs: 2500,
      description: "offer/SKU option before add-to-cart",
      page,
      required: false,
    });
    if (option) {
      await option.click({ timeout: 5000 }).catch(() => {});
      await sleep(300);
    }
    button = await firstVisible(addCandidates, {
      timeoutMs: 6000,
      description: "enabled add-to-cart control after selecting options",
      page,
      required: false,
    });
  }
  if (!button) {
    throw new GuardViolation("add-to-cart control not found on product page; url=" + page.url());
  }
  if (!(await button.isEnabled().catch(() => true))) {
    throw new GuardViolation("add-to-cart control is disabled; product options were not resolvable");
  }
  await button.click({ timeout: 8000 });
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    await page.waitForTimeout(200);
    const errorText = await page.locator(".toast--error, [role=alert]").first().innerText().catch(() => "");
    if (errorText.trim()) {
      throw new GuardViolation("add-to-cart surfaced an error: " + errorText.replace(/\s+/g, " ").slice(0, 400));
    }
    const toast = (await page.locator("#toast-region").innerText().catch(() => "")).trim();
    if (/已加入|加入购物车|添加成功|成功加入/.test(toast)) {
      return { feedback: "toast", text: toast.replace(/\s+/g, " ").slice(0, 200) };
    }
    const badge = page.locator("#cart-badge");
    if (await badge.isVisible().catch(() => false)) {
      const badgeText = (await badge.innerText().catch(() => "")).trim();
      if (badgeText && badgeText !== "0") return { feedback: "cart-badge", text: badgeText };
    }
  }
  throw new GuardViolation("add-to-cart produced no visible success toast or non-zero cart badge within 15s");
}
