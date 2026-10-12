// Checkout: server quote on every change, one idempotent order, explicit demo pay.
import { api, ApiError } from "../core/api.js";
import { refreshSession, state, updateCartCount } from "../core/store.js";
import {
  escapeHtml,
  icon,
  money,
  openAuth,
  openModal,
  priceHtml,
  toast,
} from "../components/index.js";

const ERROR_HINTS = {
  STOCK_CHANGED: "库存刚刚变化，请按最新报价重新确认。",
  COUPON_INVALID: "这张优惠券这次未能使用，请重新选择。",
  CART_EMPTY: "已选商品为空，可能已在其他页面下单或被移出购物车。",
  AUTH_REQUIRED: "登录状态已失效，请重新登录后再结算。",
  GROUP_FULL: "这个团已经满员，不能继续加入。",
  GROUP_UNAVAILABLE: "这个团已经失效或不接受新成员。",
  MIXED_BUY_MODE: "购物车中存在不兼容的商品类型，请先处理购物车。",
  IDEMPOTENCY_CONFLICT: "本次提交标识已被使用，系统已阻止重复提交。请刷新页面后重新确认。",
};

function errMessage(err, fallback = "操作没有完成，请稍后再试。") {
  if (err instanceof ApiError) {
    const hint = ERROR_HINTS[err.code];
    return hint && hint !== err.message ? `${err.message} ${hint}` : err.message;
  }
  return fallback;
}

function sentenceEnd(value) {
  const text = String(value || "").trim();
  return text && !/[。！？.!?]$/.test(text) ? text + "。" : text;
}

function amountHtml(cents, className = "") {
  return Number.isFinite(Number(cents)) && cents !== null
    ? priceHtml(cents, { className })
    : '<span class="muted">—</span>';
}

