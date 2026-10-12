// Shopping cart: guest-friendly, server-authoritative totals, inline feedback.
import { api, ApiError } from "../core/api.js";
import { state, updateCartCount } from "../core/store.js";
import {
  emptyState,
  escapeHtml,
  icon,
  openConfirm,
  priceHtml,
  toast,
} from "../components/index.js";

const ERROR_HINTS = {
  MIXED_BUY_MODE: "拼团商品需单独结算，请先把购物车中的普通商品处理掉，再重新加入拼团。",
  STOCK_CHANGED: "库存刚刚发生变化，请按最新库存调整数量。",
  CART_EMPTY: "购物车目前没有可结算的商品。",
  AUTH_REQUIRED: "请先登录后再继续结算。",
};

function errorText(err) {
  if (err instanceof ApiError) {
    const hint = ERROR_HINTS[err.code];
    return hint && hint !== err.message ? `${err.message} ${hint}` : err.message;
  }
  return "暂时无法读取购物车，请稍后再试。";
}

function badgeCount(cart) {
  const count = cart && cart.summary ? Number(cart.summary.item_count) : NaN;
  if (Number.isFinite(count)) updateCartCount(count);
  else if (cart && Array.isArray(cart.items)) updateCartCount(cart.items.length);
}

function moneyOrDash(cents, extraClass = "") {
  return Number.isFinite(Number(cents)) && cents !== null
    ? priceHtml(cents, { className: extraClass })
    : '<span class="muted">—</span>';
}

function itemCountText(item) {
  const qty = Number(item.quantity);
  return Number.isFinite(qty) ? `${qty} 件` : "—";
}

function quantityLimit(item) {
  const raw = Number(item.quantity_limit ?? item.offer?.stock);
  return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 99;
}

function productHref(item) {
  const slug = item.product?.slug;
  return slug ? `/p/${encodeURIComponent(slug)}` : "/search";
}

function groupHref(code) {
  return code ? `/g/${encodeURIComponent(code)}` : "/account?tab=orders";
}

