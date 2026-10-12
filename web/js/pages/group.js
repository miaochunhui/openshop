// Public group page: real progress, honest statuses, login then group checkout.
import { api, ApiError } from "../core/api.js";
import { state, updateCartCount } from "../core/store.js";
import {
  escapeHtml,
  icon,
  money,
  openAuth,
  priceHtml,
  toast,
} from "../components/index.js";

const STATUS_LABELS = {
  FORMING: "正在成团",
  FULL: "已成团",
  CANCELLED: "已取消",
  EXPIRED: "已过期",
};

const STATUS_COPY = {
  FORMING: "付款之后才会计入公开进度。人满后状态会由服务端变为已成团。",
  FULL: "这个团已经满员，不能再加入。可以看看商品页是否还有新的拼团。",
  CANCELLED: "这个团已取消，不能继续加入。",
  EXPIRED: "这个团已过期，不能继续加入。",
};

const ERROR_HINTS = {
  MIXED_BUY_MODE: "购物车里还有普通商品，拼团商品需要单独结算。请先回到购物车处理，再加入拼团。",
  GROUP_FULL: "你点击的这一刻团已经满员，公开进度以服务端最新状态为准。",
  GROUP_UNAVAILABLE: "这个团已失效或不接受新成员。",
  STOCK_CHANGED: "货源库存刚刚变化，请刷新页面后再试。",
  AUTH_REQUIRED: "登录状态已失效，请重新登录后再参团。",
  CART_EMPTY: "购物车里没有可结算的拼团商品。",
};

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

function moneyOrDash(cents) {
  return Number.isFinite(Number(cents)) && cents !== null
    ? priceHtml(cents)
    : '<span class="muted">—</span>';
}

function groupUrl(code) {
  return `${window.location.origin}/g/${encodeURIComponent(String(code || ""))}`;
}

