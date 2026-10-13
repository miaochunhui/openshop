// AI mediator page controller. Build/test without a framework, no external calls from the browser.
import { api, ApiError } from "../core/api.js";
import { state, updateCartCount, refreshCart } from "../core/store.js";
import { openAuth, toast } from "../components/index.js";
import {
  pageShellHtml,
  statusCardHtml,
  loadingStatusHtml,
  userBubbleHtml,
  assistantBubbleHtml,
  loadingBubbleHtml,
  errorBubbleHtml,
  resultHtml,
} from "./ai_view.js";

const LOGIN_REASON = "登录后才能让 AI 中介为你比对方案，并把结果安全地加入自己的购物车。";

function isAbort(error) {
  return Boolean(error && (error.name === "AbortError" || error.code === "ABORT_ERR"));
}

function friendlyError(error) {
  if (error instanceof ApiError) {
    if (error.status === 401) return "登录状态已失效，请重新登录后再试。";
    return error.message || "请求没有完成，请稍后重试。";
  }
  return "网络或服务暂时不可用，请稍后重试。";
}

export async function render(ctx) {
  ctx.setTitle("AI 中介 · OpenShop 开集");
  ctx.root.innerHTML = pageShellHtml();
  const page = ctx.root.querySelector('[data-testid="ai-page"]');
  if (!page) return;
  const statusEl = page.querySelector("#ai-status-card");
  const form = page.querySelector("#ai-form");
  const input = page.querySelector("#ai-input");
  const submit = page.querySelector("#ai-submit");
  const countEl = page.querySelector("#ai-count");
  const errorEl = page.querySelector("#ai-error");
  const threadSection = page.querySelector("#ai-thread-section");
  const thread = page.querySelector("#ai-thread");
  const resultRegion = page.querySelector("#ai-result-region");
  const resetButton = page.querySelector("#ai-reset");
  const disclosureEl = page.querySelector("#ai-disclosure");
  const exampleButtons = Array.from(page.querySelectorAll("[data-testid='ai-example']"));

  let status = null;
  let statusSeq = 0;
  let history = [];
  let latest = null;
  let busy = false;
  let adopted = false;
  let loadingNode = null;

  function aborted() {
    return ctx.signal.aborted;
  }

  function updateCharCount() {
    countEl.textContent = String(input.value.length);
  }

  function hideError() {
    errorEl.hidden = true;
    errorEl.replaceChildren();
  }

  function showError(message, { login = false } = {}) {
    errorEl.replaceChildren();
    const text = document.createElement("span");
    text.textContent = message;
    errorEl.appendChild(text);
    if (login) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn--ghost btn--small";
      button.dataset.testid = "ai-login";
      button.textContent = "登录 / 刷新会话";
      errorEl.appendChild(button);
    }
    errorEl.hidden = false;
  }

  function applyQuota(quota) {
    if (!status || !quota) return;
    status.usage = status.usage || {};
    if (typeof quota.user_remaining_today === "number") {
      status.usage.user_remaining_today = quota.user_remaining_today;
      if (status.limits && typeof status.limits.user_daily === "number") {
        status.usage.user_used_today = Math.max(0, status.limits.user_daily - quota.user_remaining_today);
      }
    }
    if (typeof quota.global_remaining_today === "number") {
      status.usage.global_remaining_today = quota.global_remaining_today;
      if (status.limits && typeof status.limits.global_daily === "number") {
        status.usage.global_used_today = Math.max(0, status.limits.global_daily - quota.global_remaining_today);
      }
    }
    renderStatus();
    updateControls();
  }

  function renderStatus() {
    if (!statusEl || !status) return;
    statusEl.innerHTML = statusCardHtml(status, state.user);
    if (disclosureEl && status.disclosure) disclosureEl.textContent = status.disclosure;
  }

  function renderStatusError(error) {
    if (!statusEl) return;
    statusEl.replaceChildren();
    const wrap = document.createElement("div");
    wrap.className = "ai-status-error";
    const label = document.createElement("p");
    label.className = "eyebrow";
    label.textContent = "BROKER DESK";
    const title = document.createElement("h2");
    title.textContent = "中介席位暂不可用";
    const message = document.createElement("p");
    message.textContent = error instanceof ApiError ? error.message : "AI 状态暂时无法读取。";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.id = "ai-status-retry";
    retry.className = "btn btn--ghost btn--small";
    retry.textContent = "重新连接";
    wrap.append(label, title, message, retry);
    statusEl.appendChild(wrap);
  }

  function updateControls() {
    const available = Boolean(status && status.enabled);
    const usage = (status && status.usage) || {};
    const globalBlocked = typeof usage.global_remaining_today === "number" && usage.global_remaining_today <= 0;
    const userBlocked = Boolean(state.user) && typeof usage.user_remaining_today === "number" && usage.user_remaining_today <= 0;
    const blocked = globalBlocked || userBlocked;
    input.disabled = !available || busy;
    form.setAttribute("aria-busy", busy ? "true" : "false");
    exampleButtons.forEach(function (button) { button.disabled = !available || busy; });
    submit.disabled = !available || blocked || busy;
    if (busy) submit.textContent = "AI 正在比对…";
    else if (!available) submit.textContent = "AI 中介未启用";
    else if (blocked) submit.textContent = "今日额度已用完";
    else if (!state.user) submit.textContent = "登录后开始";
    else submit.textContent = "让 AI 比一比";
  }

  async function loadStatus({ silent = false } = {}) {
    const seq = ++statusSeq;
    if (!silent && statusEl) statusEl.innerHTML = loadingStatusHtml();
    try {
      const data = await api.get("/api/ai/status", { signal: ctx.signal });
      if (aborted() || seq !== statusSeq) return;
      status = data;
      renderStatus();
      updateControls();
    } catch (error) {
      if (aborted() || isAbort(error) || seq !== statusSeq) return;
      status = null;
      renderStatusError(error);
      updateControls();
    }
  }

  function openLogin(reason) {
    openAuth({
      reason: reason || LOGIN_REASON,
      onSuccess: async function () {
        await loadStatus({ silent: true });
        if (!aborted()) input.focus();
      },
    });
  }

  function addTurn(html) {
    threadSection.hidden = false;
    thread.insertAdjacentHTML("beforeend", html);
  }

  function addLoading() {
    removeLoading();
    thread.insertAdjacentHTML("beforeend", loadingBubbleHtml());
    loadingNode = thread.querySelector('[data-testid="ai-loading"]');
  }

  function removeLoading() {
    if (loadingNode && loadingNode.isConnected) loadingNode.remove();
    loadingNode = null;
  }

  function renderResult() {
    if (!latest) {
      resultRegion.hidden = true;
      resetButton.hidden = true;
      return;
    }
    resultRegion.innerHTML = resultHtml(latest, adopted);
    resultRegion.hidden = false;
    resetButton.hidden = false;
    const adoptButton = resultRegion.querySelector('[data-testid="ai-adopt"]');
    if (adoptButton && !(latest.recommendation && latest.recommendation.plan_id)) {
      adoptButton.disabled = true;
      adoptButton.textContent = "方案不可采纳";
    }
  }

  function reset() {
    if (busy) return;
    history = [];
    latest = null;
    adopted = false;
    removeLoading();
    thread.replaceChildren();
    resultRegion.replaceChildren();
    resultRegion.hidden = true;
    threadSection.hidden = true;
    resetButton.hidden = true;
    input.value = "";
    updateCharCount();
    hideError();
    updateControls();
    input.focus();
  }

  function hasLimitBlock() {
    if (!status || !status.enabled) return true;
    const usage = status.usage || {};
    if (typeof usage.global_remaining_today === "number" && usage.global_remaining_today <= 0) return true;
    if (state.user && typeof usage.user_remaining_today === "number" && usage.user_remaining_today <= 0) return true;
    return false;
  }

  async function submitRequest(text) {
    if (busy) return;
    if (!status || !status.enabled) {
      showError("AI 中介当前未启用，仍可继续浏览和购买商品。");
      return;
    }
    if (hasLimitBlock()) {
      showError("今日 AI 中介额度已经用完，请明天再来。额度会按北京时间每天重置。");
      return;
    }
    if (!state.user) {
      openLogin(LOGIN_REASON);
      return;
    }
    hideError();
    removeLoading();
    busy = true;
    latest = null;
    adopted = false;
    resultRegion.hidden = true;
    input.value = "";
    updateCharCount();
    addTurn(userBubbleHtml(text));
    addLoading();
    updateControls();
    let shouldRefresh = false;
    try {
      const data = await api.post("/api/ai/mediate", {
        message: text,
        history: history.slice(-6),
      }, { signal: ctx.signal });
      if (aborted()) return;
      removeLoading();
      latest = data;
      adopted = false;
      addTurn(assistantBubbleHtml(data.answer || "AI 已返回结果。"));
      history.push({ role: "user", content: text });
      history.push({ role: "assistant", content: data.answer || "" });
      history = history.slice(-6);
      applyQuota(data.quota);
      renderResult();
    } catch (error) {
      if (aborted() || isAbort(error)) return;
      removeLoading();
      const message = friendlyError(error);
      showError(message, { login: error instanceof ApiError && error.status === 401 });
      addTurn(errorBubbleHtml(message));
      input.value = text;
      updateCharCount();
      shouldRefresh = Boolean(status && status.enabled);
    } finally {
      if (!aborted()) {
        busy = false;
        removeLoading();
        updateControls();
        if (shouldRefresh) void loadStatus({ silent: true });
      }
    }
  }

  function showAdoptError(message, login) {
    const old = resultRegion.querySelector(".ai-adopt-error");
    if (old) old.remove();
    const box = document.createElement("div");
    box.className = "ai-adopt-error";
    box.setAttribute("role", "alert");
    const text = document.createElement("span");
    text.textContent = message;
    box.appendChild(text);
    if (login) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn--ghost btn--small";
      button.dataset.testid = "ai-login";
      button.textContent = "登录 / 刷新会话";
      box.appendChild(button);
    }
    const anchor = resultRegion.querySelector(".ai-offer__actions") || resultRegion.firstElementChild;
    if (anchor) anchor.prepend(box);
    else resultRegion.prepend(box);
  }

  async function adopt() {
    if (busy) return;
    if (!latest || !latest.recommendation) return;
    if (!state.user) {
      openLogin("登录后才能采纳 AI 方案，并把商品加入你的购物车。");
      return;
    }
    const planId = latest.recommendation.plan_id;
    if (!planId) return;
    const button = resultRegion.querySelector('[data-testid="ai-adopt"]');
    if (button) {
      button.disabled = true;
      button.textContent = "正在加入购物车…";
    }
    try {
      const data = await api.post("/api/ai/plans/" + encodeURIComponent(planId) + "/adopt", {}, { signal: ctx.signal });
      if (aborted()) return;
      adopted = true;
      const count = Number(data && data.cart && data.cart.summary && data.cart.summary.item_count);
      if (Number.isFinite(count)) updateCartCount(count);
      else await refreshCart({ signal: ctx.signal });
      if (aborted()) return;
      renderResult();
      toast("方案已加入购物车，请确认报价与数量。", { type: "success" });
    } catch (error) {
      if (aborted() || isAbort(error)) return;
      if (button) {
        button.disabled = false;
        button.textContent = "采用方案，加入购物车";
      }
      const isLogin = error instanceof ApiError && error.status === 401;
      showAdoptError(friendlyError(error), isLogin);
      if (isLogin) openLogin("登录后才能采纳这个 AI 方案。");
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const text = input.value.trim();
    if (text.length < 2) {
      showError("请至少写两个字，让 AI 知道你到底想买什么。");
      input.focus();
      return;
    }
    if (text.length > 500) {
      showError("购物委托最多 500 字。");
      input.focus();
      return;
    }
    void submitRequest(text);
  });

  input.addEventListener("input", updateCharCount);
  exampleButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      input.value = button.dataset.example || "";
      updateCharCount();
      hideError();
      input.focus();
    });
  });

  page.addEventListener("click", function (event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.matches('[data-testid="ai-login"]')) {
      openLogin(button.textContent === "登录后开始" ? LOGIN_REASON : "登录后才能使用 AI 中介。");
    } else if (button.matches('[data-testid="ai-adopt"]')) {
      void adopt();
    } else if (button.id === "ai-status-retry") {
      void loadStatus();
    } else if (button.id === "ai-reset") {
      reset();
    }
  });

  updateCharCount();
  updateControls();
  await loadStatus();
}