export async function render(ctx) {
  ctx.setTitle("购物车 · OpenShop 开集");

  let cart = null;
  let alertMessage = "";
  let fatalError = "";
  let loading = true;
  let mutating = false;

  function headerHtml() {
    return `<header class="trade-head cart-head">
      <div>
        <p class="eyebrow">YOUR BASKET · 慢慢挑</p>
        <h1 id="cart-heading" tabindex="-1">购物车</h1>
        <p class="lede trade-head__lede">游客也能先把喜欢的收进来。数量、库存与金额，都以服务端这次返回为准。</p>
      </div>
      <a class="btn btn--ghost trade-head__back" href="/search" data-link>${icon("arrowLeft", 16)} 继续逛逛</a>
    </header>`;
  }

  function skeletonHtml() {
    return `<section class="container trade-page cart-page" data-testid="cart-root" aria-labelledby="cart-heading" aria-busy="true">
      ${headerHtml()}
      <div class="trade-layout" aria-hidden="true">
        <div class="trade-card cart-skeleton"><div class="skeleton" style="height:24px;width:36%"></div>
          ${[1, 2].map(() => `<div class="cart-skeleton__row"><div class="skeleton" style="width:94px;height:94px;border-radius:14px"></div><div style="flex:1"><div class="skeleton" style="height:18px;width:58%"></div><div class="skeleton mt-2" style="height:14px;width:38%"></div></div></div>`).join("")}
        </div>
        <div class="trade-card"><div class="skeleton" style="height:26px;width:44%"></div><div class="skeleton mt-3" style="height:16px"></div><div class="skeleton mt-2" style="height:16px"></div><div class="skeleton mt-3" style="height:52px;border-radius:999px"></div></div>
      </div>
      <p class="sr-only" role="status">正在读取购物车</p>
    </section>`;
  }

  function fatalHtml() {
    return `<section class="container trade-page cart-page" data-testid="cart-root" aria-labelledby="cart-heading">
      ${headerHtml()}
      <div class="trade-card trade-fatal">
        <p class="eyebrow">CART UNAVAILABLE</p>
        <h2>购物车这次没打开</h2>
        <p class="error-inline" role="alert">${escapeHtml(fatalError)}</p>
        <div class="cluster mt-3">
          <button class="btn btn--primary" type="button" id="cart-retry">重新读取</button>
          <a class="btn btn--ghost" href="/" data-link>回到首页</a>
        </div>
      </div>
    </section>`;
  }

  function itemHtml(item, disabled) {
    const id = String(item.id ?? "");
    const title = item.product?.title || "未命名商品";
    const groupMode = item.buy_mode === "group";
    const selected = Boolean(item.selected);
    const limit = quantityLimit(item);
    const qty = Number.isFinite(Number(item.quantity)) ? Number(item.quantity) : 1;
    const warnings = Array.isArray(item.warnings) ? item.warnings.filter(Boolean) : [];
    const shop = item.offer?.shop || {};
    const skuName = item.sku?.name || "规格以订单为准";
    const unitCents = groupMode ? item.offer?.group?.price_cents : item.offer?.unit_price_cents;
    return `<article class="cart-item ${groupMode ? "cart-item--group" : ""}" data-cart-item="${escapeHtml(id)}" data-cart-item-id="${escapeHtml(id)}" data-testid="cart-item">
      <label class="trade-check cart-item__check">
        <input type="checkbox" data-select-item="${escapeHtml(id)}" ${selected ? "checked" : ""} ${disabled ? "disabled" : ""} aria-label="选择 ${escapeHtml(title)}">
        <span class="trade-check__box" aria-hidden="true">${icon("check", 13)}</span>
      </label>
      <a class="cart-item__art" href="${escapeHtml(productHref(item))}" data-link>
        <img src="${escapeHtml(item.product?.cover || "")}" alt="${escapeHtml(item.product?.cover_alt || title)}" loading="lazy" decoding="async">
      </a>
      <div class="cart-item__body">
        <div class="cart-item__title-row">
          <a class="cart-item__title" href="${escapeHtml(productHref(item))}" data-link>${escapeHtml(title)}</a>
          ${groupMode ? `<span class="chip chip--honey">${icon("gift", 12)} 拼团价</span>` : ""}
        </div>
        <p class="cart-item__meta">${escapeHtml(item.product?.brand || "")}${item.product?.brand ? " · " : ""}${escapeHtml(shop.name || "")}</p>
        <p class="cart-item__meta">${escapeHtml(skuName)}${item.offer?.name ? ` · ${escapeHtml(item.offer.name)}` : ""}</p>
        ${groupMode && item.group_code ? `<p class="cart-item__meta"><a class="text-accent" href="${escapeHtml(groupHref(item.group_code))}" data-link>拼团邀请码 ${escapeHtml(item.group_code)} · 查看进度</a></p>` : ""}
        ${warnings.map((warning) => `<p class="cart-item__warning" role="status">${icon("alert", 14)} ${escapeHtml(warning)}</p>`).join("")}
        <p class="cart-item__unit">单价 ${moneyOrDash(unitCents, "price--small")}${item.offer?.shipping_fee_cents === 0 ? " · 包邮" : ""}</p>
      </div>
      <div class="cart-item__quantity">
        ${groupMode
          ? `<span class="cart-fixed-qty">1 件<span class="tiny muted">拼团固定</span></span>`
          : `<div class="quantity trade-qty" tabindex="-1" data-qty-wrap="${escapeHtml(id)}" role="group" aria-label="${escapeHtml(title)} 数量">
              <button type="button" data-qty-step="-1" data-id="${escapeHtml(id)}" aria-label="减少 ${escapeHtml(title)} 数量" ${disabled || qty <= 1 ? "disabled" : ""}>${icon("minus", 15)}</button>
              <input type="number" class="cart-qty__input" data-testid="cart-qty-input" data-cart-qty-input data-id="${escapeHtml(id)}" value="${qty}" min="1" max="${limit}" step="1" inputmode="numeric" aria-label="${escapeHtml(title)} 数量，1 到 ${limit} 件">
              <button type="button" data-qty-step="1" data-id="${escapeHtml(id)}" aria-label="增加 ${escapeHtml(title)} 数量" ${disabled || qty >= limit ? "disabled" : ""}>${icon("plus", 15)}</button>
            </div>`}
        <span class="tiny muted">${groupMode ? "拼团商品单独结算" : `最多 ${limit} 件`}</span>
      </div>
      <div class="cart-item__price">
        ${moneyOrDash(item.line_price_cents)}
        <span class="tiny muted">${itemCountText(item)} · 服务端计价</span>
      </div>
      <button class="icon-btn cart-item__remove" type="button" data-remove-item="${escapeHtml(id)}" data-item-title="${escapeHtml(title)}" aria-label="从购物车删除 ${escapeHtml(title)}" ${disabled ? "disabled" : ""}>${icon("trash", 18)}</button>
    </article>`;
  }

  function shopGroupHtml(shop, items, disabled) {
    const shopMeta = shop.shop || {};
    const ids = Array.isArray(shop.item_ids) ? shop.item_ids.map(String) : [];
    const groupItems = items.filter((item) => ids.includes(String(item.id)));
    if (!groupItems.length) return "";
    return `<section class="cart-shop" aria-label="${escapeHtml(shopMeta.name || "店铺")}的商品">
      <header class="cart-shop__head">
        <div class="cart-shop__name">${icon("store", 16)} <b>${escapeHtml(shopMeta.name || "店铺")}</b>
          ${shopMeta.verified ? '<span class="badge badge--jade">已验证</span>' : ""}
        </div>
        <div class="cart-shop__sub"><span>本店已选 ${moneyOrDash(shop.selected_subtotal_cents, "price--small")}</span><span>${escapeHtml(shop.delivery_note || "")}</span></div>
      </header>
      <div class="cart-shop__items">${groupItems.map((item) => itemHtml(item, disabled)).join("")}</div>
    </section>`;
  }

  function summaryHtml(items, disabled) {
    const summary = cart.summary || {};
    const shops = Array.isArray(cart.shops) ? cart.shops : [];
    const groupedIds = new Set();
    shops.forEach((shop) => (Array.isArray(shop.item_ids) ? shop.item_ids : []).forEach((id) => groupedIds.add(String(id))));
    const ungrouped = items.filter((item) => !groupedIds.has(String(item.id)));
    const selectedCount = Number(summary.selected_count || 0);
    const itemTotal = Number(summary.item_count || items.length);
    const hasSelection = selectedCount > 0;
    const notices = Array.isArray(cart.notices) ? cart.notices.filter(Boolean) : [];
    return `<div class="trade-layout cart-layout">
      <div class="cart-main">
        <div class="trade-card cart-toolbar">
          <label class="trade-check cart-toolbar__all">
            <input id="cart-select-all" data-testid="cart-select-all" type="checkbox" ${summary.all_selected ? "checked" : ""} ${disabled || !items.length ? "disabled" : ""}>
            <span class="trade-check__box" aria-hidden="true">${icon("check", 13)}</span>
            <span>全选</span>
          </label>
          <p class="cart-toolbar__count" aria-live="polite">已选 <b>${selectedCount}</b> / ${Number.isFinite(itemTotal) ? itemTotal : items.length} 件</p>
          <p class="tiny muted cart-toolbar__hint">取消选择不会删除商品</p>
        </div>
        ${shops.map((shop) => shopGroupHtml(shop, items, disabled)).join("")}
        ${ungrouped.length ? `<section class="cart-shop" aria-label="其他商品"><header class="cart-shop__head"><div class="cart-shop__name">${icon("store", 16)} <b>其他商品</b></div></header><div class="cart-shop__items">${ungrouped.map((item) => itemHtml(item, disabled)).join("")}</div></section>` : ""}
        <div class="cart-trust trade-card">
          ${icon("shield", 18)}
          <div><b>先把规则说清楚</b><p>价格、库存与可结算数量由服务端复核；提交订单后才会占用库存。演示环境不进行真实扣款。</p></div>
          <a href="/help" data-link class="link-arrow">配送与售后 ${icon("arrowRight", 14)}</a>
        </div>
      </div>
      <aside class="cart-aside" aria-label="购物车结算预览">
        <div class="trade-summary cart-summary">
          <div class="trade-summary__head">
            <div><p class="eyebrow">SUMMARY</p><h2 id="cart-summary-title">结算预览</h2></div>
            ${summary.has_group ? '<span class="chip chip--honey">含拼团</span>' : ""}
          </div>
          <dl class="amount-list">
            <div><dt>商品小计</dt><dd>${moneyOrDash(summary.subtotal_cents)}</dd></div>
            ${Number(summary.savings_cents) > 0 ? `<div class="amount-list__save"><dt>已省</dt><dd>${moneyOrDash(summary.savings_cents)}</dd></div>` : ""}
            <div><dt>运费</dt><dd>${summary.shipping_cents === 0 ? "包邮" : moneyOrDash(summary.shipping_cents)}</dd></div>
            <div><dt>优惠</dt><dd>${Number(summary.discount_cents) > 0 ? `− ${moneyOrDash(summary.discount_cents)}` : "结算页计算"}</dd></div>
          </dl>
          <div class="trade-total">
            <span>合计 <span class="tiny muted">服务端</span></span>
            <strong>${moneyOrDash(summary.total_cents, "price--lg")}</strong>
          </div>
          ${!state.user ? `<p class="cart-guest-note">${icon("user", 15)} 游客购物车会保留在本浏览器；结算时可用一键买家登录，登录后自动合并。</p>` : ""}
          ${summary.has_group ? '<p class="cart-group-note">拼团商品需单独结算；成团进度以加入的团为准，不会预先计入人数。</p>' : ""}
          <button class="btn btn--primary btn--large btn--block cart-checkout" type="button" id="cart-checkout" data-testid="cart-checkout-link" ${!hasSelection || disabled ? "disabled" : ""} aria-describedby="cart-checkout-hint">
            ${hasSelection ? `去结算${state.user ? "" : " · 登录"} ${icon("arrowRight", 17)}` : "请先勾选商品"}
          </button>
          <p id="cart-checkout-hint" class="tiny muted cart-summary__hint">${hasSelection ? "优惠券和配送方式将在结算页向服务端重新报价。" : "勾选想买的商品后即可前往结算。"}</p>
          ${notices.length ? `<ul class="cart-notices">${notices.map((notice) => `<li>${escapeHtml(notice)}</li>`).join("")}</ul>` : ""}
        </div>
      </aside>
    </div>`;
  }

  function emptyHtml() {
    return `<div class="trade-card cart-empty" data-testid="cart-empty">
      ${emptyState({ title: "购物车还是空的", message: "先去集市里转一圈。看中的商品可以随时放回来，结算前都还能改。", actionLabel: "去发现好物", actionHref: "/search", iconName: "cart" })}
      <div class="cart-empty__links"><a class="link-arrow" href="/" data-link>回到首页 ${icon("arrowRight", 14)}</a><a class="link-arrow" href="/help" data-link>配送与售后 ${icon("arrowRight", 14)}</a></div>
    </div>`;
  }

  function contentHtml() {
    const items = Array.isArray(cart?.items) ? cart.items : [];
    return `<section class="container trade-page cart-page" data-testid="cart-root" aria-labelledby="cart-heading" aria-busy="false">
      ${headerHtml()}
      ${alertMessage ? `<div class="error-inline trade-alert" role="alert" tabindex="-1">${icon("alert", 16)} <span>${escapeHtml(alertMessage)}</span></div>` : ""}
      ${items.length ? summaryHtml(items, mutating) : emptyHtml()}
    </section>`;
  }

  function paint() {
    if (ctx.signal.aborted) return;
    if (loading) {
      ctx.root.innerHTML = skeletonHtml();
      return;
    }
    if (fatalError) {
      ctx.root.innerHTML = fatalHtml();
      ctx.root.querySelector("#cart-retry")?.addEventListener("click", () => {
        loading = true;
        fatalError = "";
        paint();
        void loadCart();
      });
      return;
    }
    ctx.root.innerHTML = contentHtml();
    bindContent();
    const all = ctx.root.querySelector("#cart-select-all");
    if (all && all.type === "checkbox") {
      const items = Array.isArray(cart?.items) ? cart.items : [];
      const selected = items.filter((item) => item.selected).length;
      all.indeterminate = selected > 0 && !all.checked;
    }
    if (alertMessage) {
      const alert = ctx.root.querySelector(".trade-alert");
      if (alert) requestAnimationFrame(() => alert.focus({ preventScroll: false }));
    }
  }

  function setBusyUi(trigger, busyLabel) {
    const page = ctx.root.querySelector(".cart-page");
    page?.setAttribute("aria-busy", "true");
    ctx.root.querySelectorAll(".cart-page button, .cart-page input").forEach((control) => {
      if (!control.disabled) {
        control.dataset.tradeDisabled = "1";
        control.disabled = true;
      }
    });
    if (trigger && trigger.tagName === "BUTTON") {
      trigger.dataset.tradeBusy = "1";
      trigger.innerHTML = `<span class="spinner" aria-hidden="true"></span> ${escapeHtml(busyLabel)}`;
    }
  }

  function focusSelector(selector) {
    requestAnimationFrame(() => {
      if (ctx.signal.aborted) return;
      const target = ctx.root.querySelector(selector);
      if (!target) return;
      if (target.disabled) {
        const fallback = target.closest(".trade-qty") || ctx.root.querySelector("#cart-heading");
        fallback?.focus();
      } else {
        target.focus();
      }
    });
  }

  async function mutate({ request, trigger, busyLabel, focusSelector: focusTarget, successMessage }) {
    if (mutating) return;
    mutating = true;
    alertMessage = "";
    setBusyUi(trigger, busyLabel);
    try {
      const next = await request();
      if (ctx.signal.aborted) return;
      if (!next || !Array.isArray(next.items) || !next.summary) {
        throw new Error("购物车响应缺少必要字段");
      }
      cart = next;
      badgeCount(cart);
      alertMessage = "";
      if (successMessage) toast(successMessage);
    } catch (err) {
      if (ctx.signal.aborted) return;
      if (err?.name === "AbortError") return;
      alertMessage = errorText(err);
    } finally {
      if (!ctx.signal.aborted) {
        mutating = false;
        paint();
        if (alertMessage) {
          focusSelector(".trade-alert");
        } else if (focusTarget) {
          focusSelector(focusTarget);
        }
      }
    }
  }

  async function updateQuantity(item, requested) {
    const current = Number(item.quantity) || 1;
    const limit = quantityLimit(item);
    const quantity = Math.min(limit, Math.max(1, requested));
    if (quantity === current) {
      const input = ctx.root.querySelector(`[data-cart-qty-input][data-id="${String(item.id)}"]`);
      if (input) input.value = String(current);
      return;
    }
    const id = String(item.id);
    const trigger = ctx.root.querySelector(`[data-qty-step="${quantity > current ? 1 : -1}"][data-id="${id}"]`);
    await mutate({
      request: () => api.patch(`/api/cart/items/${encodeURIComponent(id)}`, { quantity }, { signal: ctx.signal }),
      trigger,
      busyLabel: "更新中",
      focusSelector: `[data-cart-qty-input][data-id="${id}"]`,
      successMessage: "数量已更新",
    });
  }

  async function removeItem(id, title, trigger) {
    const confirmed = await openConfirm({
      title: "把这件商品移出购物车？",
      message: `“${title}”会从当前购物车移除；之后可以重新加入。`,
      confirmLabel: "移出购物车",
      cancelLabel: "先留着",
    });
    if (!confirmed || ctx.signal.aborted) return;
    await mutate({
      request: () => api.del(`/api/cart/items/${encodeURIComponent(id)}`, { signal: ctx.signal }),
      trigger,
      busyLabel: "移除中",
      focusSelector: "#cart-heading",
      successMessage: "已移出购物车",
    });
  }

  function bindContent() {
    const items = Array.isArray(cart?.items) ? cart.items : [];
    ctx.root.querySelector("#cart-select-all")?.addEventListener("change", (event) => {
      if (mutating) return;
      const selected = Boolean(event.target.checked);
      void mutate({
        request: () => api.post("/api/cart/selection", { selected }, { signal: ctx.signal }),
        trigger: null,
        busyLabel: "更新选择",
        focusSelector: "#cart-select-all",
        successMessage: selected ? "已选中全部可结算商品" : "已取消全选",
      });
    });
    ctx.root.querySelectorAll("[data-select-item]").forEach((input) => {
      input.addEventListener("change", () => {
        if (mutating) return;
        const id = input.dataset.selectItem;
        const selected = Boolean(input.checked);
        void mutate({
          request: () => api.patch(`/api/cart/items/${encodeURIComponent(id)}`, { selected }, { signal: ctx.signal }),
          trigger: null,
          busyLabel: "更新选择",
          focusSelector: `[data-select-item="${id}"]`,
        });
      });
    });
    ctx.root.querySelectorAll("[data-qty-step]").forEach((button) => {
      button.addEventListener("click", () => {
        if (mutating) return;
        const id = button.dataset.id;
        const item = items.find((entry) => String(entry.id) === String(id));
        if (!item) return;
        void updateQuantity(item, (Number(item.quantity) || 1) + Number(button.dataset.qtyStep));
      });
    });
    ctx.root.querySelectorAll("[data-cart-qty-input]").forEach((input) => {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          input.blur();
        }
      });
      input.addEventListener("change", () => {
        if (mutating) return;
        const id = input.dataset.id;
        const item = items.find((entry) => String(entry.id) === String(id));
        if (!item) return;
        const wanted = Number.parseInt(input.value, 10);
        if (!Number.isFinite(wanted)) {
          input.value = String(Number(item.quantity) || 1);
          return;
        }
        void updateQuantity(item, wanted);
      });
    });
    ctx.root.querySelectorAll("[data-remove-item]").forEach((button) => {
      button.addEventListener("click", () => {
        if (mutating) return;
        void removeItem(button.dataset.removeItem, button.dataset.itemTitle || "这件商品", button);
      });
    });
    ctx.root.querySelector("#cart-checkout")?.addEventListener("click", () => {
      if (mutating) return;
      ctx.navigate("/checkout");
    });
  }

  async function loadCart() {
    try {
      const result = await api.get("/api/cart", { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      if (!result || !Array.isArray(result.items) || !result.summary) {
        throw new Error("购物车响应缺少必要字段");
      }
      cart = result;
      badgeCount(cart);
      loading = false;
      fatalError = "";
      paint();
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError") return;
      loading = false;
      fatalError = errorText(err);
      paint();
    }
  }

  ctx.root.innerHTML = skeletonHtml();
  await loadCart();
}