export async function render(ctx) {
  const code = String(ctx.params.code || "");
  ctx.setTitle(`一起拼 ${code} · OpenShop 开集`);

  let group = null;
  let loading = true;
  let loadError = "";
  let alertMessage = "";
  let alertCode = "";
  let joining = false;

  function focusAfter(selector) {
    requestAnimationFrame(() => {
      if (ctx.signal.aborted) return;
      const target = ctx.root.querySelector(selector);
      if (target && typeof target.focus === "function") target.focus({ preventScroll: true });
    });
  }

  function root(content, extra = "") {
    return `<section class="container trade-page group-page" data-testid="group-root" data-group-code="${escapeHtml(code)}" aria-labelledby="group-heading"${extra}>
      ${content}
    </section>`;
  }

  function skeletonHtml() {
    return root(`<div class="group-loading" aria-busy="true">
      <div class="skeleton" style="height:22px;width:22%"></div>
      <div class="skeleton mt-2" style="height:46px;width:46%"></div>
      <div class="trade-layout mt-4">
        <div class="trade-card group-main"><div class="skeleton" style="height:280px;border-radius:var(--radius)"></div><div class="skeleton mt-3" style="height:20px;width:60%"></div></div>
        <div class="trade-card"><div class="skeleton" style="height:24px;width:55%"></div><div class="skeleton mt-3" style="height:12px"></div><div class="skeleton mt-3" style="height:52px;border-radius:999px"></div></div>
      </div>
      <p class="sr-only" role="status">正在读取拼团</p>
    </div>`, ' aria-busy="true"');
  }

  function errorHtml() {
    return root(`<header class="group-page-head">
      <p class="eyebrow">GROUP · ${escapeHtml(code)}</p>
      <h1 id="group-heading" tabindex="-1">一起拼</h1>
    </header>
    <div class="trade-card order-fatal">
      <div class="empty-state__icon">${icon("gift", 26)}</div>
      <h2>这个团开不起来了</h2>
      <p class="error-inline" role="alert">${escapeHtml(loadError)}</p>
      <p class="muted small">拼团邀请码：${escapeHtml(code)}。公开进度只来自服务端，不接受页面伪造。</p>
      <div class="cluster mt-3"><button class="btn btn--primary" type="button" data-retry-group>重新读取</button><a class="btn btn--ghost" href="/search" data-link>找别的拼团</a><a class="btn btn--quiet" href="/account?tab=orders" data-link>我的订单</a></div>
    </div>`);
  }

  function actions() {
    return group?.actions || {};
  }

  function needLogin() {
    return Boolean(actions().needs_login) || (!state.user && Boolean(actions().can_join));
  }

  function membersHtml() {
    const members = Array.isArray(group?.members) ? group.members : [];
    if (!members.length) {
      return '<p class="muted small group-members__empty">还没有已付款成员。公开人数只计算真正付款的人，未付款订单不会出现在这里。</p>';
    }
    return `<ul class="group-members" aria-label="已付款参团成员">
      ${members.map((member) => `<li class="group-member">
        <span class="group-member__avatar" aria-hidden="true">${escapeHtml(String(member.display_name || "客").slice(0, 1))}</span>
        <span><b>${escapeHtml(member.display_name || "参团成员")}</b><span class="tiny muted">${member.is_initiator ? "发起人" : "参团成员"} · ${dateText(member.joined_at)}</span></span>
      </li>`).join("")}
    </ul>`;
  }

  function primaryActionHtml() {
    const serverActions = actions();
    if (joining) {
      return `<button class="btn btn--primary btn--large btn--block" type="button" disabled aria-busy="true"><span class="spinner" aria-hidden="true"></span> 正在加入拼团购物车</button>`;
    }
    if (needLogin()) {
      return `<button class="btn btn--primary btn--large btn--block group-login-btn" type="button" data-testid="group-login" data-login>${icon("user", 17)} 登录后参团</button><p class="tiny muted group-cta-note">登录只确认身份；最终能否加入仍以服务端响应为准。</p>`;
    }
    if (serverActions.can_join) {
      return `<button class="btn btn--primary btn--large btn--block" type="button" data-testid="group-join" data-join>${icon("gift", 17)} 加入拼团购物车，去结算</button><p class="tiny muted group-cta-note">加入购物车不会让进度 +1；付款成功后才会成为公开成员。</p>`;
    }
    if (group?.joined_by_me) {
      return `<div class="notice group-already">${icon("check", 16)} 你已经在这个团里。可在订单列表查看付款与成团状态。</div><a class="btn btn--ghost btn--block mt-2" href="/account?tab=orders" data-link>查看我的订单 ${icon("arrowRight", 14)}</a>`;
    }
    return `<div class="group-closed"><b>${escapeHtml(STATUS_LABELS[group?.status] || "暂不可参团")}</b><p>${escapeHtml(serverActions.reason || STATUS_COPY[group?.status] || "以服务端状态为准，当前无法加入这个团。")}</p></div>`;
  }

  function alertHtml() {
    if (!alertMessage) return "";
    return `<div class="error-inline group-alert" role="alert" tabindex="-1">
      ${icon("alert", 16)} <span>${escapeHtml(alertMessage)}</span>
      ${alertCode === "MIXED_BUY_MODE" ? '<a class="trade-alert__link" href="/cart" data-link>去购物车处理</a>' : ""}
      ${alertCode === "AUTH_REQUIRED" ? '<button class="btn btn--small btn--quiet" type="button" data-login>重新登录</button>' : ""}
    </div>`;
  }

  function detailHtml() {
    const product = group.product || {};
    const sku = group.sku || {};
    const offer = group.offer || {};
    const joined = Number(group.joined_count) || 0;
    const required = Number(group.required_size) || 0;
    const percent = required > 0 ? Math.min(100, Math.round((joined / required) * 100)) : 0;
    const statusLabel = STATUS_LABELS[group.status] || group.status || "状态未知";
    const headingText = group.status === "FORMING" ? "一起拼，慢慢等成团" : group.status === "FULL" ? "这个团，已经成团" : "这个团，已经结束";
    const progressHint = joined >= required && required > 0 ? "人数已满" : group.status === "FORMING" ? `还差 ${Math.max(0, required - joined)} 人成团` : "本团已结束";
    const canJoinNow = needLogin() || actions().can_join;
    return root(`<header class="group-page-head">
      <div>
        <p class="eyebrow">GROUP · ${escapeHtml(group.code || code)}</p>
        <h1 id="group-heading" tabindex="-1">${headingText}</h1>
        <p class="lede">邀请码 ${escapeHtml(group.code || code)}。页面上的进度、成员与可参团状态全都来自服务端，不预测、不假装。</p>
      </div>
      <a class="btn btn--ghost group-head__back" href="/search" data-link>${icon("arrowLeft", 15)} 继续逛逛</a>
    </header>
    ${alertHtml()}
    <div class="trade-layout group-layout">
      <div class="group-main">
        <section class="trade-card group-product-card" aria-labelledby="group-product-title">
          <div class="group-product-card__art"><img src="${escapeHtml(product.cover || "")}" alt="${escapeHtml(product.title || "拼团商品")}" loading="lazy" decoding="async"></div>
          <div class="group-product-card__body">
            <div class="between">
              <div><p class="eyebrow eyebrow--jade">${escapeHtml(product.brand || "开集")}</p><h2 id="group-product-title">${escapeHtml(product.title || "拼团商品")}</h2></div>
              <span class="group-status-badge">${escapeHtml(statusLabel)}</span>
            </div>
            <p class="muted">${escapeHtml(offer.name || "")} · ${escapeHtml(offer.shop?.name || "")}</p>
            <p class="muted small">规格：${escapeHtml(sku.name || "以商品规格为准")}</p>
            <div class="group-price-row">
              <div><span class="tiny muted">拼团价</span><strong>${moneyOrDash(group.group_price_cents)}</strong></div>
              <span class="chip chip--honey">${required} 人成团</span>
            </div>
            <div class="group-progress" data-testid="group-progress" role="progressbar" aria-label="拼团真实进度" aria-valuemin="0" aria-valuemax="${required}" aria-valuenow="${joined}">
              <div class="group-progress__track" style="--group-progress:${percent}%"><span></span></div>
              <div class="group-progress__labels"><b>${joined} / ${required} 已付款成员</b><span>${escapeHtml(progressHint)}</span></div>
            </div>
            ${group.expires_at ? `<p class="group-expiry">${icon("clock", 14)} 成团截止 ${dateText(group.expires_at)}（Asia/Shanghai）</p>` : ""}
            <p class="tiny muted">商品链接：<a href="/p/${encodeURIComponent(product.slug || "")}" data-link>查看商品详情 ${icon("arrowRight", 13)}</a></p>
          </div>
        </section>
        <section class="trade-card group-members-card" aria-labelledby="group-members-title">
          <div class="trade-section-head"><div><span class="trade-section-head__num">${joined}</span><div><h2 id="group-members-title">已付款成员</h2><p>未付款订单不会出现在公开成员或人数中。</p></div></div></div>
          ${membersHtml()}
        </section>
        <section class="trade-card group-share-card" aria-labelledby="group-share-title">
          <div class="trade-section-head"><div><span class="trade-section-head__num">链</span><div><h2 id="group-share-title">邀请朋友参团</h2><p>分享这个页面的真实地址；朋友仍需登录并完成付款才算参团成功。</p></div></div></div>
          <label class="field"><span class="field-label">拼团邀请链接</span><input class="input" data-testid="group-share-url" readonly value="${escapeHtml(groupUrl(group.code || code))}"></label>
          <div class="cluster mt-2"><button class="btn btn--ghost btn--small" type="button" data-copy-group>${icon("copy", 14)} 复制邀请链接</button><a class="btn btn--ghost btn--small" href="/p/${encodeURIComponent(product.slug || "")}" data-link>看商品 ${icon("arrowRight", 13)}</a></div>
        </section>
      </div>
      <aside class="group-aside" aria-label="参团操作">
        <div class="trade-summary group-summary">
          <div class="trade-summary__head"><div><p class="eyebrow">JOIN</p><h2>${canJoinNow ? "加入这个团" : "参团状态"}</h2></div></div>
          <div class="amount-list">
            <div><dt>拼团价</dt><dd>${moneyOrDash(group.group_price_cents)}</dd></div>
            <div><dt>当前进度</dt><dd>${joined} / ${required} 人</dd></div>
            <div><dt>状态</dt><dd>${escapeHtml(statusLabel)}</dd></div>
          </div>
          ${group.joined_by_me ? '<p class="chip chip--jade">你在团中</p>' : ""}
          ${primaryActionHtml()}
          ${actions().reason && needLogin() ? `<p class="group-reason">${escapeHtml(actions().reason)}</p>` : ""}
          <p class="group-honesty">${icon("info", 15)} 登录、加入购物车、付款是三件事；本页不会提前把购物车或未付款订单算进人数。</p>
        </div>
        <div class="trade-card group-help"><b>价格与服务</b><p>拼团价由商品报价提供；最终应付、优惠与运费在结算页由服务端重算。支付为模拟。</p><a class="link-arrow" href="/help" data-link>拼团规则 ${icon("arrowRight", 14)}</a></div>
      </aside>
    </div>
    <p class="group-footnote">页面刷新会重新读取 GET /api/groups/${escapeHtml(code)}；若团已变化，以最新响应为准。</p>`, "");
  }

  function paint() {
    if (ctx.signal.aborted) return;
    if (loading) { ctx.root.innerHTML = skeletonHtml(); return; }
    if (loadError) { ctx.root.innerHTML = errorHtml(); bindError(); return; }
    if (!group) return;
    ctx.root.innerHTML = detailHtml();
    bindDetail();
    if (alertMessage) focusAfter(".group-alert");
  }

  function bindError() {
    ctx.root.querySelector("[data-retry-group]")?.addEventListener("click", () => {
      loading = true;
      loadError = "";
      paint();
      void loadGroup();
    });
  }

  function bindDetail() {
    ctx.root.querySelectorAll("[data-login]").forEach((button) => button.addEventListener("click", openLogin));
    ctx.root.querySelector("[data-testid='group-join']")?.addEventListener("click", () => { void joinGroup(); });
    ctx.root.querySelector("[data-copy-group]")?.addEventListener("click", () => { void copyLink(); });
    ctx.root.querySelector("[data-retry-group]")?.addEventListener("click", () => {
      loading = true;
      loadError = "";
      alertMessage = "";
      paint();
      void loadGroup();
    });
  }

  function openLogin() {
    openAuth({
      reason: "参团需要身份，演示买家可一键登录。加入购物车不会增加公开人数，付款后才会占一个真实坑位。",
      onSuccess: async () => {
        if (ctx.signal.aborted) return;
        alertMessage = "";
        alertCode = "";
        await loadGroup({ quiet: true });
      },
    });
  }

  async function copyLink() {
    const input = ctx.root.querySelector("[data-testid='group-share-url']");
    if (!(input instanceof HTMLInputElement)) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(input.value);
        toast("拼团邀请链接已复制");
        return;
      }
      throw new Error("clipboard unavailable");
    } catch (_) {
      input.focus();
      input.select();
      let copied = false;
      try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
      toast(copied ? "拼团邀请链接已复制" : "请手动复制邀请链接", { type: copied ? "success" : "info" });
    }
  }

  async function refreshGroupQuiet() {
    try {
      const fresh = await api.get(`/api/groups/${encodeURIComponent(code)}`, { signal: ctx.signal });
      if (ctx.signal.aborted || !fresh) return;
      group = fresh;
      paint();
    } catch (_) {
      // Keep the previous server snapshot; the visible API error is the authority for the failed action.
    }
  }

  async function joinGroup() {
    if (joining || !group) return;
    if (!state.user) { openLogin(); return; }
    const joinBtn = ctx.root.querySelector("[data-testid='group-join']");
    if (joinBtn instanceof HTMLButtonElement) {
      joinBtn.disabled = true;
      joinBtn.setAttribute("aria-busy", "true");
      joinBtn.innerHTML = '<span class="spinner" aria-hidden="true"></span> 加入中';
    }
    joining = true;
    alertMessage = "";
    alertCode = "";
    try {
      const cart = await api.post("/api/cart/items", {
        offer_id: group.offer?.id,
        sku_id: group.sku?.id,
        quantity: 1,
        buy_mode: "group",
        group_code: group.code,
      }, { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      if (!cart || !Array.isArray(cart.items) || !cart.summary) throw new Error("购物车响应缺少必要字段");
      const count = Number(cart.summary.item_count);
      if (Number.isFinite(count)) updateCartCount(count);
      toast("拼团商品已加入购物车，去结算确认");
      ctx.navigate("/checkout");
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError") return;
      joining = false;
      alertMessage = errMessage(err, "没有成功加入拼团购物车，请稍后再试。");
      alertCode = err instanceof ApiError ? err.code : "";
      if (alertCode === "GROUP_FULL" || alertCode === "GROUP_UNAVAILABLE" || alertCode === "AUTH_REQUIRED") {
        await refreshGroupQuiet();
      }
      paint();
      focusAfter(".group-alert");
    }
  }

  async function loadGroup({ quiet = false } = {}) {
    if (!quiet) { loading = true; loadError = ""; paint(); }
    try {
      const data = await api.get(`/api/groups/${encodeURIComponent(code)}`, { signal: ctx.signal });
      if (ctx.signal.aborted) return;
      if (!data || !data.code) throw new Error("拼团响应缺少 code");
      group = data;
      loading = false;
      loadError = "";
      paint();
      focusAfter("#group-heading");
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError") return;
      joining = false;
      loading = false;
      loadError = err instanceof ApiError && err.code === "NOT_FOUND"
        ? "没有找到这个拼团。邀请码可能已失效，或者这个团还没有被创建。"
        : errMessage(err, "拼团暂时没有读出来，请稍后再试。");
      paint();
    }
  }

  await loadGroup();
}
