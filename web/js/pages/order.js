// Order detail: server-driven actions, packages, timeline, reviews and group sharing.
import { api, ApiError } from "../core/api.js";
import { state } from "../core/store.js";
import {
  escapeHtml,
  icon,
  money,
  openAuth,
  openConfirm,
  openModal,
  priceHtml,
  toast,
} from "../components/index.js";

const ERROR_HINTS = {
  ORDER_STATE: "订单状态刚刚变化，请以最新状态为准。",
  DUPLICATE_REVIEW: "这一单里已经有评价过了，不能重复提交。",
  AFTER_SALES_UNSUPPORTED: "多店铺订单暂不支持整单售后，请联系演示客服。",
  AUTH_REQUIRED: "登录状态已失效，请重新登录。",
  GROUP_FULL: "这个团已经满员，不能继续加入。",
};

const REFUND_STATUS = {
  REQUESTED: "等待商家处理",
  APPROVED: "商家已同意",
  REJECTED: "商家已拒绝",
  CANCELLED: "已撤销",
};

const REFUND_KIND = {
  refund_only: "仅退款",
  return_refund: "退货退款",
};

const GROUP_STATUS = {
  FORMING: "正在成团",
  FULL: "已成团",
  CANCELLED: "已取消",
  EXPIRED: "已过期",
};

const PACKAGE_STATUS = {
  PENDING: "待发货",
  SHIPPED: "已发货",
  DELIVERED: "已签收",
  CANCELLED: "已取消",
};

const ACTIONABLE_STATUS = new Set(["PAID", "SHIPPED", "COMPLETED"]);

function errMessage(err, fallback = "操作没有完成，请稍后再试。") {
  if (err instanceof ApiError) {
    const hint = ERROR_HINTS[err.code];
    return hint && hint !== err.message ? `${err.message} ${hint}` : err.message;
  }
  return fallback;
}

function dateText(value, withTime = true) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const options = { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" };
  if (withTime) { options.hour = "2-digit"; options.minute = "2-digit"; options.hour12 = false; }
  return new Intl.DateTimeFormat("zh-CN", options).format(date);
}

function amountHtml(cents, className = "") {
  return Number.isFinite(Number(cents)) && cents !== null
    ? priceHtml(cents, { className })
    : '<span class="muted">—</span>';
}

function statusClass(status) {
  const known = new Set(["PENDING_PAYMENT", "GROUP_FORMING", "PAID", "SHIPPED", "COMPLETED", "CANCELLED", "REFUNDING", "REFUNDED"]);
  return known.has(status) ? `order-status--${String(status).toLowerCase()}` : "";
}

function packageStatusLabel(pkg) {
  return pkg.status_label || PACKAGE_STATUS[pkg.status] || pkg.status || "状态待确认";
}

function fullAddress(address) {
  if (!address) return "—";
  return [address.province, address.city, address.district, address.detail].filter(Boolean).join(" ");
}

function groupShareUrl(code) {
  return `${window.location.origin}/g/${encodeURIComponent(String(code || ""))}`;
}