function dateText(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function newIdempotencyKey() {
  if (globalThis.crypto && typeof globalThis.crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  return `os${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`.slice(0, 32).padEnd(32, "0");
}

function addressLine(address) {
  if (!address) return "";
  return [address.province, address.city, address.district, address.detail].filter(Boolean).join(" ");
}

function selectedItems(cart) {
  return (Array.isArray(cart?.items) ? cart.items : []).filter((item) => item.selected);
}

export async function render(ctx) {
  ctx.setTitle("结算 · OpenShop 开集");

  let cart = null;
  let addresses = [];
  let defaultAddressId = null;
  let selectedAddressId = null;
  let addressesError = "";
  let coupons = { available: [], mine: [] };
  let couponsError = "";
  let selectedCoupon = "";
  let selectedShipping = "standard";
  let quote = null;
  let quoteError = "";
  let alertMessage = "";
  let fatalError = "";
  let noSelection = false;
  let booting = Boolean(state.user);
  let quoting = false;
  let preparing = false;
  let submitting = false;
  let submitFlow = false;
  let attempt = null;
  let createdOrder = null;
  let outcome = null;
  let note = "";
  let quoteSeq = 0;
  let addressSeq = 0;

  const busy = () => preparing || submitting;

  function cartItemsList() {
    return selectedItems(cart);
  }

  function currentAddress() {
    return addresses.find((address) => String(address.id) === String(selectedAddressId)) || null;
  }

  function mineCoupons() {
    return (Array.isArray(coupons?.mine) ? coupons.mine : []).filter((coupon) => coupon.status === "AVAILABLE");
  }

  function shippingOptions() {
    return Array.isArray(quote?.shipping_options) ? quote.shipping_options : [];
  }

  function claimableCount() {
    return (Array.isArray(coupons?.available) ? coupons.available : []).filter((coupon) => coupon.claimable === true).length;
  }

  function chosenShipping() {
    const options = shippingOptions();
    return options.find((option) => option.code === selectedShipping) || quote?.shipping_method || null;
  }

  function canSubmit() {
    return Boolean(
      quote &&
      currentAddress() &&
      !quoteError &&
      !quote?.coupon_error &&
      !quoting &&
      !preparing &&
      !submitting &&
      !fatalError &&
      !noSelection
    );
  }

  function focusAfter(selector) {
    requestAnimationFrame(() => {
      if (ctx.signal.aborted) return;
      const target = ctx.root.querySelector(selector);
      if (target && typeof target.focus === "function") target.focus({ preventScroll: true });
    });
  }

  function headerHtml() {
    return `<header class="trade-head checkout-head">
      <div>
        <p class="eyebrow">CHECKOUT · 确认这单</p>
        <h1 id="checkout-heading" tabindex="-1">结算</h1>
        <p class="lede trade-head__lede">配送方式、优惠券或商品选择变化时，都会重新向服务端报价；收货地址进入订单履约快照。最终金额以创建订单时的服务端结果为准。</p>
      </div>
      <a class="btn btn--ghost trade-head__back" href="/cart" data-link>${icon("arrowLeft", 16)} 回到购物车</a>
    </header>`;
  }

  function stepsHtml() {
    return `<ol class="trade-steps" aria-label="结算进度">
      <li class="is-done"><span>01</span>购物车</li>
      <li class="is-current" aria-current="step"><span>02</span>确认订单</li>
      <li><span>03</span>模拟支付</li>
    </ol>`;
  }

  function skeletonHtml() {
    return `<section class="container trade-page checkout-page" data-testid="checkout-root" aria-labelledby="checkout-heading" aria-busy="true">
      ${headerHtml()}
      ${stepsHtml()}
      <div class="trade-layout checkout-layout" aria-hidden="true">
        <div class="stack">
          <div class="trade-card"><div class="skeleton" style="height:28px;width:32%"></div><div class="skeleton mt-3" style="height:74px;border-radius:14px"></div><div class="skeleton mt-2" style="height:74px;border-radius:14px"></div></div>
          <div class="trade-card"><div class="skeleton" style="height:22px;width:26%"></div><div class="skeleton mt-3" style="height:110px"></div></div>
        </div>
        <div class="trade-card"><div class="skeleton" style="height:28px;width:42%"></div><div class="skeleton mt-3" style="height:16px"></div><div class="skeleton mt-2" style="height:16px"></div><div class="skeleton mt-2" style="height:16px"></div><div class="skeleton mt-3" style="height:54px;border-radius:999px"></div></div>
      </div>
      <p class="sr-only" role="status">正在准备结算</p>
    </section>`;
  }

  function guestHtml() {
    return `<section class="container trade-page checkout-page" data-testid="checkout-root" aria-labelledby="checkout-heading">
      ${headerHtml()}
      ${stepsHtml()}
      <div class="trade-lock trade-card">
        <div class="trade-lock__icon">${icon("user", 30)}</div>
        <span class="eyebrow eyebrow--jade">ONE TAP TO CONTINUE</span>
        <h2>结算前，先认一下你</h2>
        <p>选好的商品还在购物车。使用一键买家演示或登录/注册后，地址、优惠与订单才有归处；这是演示环境，不进行真实支付。</p>
        <div class="trade-lock__actions">
          <button class="btn btn--primary btn--large" type="button" data-login>${icon("user", 17)} 一键买家登录并继续</button>
          <a class="btn btn--ghost" href="/cart" data-link>先回购物车看看</a>
        </div>
        <p class="tiny muted">登录后游客购物车会自动合并；不会因为登录丢掉已选商品。</p>
      </div>
    </section>`;
  }

  function fatalHtml() {
    return `<section class="container trade-page checkout-page" data-testid="checkout-root" aria-labelledby="checkout-heading">
      ${headerHtml()}
      ${stepsHtml()}
      <div class="trade-card trade-fatal">
        <p class="eyebrow">CHECKOUT UNAVAILABLE</p>
        <h2>结算信息没读到</h2>
        <p class="error-inline" role="alert">${escapeHtml(fatalError)}</p>
        <div class="cluster mt-3"><button class="btn btn--primary" type="button" data-retry-init>重新读取</button><a class="btn btn--ghost" href="/cart" data-link>回购物车</a></div>
      </div>
    </section>`;
  }

  function emptyHtml() {
    return `<section class="container trade-page checkout-page" data-testid="checkout-root" aria-labelledby="checkout-heading">
      ${headerHtml()}
      ${stepsHtml()}
      <div class="trade-card checkout-empty">
        <div class="trade-lock__icon">${icon("cart", 28)}</div>
        <h2>还没有勾选要结算的商品</h2>
        <p>购物车里尚未选中任何商品。先去勾选想买的，再来确认地址和优惠。</p>
        <div class="cluster mt-3"><a class="btn btn--primary" href="/cart" data-link>回到购物车勾选</a><a class="btn btn--ghost" href="/search" data-link>继续逛逛</a></div>
      </div>
    </section>`;
  }

  function addressChoicesHtml() {
    const items = Array.isArray(addresses) ? addresses : [];
    if (addressesError) {
      return `<div class="error-inline" role="alert">${escapeHtml(addressesError)} <button class="btn btn--small btn--ghost" type="button" data-retry-addresses>重新读取</button></div>`;
    }
    if (!items.length) {
      return `<div class="address-empty">
        <p>还没有收货地址。演示地址请使用 11 位大陆手机号，填写后可直接用于本次结算。</p>
        <button class="btn btn--primary btn--small" type="button" data-testid="address-add-empty" data-add-address>+ 新增收货地址</button>
      </div>`;
    }
    return `<div class="address-list" role="radiogroup" aria-label="选择收货地址">${items.map((address) => {
      const checked = String(address.id) === String(selectedAddressId);
      const id = escapeHtml(String(address.id));
      return `<label class="trade-choice address-choice ${checked ? "is-selected" : ""}" data-testid="address-option" data-address-id="${id}">
        <input type="radio" name="checkout-address" value="${id}" data-address-id="${id}" ${checked ? "checked" : ""} ${busy() ? "disabled" : ""}>
        <span class="trade-choice__mark" aria-hidden="true"></span>
        <span class="address-choice__body">
          <span class="address-choice__top"><b>${escapeHtml(address.receiver || "收件人")}</b><span>${escapeHtml(address.phone || "")}</span>${address.is_default ? '<span class="badge badge--jade">默认</span>' : ""}${address.label ? `<span class="chip">${escapeHtml(address.label)}</span>` : ""}</span>
          <span class="address-choice__line">${escapeHtml(addressLine(address))}</span>
        </span>
      </label>`;
    }).join("")}</div>`;
  }

  function couponPanelHtml() {
    const list = mineCoupons();
    const options = list.map((coupon) => `<option value="${escapeHtml(coupon.code || "")}" ${String(coupon.code) === String(selectedCoupon) ? "selected" : ""}>${escapeHtml(coupon.title || coupon.code || "优惠券")} · 减 ${money(coupon.discount_cents)}（满 ${money(coupon.threshold_cents)}）</option>`).join("");
    const available = claimableCount();
    const claimLink = available > 0
      ? `<a class="link-arrow" href="/account?tab=coupons" data-link>领券中心有 ${available} 张可领 ${icon("arrowRight", 14)}</a>`
      : `<a class="link-arrow" href="/account?tab=coupons" data-link>去领券中心 ${icon("arrowRight", 14)}</a>`;
    return `<div class="coupon-picker">
      <div class="field coupon-picker__field">
        <label class="field-label" for="checkout-coupon">已领取的优惠券</label>
        <select class="select" id="checkout-coupon" name="coupon_code" data-testid="coupon-select" ${busy() || quoting ? "disabled" : ""}>
          <option value="">不使用优惠券</option>
          ${options}
        </select>
      </div>
      <div class="coupon-picker__side">
        ${couponsError ? `<p class="field-hint text-accent" role="alert">优惠券读取失败：${escapeHtml(couponsError)} <button class="btn btn--small btn--quiet" type="button" data-retry-coupons>重试</button></p>` : ""}
        ${!couponsError && !list.length ? '<p class="field-hint">还没有可用优惠券。可以先到领券中心看看。</p>' : ""}
        ${claimLink}
        ${quote?.coupon ? `<p class="coupon-applied">${icon("check", 14)} 服务端已应用 ${escapeHtml(quote.coupon.title || quote.coupon.code)}：− ${money(quote.coupon.discount_cents ?? quote.discount_cents)}</p>` : ""}
        ${quote?.coupon_error ? `<p class="coupon-error" role="alert">${escapeHtml(quote.coupon_error)}</p>` : ""}
        ${selectedCoupon && !quote?.coupon && !quote?.coupon_error && !quoting ? '<p class="field-hint">服务端暂未采用这张券，请刷新报价或重新选择。</p>' : ""}
      </div>
    </div>`;
  }

  function shippingPanelHtml() {
    const options = shippingOptions();
    if (!options.length) {
      return `<p class="muted small">拿到有效报价后，这里会显示服务端返回的配送方式。</p>`;
    }
    return `<div class="shipping-options" role="radiogroup" aria-label="选择配送方式">${options.map((option) => `<label class="trade-choice shipping-choice ${option.code === selectedShipping ? "is-selected" : ""}" data-testid="shipping-option" data-shipping-option-code="${escapeHtml(option.code)}">
      <input type="radio" name="checkout-shipping" value="${escapeHtml(option.code)}" data-shipping-code="${escapeHtml(option.code)}" ${option.code === selectedShipping ? "checked" : ""} ${busy() || quoting ? "disabled" : ""}>
      <span class="trade-choice__mark" aria-hidden="true"></span>
      <span class="shipping-choice__body"><b>${escapeHtml(option.label || "配送")}</b><span>${escapeHtml(option.detail || "")}</span></span>
      <span class="shipping-choice__fee">${Number(option.fee_cents) === 0 ? "包邮" : money(option.fee_cents)}</span>
    </label>`).join("")}</div>
    ${quote?.has_group ? '<p class="shipping-note">包含拼团商品，服务端只提供标准配送。</p>' : ""}`;
  }

  function itemRowsHtml() {
    const items = cartItemsList();
    if (!items.length) return `<p class="muted small">没有已选商品。</p>`;
    return `<ul class="checkout-lines">${items.map((item) => {
      const title = item.product?.title || "商品";
      const seller = item.offer?.shop?.name || "";
      const groupMode = item.buy_mode === "group";
      const unit = groupMode ? item.offer?.group?.price_cents : item.offer?.unit_price_cents;
      return `<li class="checkout-line">
        <img src="${escapeHtml(item.product?.cover || "")}" alt="${escapeHtml(item.product?.cover_alt || title)}" loading="lazy" decoding="async">
        <div class="checkout-line__body">
          <div class="checkout-line__title">${escapeHtml(title)}${groupMode ? '<span class="chip chip--honey">拼团</span>' : ""}</div>
          <p>${escapeHtml(item.sku?.name || "规格以订单为准")} · ${escapeHtml(seller)}</p>
          ${groupMode && item.group_code ? `<p class="tiny muted">邀请码 ${escapeHtml(item.group_code)} · 付款后才计入成团进度</p>` : ""}
        </div>
        <div class="checkout-line__qty">× ${Number(item.quantity) || 1}</div>
        <div class="checkout-line__price">${amountHtml(unit, "price--small")}</div>
      </li>`;
    }).join("")}</ul>`;
  }

  function summaryHtml() {
    if (quoteError) {
      return `<div class="trade-summary quote-summary">
        <div class="trade-summary__head"><div><p class="eyebrow">QUOTE</p><h2 id="checkout-summary-title">服务端报价</h2></div></div>
        <div class="error-inline quote-error" id="quote-error" role="alert" tabindex="-1">${escapeHtml(quoteError)}</div>
        <button class="btn btn--ghost btn--block mt-2" type="button" data-retry-quote>重新获取报价</button>
        <p class="tiny muted mt-2">取得有效报价前不会提交订单。</p>
      </div>`;
    }
    if (!quote) {
      return `<div class="trade-summary quote-summary" aria-busy="true">
        <div class="trade-summary__head"><div><p class="eyebrow">QUOTE</p><h2 id="checkout-summary-title">服务端报价</h2></div></div>
        <div class="quote-loading"><span class="spinner" aria-hidden="true"></span>正在向服务端确认金额…</div>
        <div class="skeleton mt-3" style="height:16px"></div><div class="skeleton mt-2" style="height:16px;width:72%"></div>
      </div>`;
    }
    const address = currentAddress();
    return `<div class="trade-summary quote-summary" aria-busy="${quoting ? "true" : "false"}">
      <div class="trade-summary__head">
        <div><p class="eyebrow">QUOTE</p><h2 id="checkout-summary-title">服务端报价</h2></div>
        ${quote.has_group ? '<span class="chip chip--honey">拼团</span>' : ""}
      </div>
      <dl class="amount-list">
        <div><dt>商品小计 <span class="tiny muted">${Number(quote.selected_count) || 0} 件已选</span></dt><dd>${amountHtml(quote.subtotal_cents)}</dd></div>
        <div><dt>运费 · ${escapeHtml(chosenShipping()?.label || "配送")}</dt><dd>${Number(quote.shipping_cents) === 0 ? "包邮" : amountHtml(quote.shipping_cents)}</dd></div>
        <div><dt>优惠</dt><dd>${Number(quote.discount_cents) > 0 ? `− ${amountHtml(quote.discount_cents)}` : "—"}</dd></div>
        ${Number(quote.savings_cents) > 0 ? `<div class="amount-list__save"><dt>相对市场价已省</dt><dd>${amountHtml(quote.savings_cents)}</dd></div>` : ""}
      </dl>
      <div class="trade-total"><span>应付总额 <span class="tiny muted">服务端报价</span></span><strong data-testid="quote-total" data-cents="${escapeHtml(String(quote.total_cents ?? ""))}">${amountHtml(quote.total_cents, "price--lg")}</strong></div>
      <div class="quote-meta">
        <span>${address ? `寄往 ${escapeHtml(address.receiver || "")} · ${escapeHtml(address.district || address.city || "")}` : "尚未选择地址"}</span>
        <span>${quoting ? "正在更新报价…" : "报价已是最新"}</span>
      </div>
      <div class="demo-pay-block">
        <span class="demo-pay-block__stamp">DEMO</span>
        <div><b>模拟支付</b><p>这里会先创建待付款订单，下一步再由你点击模拟支付；不会真实扣款，也不会自动跳到成功。</p></div>
        <span class="demo-pay-block__status" data-testid="payment-status" data-payment-state="not_started" role="status">模拟支付未开始</span>
      </div>
      <button class="btn btn--primary btn--large btn--block" id="place-order" data-testid="create-order" type="button" ${canSubmit() ? "" : "disabled"} ${quoting || preparing || submitting ? 'aria-busy="true"' : ""}>
        ${submitting ? '<span class="spinner" aria-hidden="true"></span> 正在创建订单' : preparing ? '<span class="spinner" aria-hidden="true"></span> 正在复核报价' : quoting ? '<span class="spinner" aria-hidden="true"></span> 正在更新报价' : "创建订单，下一步模拟支付"}
      </button>
      <p class="tiny muted quote-hint">${!address ? "请先选择收货地址。" : quote?.coupon_error ? "请先重新选择可用优惠券。" : "创建订单时会由服务端再次重算价格、库存与优惠。"}</p>
      <p class="tiny muted">模拟支付失败时订单会保留，可到订单详情重新处理；不会自动重复创建订单。</p>
    </div>`;
  }

  function createdHtml() {
    const order = createdOrder || {};
    const orderNo = order.order_no || "";
    const statusLabel = order.status_label || "待付款";
    const payAllowed = order.actions?.pay === true || (order.actions == null && order.status === "PENDING_PAYMENT");
    const group = order.group;
    const total = Number.isFinite(Number(order.total_cents)) ? order.total_cents : null;
    return `<section class="container trade-page checkout-page checkout-created" data-testid="checkout-root" aria-labelledby="checkout-created-title">
      <div class="checkout-created__head">
        <p class="eyebrow">ORDER CREATED · 下一步</p>
        <h1 id="checkout-created-title" tabindex="-1">订单已创建</h1>
        <p class="lede">订单已经由服务端生成。下一步由你亲自点击“模拟支付”；在这一步之前还没有发生支付。</p>
      </div>
      <ol class="trade-steps" aria-label="结算进度">
        <li class="is-done"><span>01</span>购物车</li>
        <li class="is-done"><span>02</span>确认订单</li>
        <li class="is-current" aria-current="step"><span>03</span>模拟支付</li>
      </ol>
      <div class="trade-layout checkout-layout">
        <div class="trade-card checkout-created__card">
          <div class="checkout-created__status">
            <span class="outcome-seal outcome-seal--small" aria-hidden="true">${icon("card", 22)}</span>
            <div>
              <span class="payment-status payment-status--pending" data-testid="payment-status" data-payment-state="pending" role="status">待模拟支付</span>
              <p>服务端订单状态：${escapeHtml(statusLabel)}</p>
            </div>
          </div>
          <dl class="outcome-facts checkout-created__facts">
            ${orderNo ? `<div><span>订单号</span><b data-order-no="${escapeHtml(orderNo)}">${escapeHtml(orderNo)}</b></div>` : ""}
            ${total !== null ? `<div><span>订单金额</span><b>${amountHtml(total)}</b></div>` : ""}
            ${group ? `<div><span>拼团</span><b>${escapeHtml(group.code || "")} · ${Number(group.joined_count) || 0}/${Number(group.required_size) || 0}</b></div>` : ""}
            ${currentAddress() ? `<div><span>收货地址</span><b>${escapeHtml(currentAddress().receiver || "")} · ${escapeHtml(addressLine(currentAddress()))}</b></div>` : ""}
          </dl>
          <div class="demo-pay-block demo-pay-block--prominent">
            <span class="demo-pay-block__stamp">DEMO</span>
            <div><b>模拟支付，不产生真实扣款</b><p>点击下方按钮会调用 demo 支付接口。失败时本页会明确说明，不会跳成成功。</p></div>
          </div>
          ${payAllowed ? `<button class="btn btn--primary btn--large btn--block" type="button" data-testid="pay-demo" id="pay-demo" ${submitting ? "disabled aria-busy=\"true\"" : ""}>${submitting ? '<span class="spinner" aria-hidden="true"></span> 正在模拟支付' : `模拟支付 ${total !== null ? money(total) : ""}（DEMO）`}</button>` : `<div class="notice">当前服务端状态不允许在此支付。请打开订单详情查看可执行操作。</div>`}
          <div class="cluster mt-2 checkout-created__links">
            ${orderNo ? `<a class="btn btn--ghost" href="/orders/${encodeURIComponent(orderNo)}" data-link>查看订单详情</a>` : ""}
            <a class="link-arrow" href="/account?tab=orders" data-link>稍后支付，先去我的订单 ${icon("arrowRight", 14)}</a>
          </div>
          <p class="tiny muted">订单超时或库存变化均由服务端裁决；本页显示的金额来自订单响应。</p>
        </div>
        <aside class="checkout-aside">
          <div class="trade-summary">
            <div class="trade-summary__head"><div><p class="eyebrow">NEXT</p><h2>这一步会发生什么</h2></div></div>
            <ol class="trade-next-list">
              <li><b>调用 demo 支付接口</b><span>只接受 payment_method=demo，不会跳转任何真实支付渠道。</span></li>
              <li><b>返回服务端订单状态</b><span>成功才显示成功；失败会保留订单并提供订单详情入口。</span></li>
              <li><b>随后查看包裹与评价</b><span>确认收货与评价仍由订单详情中的服务端 actions 控制。</span></li>
            </ol>
            <p class="demo-note">本页不会自动确认收货，也不会自动提交评价。</p>
          </div>
        </aside>
      </div>
    </section>`;
  }

  function outcomeHtml() {
    const order = outcome?.order || {};
    const orderNo = outcome?.orderNo || order.order_no;
    const isSuccess = outcome?.type === "success";
    const isUncertainPay = outcome?.type === "pay_uncertain";
    const isUncertainCreate = outcome?.type === "order_uncertain";
    const title = isSuccess ? "模拟支付完成" : isUncertainCreate ? "订单状态待确认" : isUncertainPay ? "支付结果待确认" : "模拟支付未完成";
    const statusLabel = order.status_label || (isSuccess ? "服务端已受理" : "");
    const group = order.group;
    const paymentState = isSuccess ? "success" : isUncertainCreate ? "order_uncertain" : isUncertainPay ? "pay_uncertain" : "pay_failed";
    const paymentLabel = isSuccess ? "模拟支付完成" : isUncertainCreate ? "订单状态待确认" : isUncertainPay ? "支付结果待确认" : "模拟支付未完成";
    return `<section class="container trade-page checkout-page checkout-outcome" data-testid="checkout-root" aria-labelledby="checkout-outcome-title">
      <div class="outcome-card trade-card ${isSuccess ? "outcome-card--success" : "outcome-card--attention"}">
        <div class="outcome-seal ${isSuccess ? "" : "outcome-seal--attention"}" aria-hidden="true">${isSuccess ? icon("check", 30) : icon("alert", 28)}</div>
        <p class="eyebrow">${isSuccess ? "DEMO PAYMENT · 完成" : "NEEDS ATTENTION · 请查看"}</p>
        <h1 id="checkout-outcome-title" tabindex="-1">${escapeHtml(title)}</h1>
        <p class="payment-status payment-status--${paymentState}" data-testid="payment-status" data-payment-state="${paymentState}" role="status">${paymentLabel}</p>
        <p class="outcome-lede">${isSuccess ? "这是一笔模拟支付，没有产生真实扣款。订单已经由服务端创建，接下来可在订单详情查看包裹与状态。" : escapeHtml(outcome?.message || "请到订单详情确认最新状态，本页不会重复创建订单。")}</p>
        <div class="outcome-facts">
          ${orderNo ? `<div><span>订单号</span><b>${escapeHtml(orderNo)}</b></div>` : ""}
          ${statusLabel ? `<div><span>当前状态</span><b>${escapeHtml(statusLabel)}</b></div>` : ""}
          ${Number.isFinite(Number(order.total_cents)) ? `<div><span>订单金额</span><b>${amountHtml(order.total_cents)}</b></div>` : ""}
          ${group ? `<div><span>拼团</span><b>${escapeHtml(group.code || "")} · ${Number(group.joined_count) || 0}/${Number(group.required_size) || 0}</b></div>` : ""}
        </div>
        ${group?.code ? `<p class="outcome-share">拼团邀请码 ${escapeHtml(group.code)} · <a href="/g/${encodeURIComponent(group.code)}" data-link>打开拼团页并分享</a></p>` : ""}
        <p class="demo-note">${isSuccess ? "模拟支付已完成；演示环境不涉及真实资金。" : "支付结果、订单状态与可执行操作，一律以服务端订单详情为准。"}</p>
        <div class="cluster mt-3 outcome-actions">
          ${orderNo ? `<a class="btn btn--primary" href="/orders/${encodeURIComponent(orderNo)}" data-link>查看订单详情 ${icon("arrowRight", 16)}</a>` : '<a class="btn btn--primary" href="/account?tab=orders" data-link>查看我的订单</a>'}
          <a class="btn btn--ghost" href="/search" data-link>继续逛逛</a>
          <a class="btn btn--quiet" href="/cart" data-link>购物车</a>
        </div>
        <p class="tiny muted">${isSuccess ? `成功时间以订单详情为准 · ${dateText(order.paid_at || order.created_at)}` : "未取得明确成功响应前，本页不会显示支付成功。"}</p>
      </div>
    </section>`;
  }

  function readyHtml() {
    const items = cartItemsList();
    const address = currentAddress();
    return `<section class="container trade-page checkout-page" data-testid="checkout-root" aria-labelledby="checkout-heading" aria-busy="${quoting || submitting || preparing ? "true" : "false"}">
      ${headerHtml()}
      ${stepsHtml()}
      ${alertMessage ? `<div class="error-inline trade-alert" role="alert" tabindex="-1">${icon("alert", 16)} <span>${escapeHtml(alertMessage)}</span>${alertMessage.includes("幂等") || alertMessage.includes("无法确认") ? ' <a class="trade-alert__link" href="/account?tab=orders">先去订单列表核对</a>' : ""}</div>` : ""}
      <div class="trade-layout checkout-layout">
        <div class="checkout-main stack">
          <section class="trade-card checkout-section" aria-labelledby="checkout-address-title">
            <div class="trade-section-head">
              <div><span class="trade-section-head__num">01</span><div><h2 id="checkout-address-title">收货地址</h2><p>由地址簿服务提供，选择后用于本单。</p></div></div>
              <div class="cluster"><a class="link-arrow" href="/account?tab=addresses" data-link>管理地址</a><button class="btn btn--small btn--ghost" type="button" data-testid="address-add" data-add-address aria-label="新增收货地址" ${busy() ? "disabled" : ""}>+ 新增地址</button></div>
            </div>
            ${addressChoicesHtml()}
          </section>
          <section class="trade-card checkout-section" aria-labelledby="checkout-shipping-title">
            <div class="trade-section-head"><div><span class="trade-section-head__num">02</span><div><h2 id="checkout-shipping-title">配送方式</h2><p>选项与费用来自当前服务端报价。</p></div></div>${quote ? `<span class="tiny muted">${quote.has_group ? "拼团仅标准配送" : "以服务端返回为准"}</span>` : ""}</div>
            ${shippingPanelHtml()}
          </section>
          <section class="trade-card checkout-section" aria-labelledby="checkout-coupon-title">
            <div class="trade-section-head"><div><span class="trade-section-head__num">03</span><div><h2 id="checkout-coupon-title">优惠券</h2><p>每次更换都会重新向服务端报价。</p></div></div></div>
            ${couponPanelHtml()}
          </section>
          <section class="trade-card checkout-section" aria-labelledby="checkout-items-title">
            <div class="trade-section-head"><div><span class="trade-section-head__num">04</span><div><h2 id="checkout-items-title">商品清单</h2><p>共 ${Number(quote?.selected_count) || 0} 件已选 · 由购物车服务返回</p></div></div></div>
            ${itemRowsHtml()}
            ${items.some((item) => item.buy_mode === "group") ? '<p class="shipping-note">含拼团商品：创建订单时还会校验邀请码、团状态与成员身份；付款后才计入公开成团进度。</p>' : ""}
          </section>
          <section class="trade-card checkout-section" aria-labelledby="checkout-note-title">
            <div class="trade-section-head"><div><span class="trade-section-head__num">05</span><div><h2 id="checkout-note-title">订单备注</h2><p>选填，随订单一起交给商家。</p></div></div></div>
            <label class="field"><span class="sr-only">订单备注</span><textarea class="textarea" id="checkout-note" maxlength="200" placeholder="例如：工作日送达，请放门口。">${escapeHtml(note)}</textarea><span class="field-hint">最多 200 字；配送与隐私信息不必写在备注里。</span></label>
          </section>
          <p class="demo-note checkout-demo-note">${icon("info", 16)} 这是模拟支付流程：提交订单和支付都会调用真实后端接口，但不会产生真实扣款。金额只以服务端报价与订单返回为准。</p>
        </div>
        <aside class="checkout-aside" aria-label="结算汇总">
          ${summaryHtml()}
        </aside>
      </div>
    </section>`;
  }

  function paint() {
    if (ctx.signal.aborted) return;
    if (outcome) {
      ctx.root.innerHTML = outcomeHtml();
      bindOutcome();
      focusAfter("#checkout-outcome-title");
      return;
    }
    if (!state.user) {
      ctx.root.innerHTML = guestHtml();
      ctx.root.querySelector("[data-login]")?.addEventListener("click", openLogin);
      return;
    }
    if (booting) {
      ctx.root.innerHTML = skeletonHtml();
      return;
    }
    if (fatalError) {
      ctx.root.innerHTML = fatalHtml();
      ctx.root.querySelector("[data-retry-init]")?.addEventListener("click", () => { void initialize(); });
      return;
    }
    if (createdOrder) {
      ctx.root.innerHTML = createdHtml();
      bindCreated();
      if (!submitting) focusAfter("#checkout-created-title");
      return;
    }
    if (noSelection) {
      ctx.root.innerHTML = emptyHtml();
      focusAfter("#checkout-heading");
      return;
    }
    ctx.root.innerHTML = readyHtml();
    bindReady();
    if (alertMessage) focusAfter(".trade-alert");
  }

  function bindOutcome() {
    // Internal links are delegated by the app shell; nothing else to wire.
  }

  function bindCreated() {
    ctx.root.querySelector("#pay-demo")?.addEventListener("click", () => { void payCreated(); });
  }

  function markQuoteBusy() {
    const button = ctx.root.querySelector("#place-order");
    const region = ctx.root.querySelector(".quote-summary");
    if (region) region.setAttribute("aria-busy", "true");
    const meta = region?.querySelector(".quote-meta");
    if (meta) meta.textContent = "正在向服务端确认最新价格…";
    if (button instanceof HTMLButtonElement) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      button.innerHTML = '<span class="spinner" aria-hidden="true"></span> 正在更新报价';
    }
  }

  function bindReady() {
    ctx.root.querySelector("[data-add-address]")?.addEventListener("click", () => {
      if (!busy()) openAddressModal();
    });
    ctx.root.querySelectorAll("[data-address-id]").forEach((input) => {
      input.addEventListener("change", () => {
        if (!input.checked) return;
        selectedAddressId = addresses.find((address) => String(address.id) === String(input.dataset.addressId))?.id ?? null;
        alertMessage = "";
        paint();
        focusAfter(`[data-address-id="${input.dataset.addressId}"]`);
      });
    });
    ctx.root.querySelectorAll("[data-shipping-code]").forEach((input) => {
      input.addEventListener("change", () => {
        if (!input.checked || busy() || quoting) return;
        selectedShipping = input.value;
        alertMessage = "";
        const radioSelector = '[data-shipping-code="' + (window.CSS && CSS.escape ? CSS.escape(input.value) : input.value) + '"]';
        void requestQuote({ focusTarget: radioSelector });
      });
    });
    ctx.root.querySelector("#checkout-coupon")?.addEventListener("change", (event) => {
      if (busy() || quoting) return;
      selectedCoupon = event.target.value || "";
      alertMessage = "";
      void requestQuote({ focusTarget: "#checkout-coupon" });
    });
    ctx.root.querySelectorAll("[data-retry-addresses]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        await fetchAddresses();
        if (!ctx.signal.aborted) paint();
      });
    });
    ctx.root.querySelectorAll("[data-retry-coupons]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        await fetchCoupons();
        if (!ctx.signal.aborted) {
          paint();
          focusAfter("#checkout-coupon");
        }
      });
    });
    ctx.root.querySelector("[data-retry-quote]")?.addEventListener("click", () => {
      if (busy()) return;
      void requestQuote({ focusTarget: "#checkout-summary-title" });
    });
    ctx.root.querySelector("#checkout-note")?.addEventListener("input", (event) => {
      note = event.target.value;
    });
    ctx.root.querySelector("#place-order")?.addEventListener("click", () => { void beginSubmit(); });
    ctx.root.querySelector("[data-retry-init]")?.addEventListener("click", () => { void initialize(); });
  }

  function openLogin() {
    openAuth({
      reason: "结算、地址和订单都需要一个身份。演示买家支持一键登录，游客购物车会自动合并，且不会发生真实付款。",
      onSuccess: async () => {
        if (ctx.signal.aborted) return;
        await initialize();
      },
    });
  }

  async function fetchAddresses() {
    try {
      const result = await api.get("/api/addresses", { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      addresses = Array.isArray(result?.items) ? result.items : [];
      defaultAddressId = result?.default_id ?? null;
      addressesError = "";
      const stillThere = addresses.some((address) => String(address.id) === String(selectedAddressId));
      if (!stillThere) selectedAddressId = null;
      if (selectedAddressId == null) {
        const fallback = addresses.find((address) => String(address.id) === String(defaultAddressId))
          || addresses.find((address) => address.is_default)
          || addresses[0];
        selectedAddressId = fallback?.id ?? null;
      }
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError") return;
      addressesError = errMessage(err, "收货地址暂时没有读出来。");
    }
  }

  async function fetchCoupons() {
    try {
      const result = await api.get("/api/coupons", { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      coupons = {
        available: Array.isArray(result?.available) ? result.available : [],
        mine: Array.isArray(result?.mine) ? result.mine : [],
      };
      couponsError = "";
      if (selectedCoupon && !mineCoupons().some((coupon) => coupon.code === selectedCoupon)) selectedCoupon = "";
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError") return;
      couponsError = errMessage(err, "优惠券暂时没有读出来。");
      coupons = { available: [], mine: [] };
    }
  }

  async function fetchQuoteDirect({ final = false } = {}) {
    if (ctx.signal.aborted) return false;
    const seq = ++quoteSeq;
    quoting = true;
    if (!booting && !final) markQuoteBusy();
    try {
      const body = { shipping_method: selectedShipping };
      if (selectedCoupon) body.coupon_code = selectedCoupon;
      const data = await api.post("/api/checkout/quote", body, { signal: ctx.signal });
      if (ctx.signal.aborted || seq !== quoteSeq) return false;
      quoting = false;
      quoteError = "";
      quote = data && typeof data === "object" ? data : null;
      if (!quote) {
        quoteError = "服务端没有返回可用报价。";
        return false;
      }
      const options = Array.isArray(quote.shipping_options) ? quote.shipping_options : [];
      if (options.length && !options.some((option) => option.code === selectedShipping)) {
        selectedShipping = options[0].code;
        return fetchQuoteDirect({ final });
      }
      return true;
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError") return false;
      if (seq !== quoteSeq) return false;
      quoting = false;
      quote = null;
      quoteError = errMessage(err, "暂时无法取得服务端报价，请重试。");
      return false;
    }
  }

  async function requestQuote({ focusTarget = null } = {}) {
    if (busy()) return false;
    if (!booting) {
      const region = ctx.root.querySelector(".quote-summary");
      if (region) region.setAttribute("aria-busy", "true");
      markQuoteBusy();
    }
    const ok = await fetchQuoteDirect();
    if (ctx.signal.aborted) return false;
    paint();
    if (focusTarget) focusAfter(focusTarget);
    if (!ok) focusAfter("#quote-error");
    return ok;
  }

  async function initialize() {
    if (!state.user) {
      booting = false;
      outcome = null;
      paint();
      return;
    }
    booting = true;
    fatalError = "";
    noSelection = false;
    outcome = null;
    createdOrder = null;
    alertMessage = "";
    quoteError = "";
    quote = null;
    submitting = false;
    preparing = false;
    submitFlow = false;
    attempt = null;
    addressesError = "";
    couponsError = "";
    paint();
    try {
      const cartData = await api.get("/api/cart", { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      cart = cartData;
      if (!cart || !Array.isArray(cart.items) || !cart.summary) throw new Error("购物车响应缺少必要字段");
      updateCartCount(Number(cart.summary.item_count ?? cart.items.length));
      if (!selectedItems(cart).length) {
        booting = false;
        noSelection = true;
        paint();
        return;
      }
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError") return;
      booting = false;
      fatalError = errMessage(err, "暂时无法读取购物车，请稍后再试。");
      paint();
      return;
    }
    await Promise.all([fetchAddresses(), fetchCoupons()]);
    if (ctx.signal.aborted) return;
    booting = false;
    await fetchQuoteDirect();
    if (ctx.signal.aborted) return;
    paint();
    if (quoteError) focusAfter("#quote-error");
  }

  function buildOrderBody() {
    const body = {
      address_id: currentAddress()?.id ?? null,
      shipping_method: selectedShipping,
      note: note.trim(),
      payment_method: "demo",
    };
    if (selectedCoupon) body.coupon_code = selectedCoupon;
    return body;
  }

  function keyFor(body) {
    const signature = JSON.stringify(body);
    if (!attempt || attempt.signature !== signature) {
      attempt = { signature, key: newIdempotencyKey() };
    }
    return attempt.key;
  }

  async function beginSubmit() {
    if (submitFlow || submitting) return;
    if (!quote || quoteError) {
      alertMessage = quoteError || "请先取得有效的服务端报价。";
      paint();
      focusAfter(quoteError ? "#quote-error" : ".trade-alert");
      return;
    }
    if (!currentAddress()) {
      alertMessage = "请选择或新增一个收货地址后再提交。";
      paint();
      focusAfter(".trade-alert");
      return;
    }
    if (quote.coupon_error) {
      alertMessage = "这张优惠券未被服务端采用，请重新选择优惠券或改用“不使用优惠券”。";
      paint();
      focusAfter(".trade-alert");
      return;
    }
    if (quoting) return;
    submitFlow = true;
    preparing = true;
    alertMessage = "";
    paint();
    const refreshed = await fetchQuoteDirect({ final: true });
    if (ctx.signal.aborted) return;
    preparing = false;
    if (!refreshed || !quote) {
      submitFlow = false;
      paint();
      focusAfter(quoteError ? "#quote-error" : ".trade-alert");
      return;
    }
    if (quote.coupon_error) {
      submitFlow = false;
      paint();
      focusAfter(".trade-alert");
      return;
    }
    paint();
    const body = buildOrderBody();
    const key = keyFor(body);
    submitting = true;
    paint();
    await createOrder({ ...body, idempotency_key: key });
  }

  async function createOrder(body) {
    let created;
    try {
      created = await api.post("/api/orders", body, { signal: ctx.signal });
    } catch (err) {
      if (ctx.signal.aborted) return;
      submitting = false;
      preparing = false;
      submitFlow = false;
      const uncertain = !(err instanceof ApiError) || err.status === 0 || err.status >= 500;
      if (!uncertain) attempt = null;
      alertMessage = uncertain
        ? `无法确认订单是否已创建：${sentenceEnd(errMessage(err))}请勿重复提交新订单；若重试，本页会对完全相同的订单 body 复用同一幂等键。`
        : sentenceEnd(errMessage(err, "订单没有创建成功，请稍后再试。"));
      paint();
      focusAfter(".trade-alert");
      return;
    }
    if (ctx.signal.aborted) return;
    if (!created?.order_no) {
      submitting = false;
      submitFlow = false;
      outcome = {
        type: "order_uncertain",
        order: created && typeof created === "object" ? created : {},
        orderNo: "",
        message: "服务端已返回响应，但没有可核对的订单号。为避免重复下单，本页不会自动重试；请先到订单列表确认。",
      };
      ctx.setTitle("订单状态待确认 · OpenShop 开集");
      paint();
      return;
    }
    attempt = null;
    createdOrder = created;
    submitting = false;
    preparing = false;
    submitFlow = false;
    alertMessage = "";
    ctx.setTitle("等待模拟支付 · OpenShop 开集");
    paint();
    void refreshSession().catch(() => {});
  }

  async function payCreated() {
    if (submitting || !createdOrder) return;
    const createdOrderNo = createdOrder.order_no;
    if (!createdOrderNo) {
      submitting = false;
      outcome = {
        type: "order_uncertain",
        order: createdOrder,
        orderNo: "",
        message: "订单没有可核对的订单号，本页不会发起支付；请到订单列表确认。",
      };
      paint();
      return;
    }
    submitting = true;
    alertMessage = "";
    paint();
    let paid = null;
    let payErr = null;
    try {
      paid = await api.post(`/api/orders/${encodeURIComponent(createdOrderNo)}/pay`, { payment_method: "demo" }, { signal: ctx.signal });
    } catch (err) {
      if (ctx.signal.aborted) return;
      payErr = err;
    }
    if (ctx.signal.aborted) return;
    if (payErr) {
      let latest = createdOrder;
      try {
        const fetched = await api.get(`/api/orders/${encodeURIComponent(createdOrderNo)}`, { signal: ctx.signal });
        if (fetched && typeof fetched.order_no === "string") latest = fetched;
      } catch (_) {
        // Never convert a failed/unknown payment response into a success screen.
      }
      if (ctx.signal.aborted) return;
      const definitive = payErr instanceof ApiError && payErr.status > 0 && payErr.status < 500;
      submitting = false;
      submitFlow = false;
      outcome = {
        type: definitive ? "pay_failed" : "pay_uncertain",
        order: latest,
        orderNo: latest?.order_no || createdOrderNo,
        message: definitive
          ? `${sentenceEnd(errMessage(payErr))}订单已创建，请到订单详情查看并重试支付。`
          : `${sentenceEnd(errMessage(payErr))}订单已创建；支付结果以订单详情中的服务端状态为准，本页不会重复创建订单。`,
      };
      ctx.setTitle("模拟支付未完成 · OpenShop 开集");
      paint();
      return;
    }
    const finalOrder = paid && typeof paid === "object" ? paid : createdOrder;
    submitting = false;
    submitFlow = false;
    outcome = {
      type: "success",
      order: finalOrder,
      orderNo: finalOrder?.order_no || createdOrderNo,
      message: "",
    };
    ctx.setTitle("模拟支付完成 · OpenShop 开集");
    paint();
    void refreshSession().catch(() => {});
  }

  function openAddressModal() {
    addressSeq += 1;
    const formId = `checkout-address-form-${addressSeq}`;
    const form = document.createElement("form");
    form.id = formId;
    form.className = "trade-form";
    form.setAttribute("data-testid", "address-form");
    form.innerHTML = `<div class="form-grid form-grid--2">
      <label class="field"><span class="field-label">收件人</span><input class="input" data-testid="recipient-input" name="receiver" required minlength="2" maxlength="20" autocomplete="name" placeholder="2-20 个字"></label>
      <label class="field"><span class="field-label">手机号</span><input class="input" data-testid="phone-input" name="phone" type="tel" inputmode="numeric" required pattern="1[3-9][0-9]{9}" maxlength="11" title="请输入 11 位大陆手机号" autocomplete="tel" placeholder="11 位演示手机号"></label>
      <label class="field"><span class="field-label">省</span><input class="input" data-testid="province-input" name="province" required maxlength="20" autocomplete="address-level1" placeholder="上海市"></label>
      <label class="field"><span class="field-label">市</span><input class="input" data-testid="city-input" name="city" required maxlength="20" autocomplete="address-level2" placeholder="上海市"></label>
      <label class="field"><span class="field-label">区 / 县</span><input class="input" data-testid="district-input" name="district" required maxlength="20" autocomplete="address-level3" placeholder="徐汇区"></label>
      <label class="field"><span class="field-label">详细地址</span><input class="input" data-testid="detail-input" name="detail" required minlength="5" maxlength="120" autocomplete="street-address" placeholder="街道、门牌号、楼栋与房间"></label>
      <label class="field"><span class="field-label">地址标签 <span class="tiny muted">选填</span></span><input class="input" data-testid="address-label" name="label" maxlength="10" placeholder="家 / 公司"></label>
    </div>
    <label class="check"><input type="checkbox" name="is_default" value="1"> 设为默认地址</label>
    <p class="form-error" role="alert" tabindex="-1" hidden></p>
    <div class="trade-form__actions"><button class="btn btn--ghost" type="button" data-cancel>取消</button><button class="btn btn--primary" type="submit" data-testid="address-save" data-save>保存并使用</button></div>`;
    const errorBox = form.querySelector(".form-error");
    let saving = false;
    const modal = openModal({
      title: "新增收货地址",
      subtitle: "地址仅用于履约演示；完整信息只展示给你自己。",
      body: form,
    });
    form.querySelector("[data-cancel]")?.addEventListener("click", () => modal.close());
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (saving) return;
      if (!form.reportValidity()) return;
      saving = true;
      errorBox.hidden = true;
      const save = form.querySelector("[data-save]");
      save.disabled = true;
      save.innerHTML = '<span class="spinner" aria-hidden="true"></span> 保存中';
      const values = new FormData(form);
      const body = {
        receiver: String(values.get("receiver") || "").trim(),
        phone: String(values.get("phone") || "").trim(),
        province: String(values.get("province") || "").trim(),
        city: String(values.get("city") || "").trim(),
        district: String(values.get("district") || "").trim(),
        detail: String(values.get("detail") || "").trim(),
        label: String(values.get("label") || "").trim(),
        is_default: values.get("is_default") === "1",
      };
      try {
        const created = await api.post("/api/addresses", body, { signal: ctx.signal });
        if (ctx.signal.aborted) return;
        modal.close();
        await fetchAddresses();
        if (ctx.signal.aborted) return;
        if (created?.id != null) selectedAddressId = created.id;
        paint();
        focusAfter('[name="checkout-address"]:checked');
        toast("地址已保存，并选为本次收货地址");
      } catch (err) {
        if (ctx.signal.aborted) return;
        saving = false;
        save.disabled = false;
        save.textContent = "保存并使用";
        errorBox.textContent = errMessage(err, "地址没有保存成功，请稍后再试。");
        errorBox.hidden = false;
        errorBox.focus?.();
      }
    });
    window.requestAnimationFrame(() => form.querySelector("input")?.focus());
  }

  paint();
  if (state.user) await initialize();
}