export async function render(ctx) {
  const orderNo = ctx.params.orderNo || "";
  let order = null;
  let loading = Boolean(state.user);
  let loadError = "";
  let alertMessage = "";
  let busyAction = "";
  let loginReason = "查看订单需要登录。使用买家演示即可继续，演示环境不会真实扣款。";

  function focusAfter(selector) {
    requestAnimationFrame(() => {
      if (ctx.signal.aborted) return;
      const target = ctx.root.querySelector(selector);
      if (target && typeof target.focus === "function") target.focus({ preventScroll: true });
    });
  }

  function pageShell(contentHtml, extraTestAttr = "") {
    return `<section class="container trade-page order-page" data-testid="order-root" data-order-no="${escapeHtml(orderNo)}" aria-labelledby="order-heading"${extraTestAttr}>
      ${contentHtml}
    </section>`;
  }

  function skeletonHtml() {
    return pageShell(`<div class="order-loading" aria-busy="true">
      <div class="skeleton" style="height:26px;width:22%"></div>
      <div class="skeleton mt-2" style="height:52px;width:44%"></div>
      <div class="trade-layout mt-4">
        <div class="stack"><div class="skeleton" style="height:200px;border-radius:var(--radius)"></div><div class="skeleton" style="height:130px;border-radius:var(--radius)"></div></div>
        <div class="skeleton" style="height:260px;border-radius:var(--radius)"></div>
      </div>
      <p class="sr-only" role="status">正在读取订单 ${escapeHtml(orderNo)}</p>
    </div>`, ' aria-busy="true"');
  }

  function loginHtml() {
    return pageShell(`<header class="order-head-simple">
      <p class="eyebrow">ORDER · ${escapeHtml(orderNo)}</p>
      <h1 id="order-heading" tabindex="-1">订单详情</h1>
    </header>
    <div class="trade-lock trade-card">
      <div class="trade-lock__icon">${icon("user", 28)}</div>
      <span class="eyebrow eyebrow--jade">SIGN IN TO VIEW</span>
      <h2>登录后查看这笔订单</h2>
      <p>${escapeHtml(loginReason)}</p>
      <div class="cluster mt-3"><button class="btn btn--primary" type="button" data-login>${icon("user", 16)} 一键买家登录</button><a class="btn btn--ghost" href="/account?tab=orders" data-link>回到我的开集</a></div>
      <p class="tiny muted">订单属于当前登录用户；非本人无法查看。</p>
    </div>`);
  }

  function errorHtml() {
    const needsLogin = loadError.includes("登录") || loadError.includes("会话");
    return pageShell(`<header class="order-head-simple">
      <p class="eyebrow">ORDER · ${escapeHtml(orderNo)}</p>
      <h1 id="order-heading" tabindex="-1">订单详情</h1>
    </header>
    <div class="trade-card order-fatal">
      <div class="empty-state__icon">${icon("alert", 26)}</div>
      <h2>这笔订单暂时没找到</h2>
      <p class="error-inline" role="alert">${escapeHtml(loadError)}</p>
      <div class="cluster mt-3">
        <button class="btn btn--primary" type="button" data-retry-order>重新读取</button>
        ${needsLogin ? '<button class="btn btn--ghost" type="button" data-login>重新登录</button>' : ""}
        <a class="btn btn--ghost" href="/account?tab=orders" data-link>我的订单</a>
        <a class="btn btn--quiet" href="/help" data-link>帮助</a>
      </div>
    </div>`);
  }

  function openLogin() {
    openAuth({
      reason: loginReason,
      onSuccess: () => { if (!ctx.signal.aborted) void render(ctx); },
    });
  }

  function bindBasic() {
    ctx.root.querySelectorAll("[data-login]").forEach((button) => button.addEventListener("click", openLogin));
    ctx.root.querySelector("[data-retry-order]")?.addEventListener("click", () => {
      loading = true;
      loadError = "";
      paint();
      void loadOrder();
    });
  }

  function actionButtonHtml(kind, testid, label, { primary = false, danger = false, quiet = false, iconName = "" } = {}) {
    const busy = busyAction === kind;
    const cls = ["btn", primary ? "btn--primary" : quiet ? "btn--quiet" : "btn--ghost", danger ? "text-accent" : ""].filter(Boolean).join(" ");
    return `<button class="${cls}" type="button" data-testid="${testid}" data-order-action="${kind}" ${busyAction ? "disabled" : ""}>${busy ? '<span class="spinner" aria-hidden="true"></span> 处理中' : `${iconName ? icon(iconName, 15) : ""} ${escapeHtml(label)}`}</button>`;
  }

  function packageItemHtml(item, order_) {
    const reviewState = item.reviewed ? "已评价" : item.reviewable ? "可评价" : "";
    return `<li class="order-item" data-testid="order-item" data-order-item-id="${escapeHtml(String(item.id || ""))}" data-order-no="${escapeHtml(order_.order_no || "")}">
      <a class="order-item__art" href="/p/${encodeURIComponent(item.slug || "")}" data-link aria-label="查看 ${escapeHtml(item.title || "商品")}">
        <img src="${escapeHtml(item.cover || "")}" alt="${escapeHtml(item.title || "商品")}" loading="lazy" decoding="async">
      </a>
      <div class="order-item__body">
        <a class="order-item__title" href="/p/${encodeURIComponent(item.slug || "")}" data-link>${escapeHtml(item.title || "商品")}</a>
        <p class="tiny muted">${escapeHtml(item.sku_label || "规格以订单为准")}</p>
        <div class="chip-row">
          ${item.offer_kind ? `<span class="chip chip--jade">${escapeHtml(item.offer_kind === "self" ? "官方自营" : item.offer_kind === "brand" ? "品牌旗舰" : "源头工厂")}</span>` : ""}
          ${reviewState ? `<span class="chip ${item.reviewed ? "chip--ghost" : "chip--honey"}">${reviewState}</span>` : ""}
          ${Array.isArray(item.services) ? item.services.slice(0, 2).map((service) => `<span class="chip chip--ghost">${escapeHtml(service)}</span>`).join("") : ""}
        </div>
      </div>
      <div class="order-item__numbers">
        <span>${amountHtml(item.unit_price_cents)} <span class="tiny muted">× ${Number(item.quantity) || 1}</span></span>
        <span class="tiny muted">商品金额以订单为准</span>
      </div>
    </li>`;
  }

  function packagesHtml(order_) {
    const packages = Array.isArray(order_.packages) ? order_.packages : [];
    if (!packages.length) return '<p class="muted small">订单中没有可展示的包裹。</p>';
    return packages.map((pkg, index) => `<section class="trade-card order-package" aria-labelledby="package-title-${Number(pkg.id) || index}">
      <div class="order-package__head">
        <div><span class="eyebrow">PACKAGE ${String(index + 1).padStart(2, "0")}</span><h2 id="package-title-${Number(pkg.id) || index}">${escapeHtml(pkg.shop?.name || "店铺包裹")}</h2></div>
        <span class="package-state">${escapeHtml(packageStatusLabel(pkg))}</span>
      </div>
      <div class="package-tracking">
        ${pkg.carrier || pkg.tracking_no ? `<span>${icon("truck", 15)} ${escapeHtml(pkg.carrier || "物流")} · <b>${escapeHtml(pkg.tracking_no || "")}</b></span><span class="tiny muted">${pkg.shipped_at ? `发货于 ${dateText(pkg.shipped_at)}` : ""}</span>` : `<span class="muted">${icon("truck", 15)} 等待商家发货</span>`}
      </div>
      <ul class="order-items">${(Array.isArray(pkg.items) ? pkg.items : []).map((item) => packageItemHtml(item, order_)).join("")}</ul>
      ${timelineHtml(Array.isArray(pkg.events) ? pkg.events : [], "包裹轨迹")}
    </section>`).join("");
  }

  function timelineHtml(events, ariaLabel = "订单时间线") {
    if (!events.length) return '<p class="muted tiny package-events-empty">还没有轨迹记录。</p>';
    return `<ol class="order-timeline" aria-label="${escapeHtml(ariaLabel)}">
      ${events.map((event) => `<li><span class="order-timeline__dot" aria-hidden="true"></span><div><b>${escapeHtml(event.label || "进展")}</b><p>${escapeHtml(event.detail || "")}</p><time datetime="${escapeHtml(event.created_at || "")}">${dateText(event.created_at)}</time></div></li>`).join("")}
    </ol>`;
  }

  function groupPanelHtml(group) {
    if (!group) return "";
    const joined = Number(group.joined_count) || 0;
    const required = Number(group.required_size) || 0;
    const percent = required > 0 ? Math.min(100, Math.round((joined / required) * 100)) : 0;
    const code = String(group.code || "");
    const share = groupShareUrl(code);
    const progressHint = joined >= required && required > 0 ? "人数已满" : group.status === "FORMING" ? `还差 ${Math.max(0, required - joined)} 人成团` : "本团已结束";
    return `<section class="trade-card order-group-panel" aria-labelledby="order-group-title">
      <div class="trade-section-head">
        <div><span class="trade-section-head__num">拼</span><div><h2 id="order-group-title">一起拼 · ${escapeHtml(code)}</h2><p>${escapeHtml(GROUP_STATUS[group.status] || group.status || "状态以服务端为准")} · 公开进度只统计已付款成员。</p></div></div>
        ${group.expires_at ? `<span class="chip chip--honey">截止 ${dateText(group.expires_at)}</span>` : ""}
      </div>
      <div class="group-progress" style="--group-progress:${percent}%" role="progressbar" aria-label="拼团进度" aria-valuemin="0" aria-valuemax="${required}" aria-valuenow="${joined}">
        <div class="group-progress__track"><span></span></div>
        <div class="group-progress__labels"><b>${joined} 人已付款</b><span>${progressHint} · 需要 ${required} 人</span></div>
      </div>
      <div class="group-share">
        <label class="field"><span class="field-label">把真实邀请链接发给朋友</span><input class="input" data-testid="group-share-url" readonly value="${escapeHtml(share)}"></label>
        <div class="cluster"><button class="btn btn--small btn--ghost" type="button" data-copy-group>${icon("copy", 14)} 复制邀请链接</button><a class="btn btn--small btn--ghost" href="/g/${encodeURIComponent(code)}" data-link>打开拼团页 ${icon("arrowRight", 13)}</a></div>
      </div>
      <p class="tiny muted">未成团、到期或成团后的处理以订单和服务端状态为准，本页不会伪造人数。</p>
    </section>`;
  }

  function refundsHtml(order_) {
    const refunds = Array.isArray(order_.refunds) ? order_.refunds : [];
    if (!refunds.length) return "";
    return `<section class="trade-card order-refunds" aria-labelledby="order-refunds-title">
      <div class="trade-section-head"><div><span class="trade-section-head__num">售</span><div><h2 id="order-refunds-title">售后记录</h2><p>整单单店铺售后，处理结果以商家决定为准。</p></div></div></div>
      <ul class="refund-list">${refunds.map((refund) => `<li>
        <div class="between"><b>${escapeHtml(REFUND_KIND[refund.kind] || refund.kind || "售后")} · ${amountHtml(refund.amount_cents)}</b><span class="chip ${refund.status === "REQUESTED" ? "chip--honey" : refund.status === "APPROVED" ? "chip--jade" : "chip--ghost"}">${escapeHtml(REFUND_STATUS[refund.status] || refund.status || "状态待确认")}</span></div>
        <p>${escapeHtml(refund.reason || "")}</p>
        <span class="tiny muted">${dateText(refund.created_at)}</span>
      </li>`).join("")}</ul>
    </section>`;
  }

  function summaryAsideHtml(order_) {
    const actions = order_.actions || {};
    return `<aside class="order-aside" aria-label="订单摘要">
      <div class="trade-summary">
        <div class="trade-summary__head"><div><p class="eyebrow">RECEIPT</p><h2>金额明细</h2></div></div>
        <dl class="amount-list">
          <div><dt>商品小计</dt><dd>${amountHtml(order_.subtotal_cents)}</dd></div>
          <div><dt>运费</dt><dd>${Number(order_.shipping_cents) === 0 ? "包邮" : amountHtml(order_.shipping_cents)}</dd></div>
          <div><dt>优惠</dt><dd>${Number(order_.discount_cents) > 0 ? `− ${amountHtml(order_.discount_cents)}` : "—"}</dd></div>
          ${Number(order_.market_savings_cents) > 0 ? `<div class="amount-list__save"><dt>相对市场价已省</dt><dd>${amountHtml(order_.market_savings_cents)}</dd></div>` : ""}
        </dl>
        <div class="trade-total"><span>订单总额</span><strong data-testid="order-total">${amountHtml(order_.total_cents, "price--lg")}</strong></div>
        <div class="order-payment-meta">
          <p><span>支付方式</span><b>${order_.payment_method === "demo" ? "模拟支付（DEMO）" : escapeHtml(order_.payment_method || "尚未支付")}</b></p>
          <p><span>支付状态</span><b>${order_.paid_at ? `模拟支付于 ${dateText(order_.paid_at)}` : "尚未支付"}</b></p>
          ${order_.shipping_method ? `<p><span>配送方式</span><b>${escapeHtml(order_.shipping_method === "express" ? "加急配送" : "标准配送")}</b></p>` : ""}
        </div>
        <p class="demo-note">支付为演示流程，不会产生真实资金变动。所有金额来自订单响应。</p>
      </div>
      <div class="trade-card order-address-card">
        <h3>收货信息</h3>
        <p class="strong">${escapeHtml(order_.address?.receiver || "—")} <span class="muted">${escapeHtml(order_.address?.phone || "")}</span></p>
        <p>${escapeHtml(fullAddress(order_.address))}</p>
        ${order_.address?.label ? `<span class="chip">${escapeHtml(order_.address.label)}</span>` : ""}
        ${order_.note ? `<p class="tiny muted order-note">备注：${escapeHtml(order_.note)}</p>` : ""}
      </div>
      <div class="trade-card order-support">
        <b>需要帮助？</b>
        <p>查看 <a href="/help" data-link>配送与售后说明</a>。所有订单状态与可执行操作都由服务端计算，前端不会自行推断。</p>
        <a class="link-arrow" href="/account?tab=orders" data-link>回到我的订单 ${icon("arrowRight", 14)}</a>
      </div>
    </aside>`;
  }

  function actionAreaHtml(order_) {
    const actions = order_.actions || {};
    const packageCount = Array.isArray(order_.packages) ? order_.packages.length : 0;
    const buttons = [];
    if (actions.pay) buttons.push(actionButtonHtml("pay", "pay-order", "模拟支付", { primary: true, iconName: "card" }));
    if (actions.cancel) buttons.push(actionButtonHtml("cancel", "cancel-order", "取消订单", { iconName: "x" }));
    if (actions.refund && packageCount <= 1) buttons.push(actionButtonHtml("refund", "refund-request", "申请售后", { iconName: "return" }));
    if (actions.confirm) buttons.push(actionButtonHtml("confirm", "confirm-receipt", "确认收货", { primary: true, iconName: "check" }));
    if (actions.review) buttons.push(actionButtonHtml("review", "review-open", "评价商品", { iconName: "star" }));
    if (order_.group?.code) buttons.push(`<a class="btn btn--ghost" href="/g/${encodeURIComponent(order_.group.code)}" data-link>${icon("gift", 15)} 查看拼团</a>`);
    const multiShopNote = packageCount > 1 && ACTIONABLE_STATUS.has(order_.status)
      ? '<p class="tiny muted order-action-note">多店铺订单暂不支持整单售后，本页不会提交注定失败的申请。</p>'
      : "";
    if (!buttons.length) {
      return `<div class="order-action-area"><p class="muted small">当前没有可执行操作。订单状态或售后处理后，操作会由服务端重新给出。</p>${multiShopNote}</div>`;
    }
    return `<div class="order-action-area" data-testid="order-actions">${buttons.join("")}${actions.pay ? '<p class="tiny muted order-action-note">点击“模拟支付”只会调用 demo 支付接口，不会真实扣款。</p>' : ""}${multiShopNote}</div>`;
  }

  function detailHtml() {
    const order_ = order || {};
    const actions = order_.actions || {};
    const packageCount = Array.isArray(order_.packages) ? order_.packages.length : 0;
    return pageShell(`<header class="order-hero trade-card">
      <div class="order-hero__main">
        <p class="eyebrow">ORDER · ${escapeHtml(order_.order_no || orderNo)}</p>
        <div class="order-hero__title">
          <h1 id="order-heading" tabindex="-1">订单详情</h1>
          <span class="order-status ${statusClass(order_.status)}" data-testid="order-status" data-order-status="${escapeHtml(order_.status || "")}" tabindex="-1" id="order-status">${escapeHtml(order_.status_label || "状态待确认")}</span>
        </div>
        <p class="order-hero__meta">下单于 ${dateText(order_.created_at)} · 共 ${Number(order_.item_count) || 0} 件 · ${Number(order_.shop_count) || 0} 个店铺</p>
      </div>
      ${actionAreaHtml(order_)}
    </header>
    ${alertMessage ? `<div class="error-inline trade-alert order-alert" role="alert" tabindex="-1">${icon("alert", 16)} <span>${escapeHtml(alertMessage)}</span></div>` : ""}
    <div class="trade-layout order-layout">
      <div class="order-main stack">
        ${groupPanelHtml(order_.group)}
        <section class="order-packages" aria-label="订单包裹">
          <div class="order-section-heading"><p class="eyebrow">PARCELS</p><h2>包裹与物流</h2><p>按店铺拆分；每个包裹的状态由后端独立维护。</p></div>
          ${packagesHtml(order_)}
        </section>
        <section class="trade-card order-timeline-card" aria-labelledby="order-timeline-title">
          <div class="trade-section-head"><div><span class="trade-section-head__num">轨</span><div><h2 id="order-timeline-title">订单进展</h2><p>时间均为 Asia/Shanghai 展示。</p></div></div></div>
          ${timelineHtml(Array.isArray(order_.timeline) ? order_.timeline : [])}
        </section>
        ${refundsHtml(order_)}
        <div class="order-footer-links"><a class="link-arrow" href="/account?tab=orders" data-link>${icon("arrowLeft", 14)} 回到我的订单</a><a class="link-arrow" href="/search" data-link>继续逛逛 ${icon("arrowRight", 14)}</a><a class="link-arrow" href="/help" data-link>帮助与规则 ${icon("arrowRight", 14)}</a></div>
      </div>
      ${summaryAsideHtml(order_)}
    </div>
    <p class="order-footnote">${packageCount ? `本单拆分为 ${packageCount} 个包裹。` : ""} 演示支付，不产生真实扣款；如需售后请以服务端 actions 与售后记录为准。</p>`);
  }

  function paint() {
    if (ctx.signal.aborted) return;
    if (loading) {
      ctx.root.innerHTML = skeletonHtml();
      return;
    }
    if (!state.user) {
      ctx.root.innerHTML = loginHtml();
      bindBasic();
      return;
    }
    if (loadError) {
      ctx.root.innerHTML = errorHtml();
      bindBasic();
      return;
    }
    if (!order) return;
    ctx.root.innerHTML = detailHtml();
    bindDetail();
    if (alertMessage) focusAfter(".order-alert");
  }

  function bindDetail() {
    ctx.root.querySelectorAll("[data-login]").forEach((button) => button.addEventListener("click", openLogin));
    bindCopyGroup();
    ctx.root.querySelector("[data-testid='pay-order']")?.addEventListener("click", () => { void beginPay(); });
    ctx.root.querySelector("[data-testid='cancel-order']")?.addEventListener("click", () => { void cancelOrder(); });
    ctx.root.querySelector("[data-testid='confirm-receipt']")?.addEventListener("click", () => { void confirmReceipt(); });
    ctx.root.querySelector("[data-testid='refund-request']")?.addEventListener("click", () => openRefundForm());
    ctx.root.querySelector("[data-testid='review-open']")?.addEventListener("click", () => openReviewForm());
  }

  async function copyGroupLink() {
    const input = ctx.root.querySelector("[data-testid='group-share-url']");
    if (!(input instanceof HTMLInputElement)) return;
    const value = input.value;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
        toast("拼团邀请链接已复制");
        return;
      }
      throw new Error("clipboard unavailable");
    } catch (_) {
      input.focus();
      input.select();
      let copied = false;
      try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
      toast(copied ? "拼团邀请链接已复制" : "请手动复制拼团邀请链接", { type: copied ? "success" : "info" });
    }
  }

  function bindCopyGroup() {
    ctx.root.querySelector("[data-copy-group]")?.addEventListener("click", () => { void copyGroupLink(); });
    ctx.root.querySelector("[data-copy-order-link]")?.addEventListener("click", () => { void copyGroupLink(); });
  }

  async function loadOrder() {
    loadError = "";
    try {
      const data = await api.get(`/api/orders/${encodeURIComponent(orderNo)}`, { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      if (!data || typeof data !== "object" || !data.order_no) throw new Error("订单响应缺少订单号");
      order = data;
      loading = false;
      loadError = "";
      ctx.setTitle(`订单 ${data.order_no} · OpenShop 开集`);
      paint();
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError") return;
      loading = false;
      loadError = errMessage(err, "订单暂时没有读出来，请稍后重试。");
      paint();
      focusAfter(".order-fatal .error-inline");
    }
  }

  async function reloadOrderAfterAction(successMessage) {
    try {
      const data = await api.get(`/api/orders/${encodeURIComponent(orderNo)}`, { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      if (data && typeof data === "object" && data.order_no) order = data;
      alertMessage = "";
      paint();
      focusAfter("#order-status");
      toast(successMessage);
    } catch (err) {
      if (ctx.signal.aborted) return;
      alertMessage = `操作已提交，但订单状态刷新失败：${errMessage(err, "请重新读取订单。")}`;
      paint();
    }
  }

  async function runAction(name, request, successMessage) {
    if (busyAction) return;
    busyAction = name;
    alertMessage = "";
    paint();
    let updated;
    try {
      updated = await request();
      if (ctx.signal.aborted) return;
    } catch (err) {
      if (ctx.signal.aborted) return;
      busyAction = "";
      alertMessage = errMessage(err);
      paint();
      focusAfter(".order-alert");
      return;
    }
    busyAction = "";
    if (updated && typeof updated === "object" && updated.order_no) {
      order = updated;
      alertMessage = "";
      paint();
      focusAfter("#order-status");
      toast(successMessage);
      return;
    }
    await reloadOrderAfterAction(successMessage);
  }

  async function beginPay() {
    if (busyAction || !order) return;
    await runAction("pay", () => api.post(`/api/orders/${encodeURIComponent(orderNo)}/pay`, { payment_method: "demo" }, { signal: ctx.signal }), "模拟支付已完成（演示）");
  }

  function confirmWithHook(options, testid) {
    const decision = openConfirm(options);
    const confirm = document.querySelector("#modal-root .modal__footer .btn--primary");
    const cancel = document.querySelectorAll("#modal-root .modal__footer .btn")[0];
    if (confirm) confirm.setAttribute("data-testid", testid);
    if (cancel && cancel !== confirm) cancel.setAttribute("data-testid", `${testid}-cancel`);
    return decision;
  }

  async function cancelOrder() {
    if (busyAction || !order) return;
    const confirmed = await confirmWithHook({
      title: "取消这笔订单？",
      message: "取消后库存与优惠券会按服务端规则释放，购物车不会自动恢复。本次取消不能从页面撤销。",
      confirmLabel: "取消订单",
      cancelLabel: "再想想",
      danger: true,
    }, "cancel-order-confirm");
    if (!confirmed || ctx.signal.aborted) return;
    await runAction("cancel", () => api.post(`/api/orders/${encodeURIComponent(orderNo)}/cancel`, {}, { signal: ctx.signal }), "订单已取消（以服务端状态为准）");
  }

  async function confirmReceipt() {
    if (busyAction || !order) return;
    const confirmed = await confirmWithHook({
      title: "确认已经收到货？",
      message: "确认收货后订单会完成，评价窗口随之打开；这个动作不能从页面撤回。请确认所有包裹已经到手。",
      confirmLabel: "确认收货",
      cancelLabel: "还没收到",
    }, "confirm-receipt-confirm");
    if (!confirmed || ctx.signal.aborted) return;
    await runAction("confirm", () => api.post(`/api/orders/${encodeURIComponent(orderNo)}/confirm`, {}, { signal: ctx.signal }), "已确认收货");
  }

  function orderItems() {
    const result = [];
    for (const pkg of Array.isArray(order?.packages) ? order.packages : []) {
      for (const item of Array.isArray(pkg.items) ? pkg.items : []) result.push(item);
    }
    return result;
  }

  function openRefundForm() {
    if (!order || busyAction) return;
    if (Array.isArray(order.packages) && order.packages.length > 1) {
      alertMessage = "多店铺订单暂不支持整单售后；本页不会提交会失败的申请。";
      paint();
      focusAfter(".order-alert");
      return;
    }
    const form = document.createElement("form");
    form.className = "trade-form";
    form.setAttribute("data-testid", "refund-form");
    form.innerHTML = `<div class="field" role="radiogroup" aria-label="售后类型"><span class="field-label">售后类型</span>
      <label class="trade-choice refund-kind"><input data-testid="refund-kind" type="radio" name="refund_kind" value="refund_only" checked><span class="trade-choice__mark" aria-hidden="true"></span><span><b>仅退款</b><span>不退回商品，仅处理款项</span></span></label>
      <label class="trade-choice refund-kind"><input data-testid="refund-kind-return" type="radio" name="refund_kind" value="return_refund"><span class="trade-choice__mark" aria-hidden="true"></span><span><b>退货退款</b><span>演示环境不提供真实退货物流</span></span></label>
    </div>
    <label class="field"><span class="field-label">申请原因 <span class="text-accent">*</span></span><textarea class="textarea" data-testid="refund-reason" name="reason" required maxlength="200" placeholder="请具体说明问题；需要 1-200 字。"></textarea></label>
    <label class="field"><span class="field-label">补充说明 <span class="tiny muted">选填</span></span><textarea class="textarea" data-testid="refund-detail" name="detail" maxlength="500" placeholder="可补充订单相关信息。"></textarea></label>
    <p class="error-inline" data-refund-error role="alert" tabindex="-1" hidden></p>
    <div class="notice">售后仅支持整单、单店铺；提交后订单会进入等待商家处理。演示环境不会产生真实退款。</div>
    <div class="trade-form__actions"><button class="btn btn--ghost" type="button" data-cancel>取消</button><button class="btn btn--primary" type="submit" data-testid="refund-submit" data-refund-submit>提交售后申请</button></div>`;
    const modal = openModal({ title: "申请整单售后", subtitle: "服务端会复核订单状态与店铺范围。", body: form, wide: true });
    form.querySelector("[data-cancel]")?.addEventListener("click", () => modal.close());
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const submit = form.querySelector("[data-refund-submit]");
      const error = form.querySelector("[data-refund-error]");
      if (submit.disabled) return;
      error.hidden = true;
      submit.disabled = true;
      submit.innerHTML = '<span class="spinner" aria-hidden="true"></span> 提交中';
      const values = new FormData(form);
      const reason = String(values.get("reason") || "").trim();
      if (!reason) {
        error.textContent = "请填写售后原因。";
        error.hidden = false;
        submit.disabled = false;
        submit.textContent = "提交售后申请";
        return;
      }
      const body = {
        reason,
        kind: String(values.get("refund_kind") || "refund_only"),
      };
      const detail = String(values.get("detail") || "").trim();
      if (detail) body.detail = detail;
      try {
        const updated = await api.post(`/api/orders/${encodeURIComponent(orderNo)}/refund`, body, { signal: ctx.signal });
        if (ctx.signal.aborted) return;
        modal.close();
        if (updated && typeof updated === "object" && updated.order_no) {
          order = updated;
          alertMessage = "";
          paint();
          focusAfter("#order-status");
          toast("售后申请已提交，等待商家处理");
        } else {
          await reloadOrderAfterAction("售后申请已提交，等待商家处理");
        }
      } catch (err) {
        if (ctx.signal.aborted) return;
        submit.disabled = false;
        submit.textContent = "提交售后申请";
        error.textContent = errMessage(err, "售后申请没有提交成功。");
        error.hidden = false;
        error.focus?.();
      }
    });
    requestAnimationFrame(() => form.querySelector("textarea")?.focus());
  }

  function openReviewForm() {
    if (!order || busyAction) return;
    const reviewable = orderItems().filter((item) => item.reviewable === true && item.reviewed !== true);
    if (!reviewable.length) {
      alertMessage = "当前没有可评价的商品，可能已经评价过了。";
      paint();
      focusAfter(".order-alert");
      return;
    }
    const form = document.createElement("form");
    form.className = "trade-form review-form";
    form.setAttribute("data-testid", "review-form");
    form.innerHTML = `<p class="field-hint">逐件写下真实使用感受。只填写你想提交的商品即可；留空的商品可稍后再评价。每件 10-600 字。</p>
      <div class="review-items">${reviewable.map((item) => `<section class="review-block" data-review-block data-review-item-id="${escapeHtml(String(item.id || ""))}">
        <div class="review-block__head"><img src="${escapeHtml(item.cover || "")}" alt="" loading="lazy"><div><b>${escapeHtml(item.title || "商品")}</b><p class="tiny muted">${escapeHtml(item.sku_label || "")}</p></div></div>
        <div class="review-block__fields">
          <label class="field"><span class="field-label">评分</span>
            <select class="select" data-testid="rating-input" data-review-rating>
              <option value="5">5 星 · 很满意</option><option value="4">4 星 · 满意</option><option value="3">3 星 · 一般</option><option value="2">2 星 · 不太满意</option><option value="1">1 星 · 不满意</option>
            </select>
          </label>
          <label class="field"><span class="field-label">标题 <span class="tiny muted">选填</span></span><input class="input" data-review-title maxlength="40" placeholder="一句话概括"></label>
        </div>
        <label class="field"><span class="field-label">评价内容 <span class="tiny muted">10-600 字</span></span><textarea class="textarea" data-testid="review-body" data-review-body minlength="10" maxlength="600" placeholder="写写质感、尺寸、使用感受或值得说明的细节。"></textarea></label>
      </section>`).join("")}</div>
      <p class="error-inline" data-review-error role="alert" tabindex="-1" hidden></p>
      <div class="trade-form__actions"><button class="btn btn--ghost" type="button" data-cancel>取消</button><button class="btn btn--primary" type="submit" data-testid="review-submit" data-review-submit>提交评价</button></div>`;
    const modal = openModal({ title: "评价这单里的商品", subtitle: "按订单项提交；评价会绑定到已完成订单的对应商品。", body: form, wide: true });
    form.querySelector("[data-cancel]")?.addEventListener("click", () => modal.close());
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector("[data-review-submit]");
      const error = form.querySelector("[data-review-error]");
      if (submit.disabled) return;
      const reviews = [];
      for (const block of form.querySelectorAll("[data-review-block]")) {
        const body = block.querySelector("[data-review-body]");
        const text = String(body?.value || "").trim();
        if (!text) continue;
        if (text.length < 10 || text.length > 600) {
          error.textContent = "评价内容需要 10-600 个字；留空则跳过该商品。";
          error.hidden = false;
          error.focus?.();
          body?.focus();
          return;
        }
        const rating = Number(block.querySelector("[data-review-rating]")?.value || 0);
        if (!Number.isInteger(rating) || rating < 1 || rating > 5) {
          error.textContent = "请给每件提交的商品选择 1-5 星评分。";
          error.hidden = false;
          error.focus?.();
          return;
        }
        const title = String(block.querySelector("[data-review-title]")?.value || "").trim();
        if (title.length > 40) {
          error.textContent = "评价标题不能超过 40 个字。";
          error.hidden = false;
          error.focus?.();
          return;
        }
        reviews.push({ item_id: Number(block.dataset.reviewItemId), rating, title, body: text, tags: [] });
      }
      if (!reviews.length) {
        error.textContent = "请至少为一件商品写下 10 个字以上的评价。";
        error.hidden = false;
        error.focus?.();
        return;
      }
      error.hidden = true;
      submit.disabled = true;
      submit.innerHTML = '<span class="spinner" aria-hidden="true"></span> 提交中';
      try {
        await api.post(`/api/orders/${encodeURIComponent(orderNo)}/reviews`, { reviews }, { signal: ctx.signal });
        if (ctx.signal.aborted) return;
        modal.close();
        await reloadOrderAfterAction(reviews.length === 1 ? "评价已提交" : `${reviews.length} 条评价已提交`);
      } catch (err) {
        if (ctx.signal.aborted) return;
        submit.disabled = false;
        submit.textContent = "提交评价";
        error.textContent = errMessage(err, "评价没有提交成功。");
        error.hidden = false;
        error.focus?.();
      }
    });
    requestAnimationFrame(() => form.querySelector("[data-review-item-id] select")?.focus());
  }

  ctx.setTitle(`订单 ${orderNo} · OpenShop 开集`);
  if (!state.user) {
    loading = false;
    paint();
    return;
  }
  loading = true;
  paint();
  await loadOrder();
}
