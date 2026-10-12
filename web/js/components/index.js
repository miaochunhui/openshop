// Shared UI primitives. All dynamic text must be escaped before insertion.
import { api, ApiError } from "../core/api.js";
import { applySession, state } from "../core/store.js";

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

const ICONS = {
  search: '<circle cx="10.8" cy="10.8" r="6.3"/><path d="m16 16 4 4"/>',
  user: '<circle cx="12" cy="8" r="3.6"/><path d="M5 20c.6-4 3.2-6 7-6s6.4 2 7 6"/>',
  cart: '<path d="M3.5 5h2l2.2 10.2h9.8l2-7.2H6.1"/><circle cx="9" cy="19" r="1.1"/><circle cx="17" cy="19" r="1.1"/>',
  heart: '<path d="M12 20s-7.4-4.4-7.4-10A4.1 4.1 0 0 1 12 7.4 4.1 4.1 0 0 1 19.4 10c0 5.6-7.4 10-7.4 10Z"/>',
  star: '<path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.4l6.1-.9Z"/>',
  shield: '<path d="M12 3 5 6v5.2c0 4.4 2.9 7.8 7 9.8 4.1-2 7-5.4 7-9.8V6Z"/><path d="m9 12 2 2 4-4"/>',
  truck: '<path d="M3 6h10v10H3z"/><path d="M13 10h4l3 3v3h-7"/><circle cx="7" cy="18" r="1.4"/><circle cx="17" cy="18" r="1.4"/>',
  return: '<path d="M5 9h11a4 4 0 0 1 0 8H8"/><path d="m9 5-4 4 4 4"/>',
  chat: '<path d="M4 5h16v11H9l-5 4Z"/><path d="M8 9h8M8 12h5"/>',
  arrowRight: '<path d="M4 12h16M14 6l6 6-6 6"/>',
  arrowLeft: '<path d="M20 12H4M10 6l-6 6 6 6"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  plus: '<path d="M12 4v16M4 12h16"/>',
  minus: '<path d="M4 12h16"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  spark: '<path d="m12 3 1.8 5.6L19 11l-5.2 2.4L12 19l-1.8-5.6L5 11l5.2-2.4Z"/>',
  leaf: '<path d="M20 4C11 4 5 8 5 15c0 2 1 4 3 5 0-7 5-10 12-16Z"/><path d="M5 21c2-7 7-10 13-13"/>',
  factory: '<path d="M3 21V9l6 4V8l6 4V8l6 4v9Z"/><path d="M7 17h2M12 17h2M17 17h2"/>',
  store: '<path d="M4 10 6 4h12l2 6"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/>',
  box: '<path d="m4 8 8-4 8 4v9l-8 4-8-4Z"/><path d="m4 8 8 4 8-4M12 12v9"/>',
  card: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h4"/>',
  wallet: '<path d="M4 6h15v14H4z"/><path d="M4 9V5a2 2 0 0 1 2-2h11v3"/><path d="M15 12h4v4h-4a2 2 0 0 1 0-4Z"/>',
  mapPin: '<path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
  tag: '<path d="m3 12 9-9h7v7l-9 9Z"/><circle cx="16" cy="8" r="1.2"/>',
  package: '<path d="m4 8 8-4 8 4v9l-8 4-8-4Z"/><path d="M8 6v6l4 2 4-2V6M4 8l8 4 8-4"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14"/><path d="M10 11v6M14 11v6"/>',
  edit: '<path d="M4 20h4L20 8l-4-4L4 16Z"/><path d="m14 6 4 4"/>',
  logout: '<path d="M14 4h5v16h-5"/><path d="M3 12h12M11 8l4 4-4 4"/>',
  alert: '<path d="M12 3 2.8 20h18.4Z"/><path d="M12 9v5M12 17.5v.1"/>',
  filter: '<path d="M3 5h18l-7 8v6l-4 2v-8Z"/>',
  grid: '<rect x="4" y="4" width="6" height="6"/><rect x="14" y="4" width="6" height="6"/><rect x="4" y="14" width="6" height="6"/><rect x="14" y="14" width="6" height="6"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  headphones: '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="13" width="4" height="7" rx="2"/><rect x="17" y="13" width="4" height="7" rx="2"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.1"/>',
  camera: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="m8 7 1.5-3h5L16 7"/><circle cx="12" cy="13" r="4"/>',
  gift: '<rect x="4" y="9" width="16" height="11" rx="2"/><path d="M12 9v11M4 13h16"/><path d="M12 9C9 9 6.5 7.5 7 5.5 7.7 3 12 4.5 12 9Zm0 0c3 0 5.5-1.5 5-3.5C16.3 3 12 4.5 12 9Z"/>',
};

export function icon(name, size = 20, className = "") {
  const body = ICONS[name] || ICONS.info;
  return `<svg class="icon ${escapeHtml(className)}" width="${Number(size) || 20}" height="${Number(size) || 20}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

const CNY_FORMATTER = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2, minimumFractionDigits: 0 });
export function money(cents) {
  const value = Number(cents || 0) / 100;
  return "¥" + CNY_FORMATTER.format(value);
}

export function priceHtml(cents, { className = "", size = "" } = {}) {
  const formatted = CNY_FORMATTER.format(Number(cents || 0) / 100);
  return `<span class="price ${escapeHtml([className, size].filter(Boolean).join(" "))}"><span class="currency">¥</span><span class="amount">${formatted}</span></span>`;
}

export function ratingStars(rating) {
  const value = Math.round(Number(rating || 0));
  return `<span class="rating-stars" role="img" aria-label="${Number(rating || 0).toFixed(1)} 分">${[1,2,3,4,5].map((n) => `<span class="${n <= value ? "" : "is-empty"}" aria-hidden="true">★</span>`).join("")}</span>`;
}

export const KIND_LABELS = { self: "官方自营", brand: "品牌旗舰", factory: "源头工厂" };
export const KIND_ICONS = { self: "shield", brand: "store", factory: "factory" };

export function kindBadge(kind, extra = "") {
  return `<span class="badge badge--jade ${escapeHtml(extra)}">${icon(KIND_ICONS[kind] || "store", 11)} ${escapeHtml(KIND_LABELS[kind] || "好店")}</span>`;
}

export function productCard(card, options = {}) {
  const {
    compact = false,
    showDelivery = true,
    horizontal = false,
    badgeLimit = 2,
  } = options;
  if (!card) return document.createDocumentFragment();
  const card_ = document.createElement("article");
  card_.className = "product-card" + (compact ? " product-card--compact" : "") + (horizontal ? " product-card--horizontal" : "");
  const badges = (card.badges || []).slice(0, badgeLimit);
  const group = card.group;
  const href = `/p/${encodeURIComponent(card.slug)}`;
  const groupText = group ? `${group.size} 人团 ${money(group.price_cents)}` : "";
  const kinds = (card.offer_kinds || []).map((kind) => KIND_LABELS[kind]).filter(Boolean);
  card_.innerHTML = `
    <a class="product-card__anchor" href="${href}" data-link aria-label="查看 ${escapeHtml(card.title)}">
      <div class="product-card__art">
        <img src="${escapeHtml(card.cover)}" alt="${escapeHtml(card.cover_alt || card.title)}" loading="lazy" decoding="async">
        <div class="product-card__badge-row">
          ${badges.map((badge, index) => `<span class="badge ${index === 0 ? "badge--light" : "badge--outline"}">${escapeHtml(badge)}</span>`).join("")}
        </div>
        ${card.discount_percent > 0 ? `<span class="product-card__save">省 ${card.discount_percent}%</span>` : ""}
      </div>
      <div class="product-card__body">
        <div class="product-card__meta"><span class="product-card__brand">${escapeHtml(card.brand)}</span><span>${escapeHtml(kinds.slice(0, 2).join(" · "))}</span></div>
        <h3 class="product-card__title">${escapeHtml(card.title)}</h3>
        ${compact ? "" : `<p class="product-card__subtitle">${escapeHtml(card.subtitle)}</p>`}
        <div class="rating-line">${ratingStars(card.rating)}<span>${Number(card.rating || 0).toFixed(1)}</span><span aria-hidden="true">·</span><span>${Number(card.review_count || 0)} 条评价</span></div>
        <div class="product-card__pricing">
          ${priceHtml(card.price_cents)}
          ${card.market_price_cents > card.price_cents ? `<span class="was-price">${money(card.market_price_cents)}</span>` : ""}
        </div>
        ${group ? `<div class="product-card__group">${icon("gift", 12)} ${escapeHtml(groupText)} 包邮</div>` : ""}
        ${showDelivery && card.delivery_label ? `<div class="product-card__delivery"><span>${escapeHtml(card.delivery_label)}</span><span>${card.free_shipping ? "包邮" : ""}</span></div>` : ""}
      </div>
    </a>`;
  const img = card_.querySelector("img");
  img?.addEventListener("error", () => {
    const fallback = document.createElement("div");
    fallback.className = "product-fallback";
    fallback.textContent = card.brand || "开集";
    img.replaceWith(fallback);
  });
  return card_;
}

export function skeletonCards(count = 8) {
  return `<div class="product-grid" aria-hidden="true">${Array.from({length: count}, () => `
    <div class="product-card"><div class="skeleton" style="aspect-ratio:1"></div>
      <div class="skeleton" style="height:14px;width:46%;margin-top:14px"></div>
      <div class="skeleton" style="height:18px;width:82%;margin-top:8px"></div>
      <div class="skeleton" style="height:16px;width:38%;margin-top:10px"></div>
    </div>`).join("")}</div>`;
}

export function emptyState({ title, message, actionLabel, actionHref, iconName = "box" }) {
  return `<div class="empty-state">
    <div class="empty-state__icon">${icon(iconName, 26)}</div>
    <h3>${escapeHtml(title)}</h3>
    <p>${escapeHtml(message)}</p>
    ${actionLabel && actionHref ? `<a class="btn btn--primary btn--small empty-state__actions" href="${escapeHtml(actionHref)}" data-link>${escapeHtml(actionLabel)}</a>` : ""}
  </div>`;
}

export function sectionHead({ eyebrow, title, description, href, linkLabel = "查看全部" }) {
  return `<div class="section-head">
    <div>${eyebrow ? `<p class="eyebrow">${escapeHtml(eyebrow)}</p>` : ""}<h2>${escapeHtml(title)}</h2>${description ? `<p class="section-description">${escapeHtml(description)}</p>` : ""}</div>
    ${href ? `<a class="link-arrow section-link" href="${escapeHtml(href)}" data-link>${escapeHtml(linkLabel)} ${icon("arrowRight", 15)}</a>` : ""}
  </div>`;
}

export function toast(message, { type = "success", timeout = 3400 } = {}) {
  const region = document.getElementById("toast-region");
  if (!region) return;
  const node = document.createElement("div");
  node.className = "toast" + (type === "error" ? " toast--error" : type === "info" ? " toast--info" : "");
  node.setAttribute("role", type === "error" ? "alert" : "status");
  node.textContent = message;
  region.appendChild(node);
  window.setTimeout(() => {
    node.classList.add("is-leaving");
    window.setTimeout(() => node.remove(), 230);
  }, timeout);
}

export function formatDate(value, { withTime = false, short = false } = {}) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: short ? undefined : "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: withTime ? "2-digit" : undefined,
    minute: withTime ? "2-digit" : undefined,
    hour12: false,
  }).format(date);
}

export function debounce(fn, wait = 220) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function openModal({ title, subtitle = "", body = "", footer = "", wide = false, onClose = null, closeOnBackdrop = true } = {}) {
  const root = document.getElementById("modal-root");
  const previousFocus = document.activeElement;
  const scrim = document.createElement("div");
  scrim.className = "modal-scrim";
  scrim.innerHTML = `<section class="modal ${wide ? "modal--wide" : ""}" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <header class="modal__header"><div><h2 class="modal__title" id="modal-title">${escapeHtml(title)}</h2>${subtitle ? `<p class="modal__subtitle">${escapeHtml(subtitle)}</p>` : ""}</div><button class="icon-btn modal__close" type="button" aria-label="关闭">${icon("x", 20)}</button></header>
    <div class="modal__body"></div>
    <div class="modal__footer"></div>
  </section>`;
  const dialog = scrim.querySelector(".modal");
  const bodyEl = scrim.querySelector(".modal__body");
  const footerEl = scrim.querySelector(".modal__footer");
  const closeBtn = scrim.querySelector(".modal__close");
  if (body instanceof Node) bodyEl.appendChild(body); else bodyEl.innerHTML = body;
  if (footer instanceof Node) footerEl.appendChild(footer); else footerEl.innerHTML = footer;
  if (!footerEl.childNodes.length) footerEl.hidden = true;
  root.replaceChildren(scrim);
  root.hidden = false;
  document.body.classList.add("has-modal");
  let closed = false;
  const close = (value = undefined) => {
    if (closed) return;
    closed = true;
    document.removeEventListener("keydown", onKey, true);
    scrim.remove();
    document.body.classList.remove("has-modal");
    if (previousFocus && previousFocus.focus) previousFocus.focus();
    if (onClose) onClose(value);
  };
  const onKey = (event) => {
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const items = [...dialog.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  closeBtn.addEventListener("click", () => close());
  scrim.addEventListener("click", (event) => { if (closeOnBackdrop && event.target === scrim) close(); });
  document.addEventListener("keydown", onKey, true);
  requestAnimationFrame(() => {
    const target = bodyEl.querySelector("input, select, textarea, button:not([disabled])") || closeBtn;
    target.focus({ preventScroll: true });
  });
  return { root: scrim, dialog, body: bodyEl, footer: footerEl, close };
}

export function openConfirm({ title, message, confirmLabel = "确认", cancelLabel = "取消", danger = false }) {
  return new Promise((resolve) => {
    let decided = false;
    const modal = openModal({
      title,
      subtitle: message,
      body: "",
      footer: `<button class="btn btn--ghost" type="button" data-cancel>${escapeHtml(cancelLabel)}</button><button class="btn ${danger ? "btn--primary" : "btn--primary"}" type="button" data-confirm>${escapeHtml(confirmLabel)}</button>`,
      onClose: () => { if (!decided) resolve(false); },
    });
    modal.footer.querySelector("[data-cancel]").addEventListener("click", () => { decided = true; modal.close(); resolve(false); });
    modal.footer.querySelector("[data-confirm]").addEventListener("click", () => { decided = true; modal.close(); resolve(true); });
  });
}

export function bindAuthTab(container) {
  const tabs = container.querySelectorAll("[data-auth-tab]");
  const panes = container.querySelectorAll("[data-auth-pane]");
  tabs.forEach((tab) => tab.addEventListener("click", () => {
    tabs.forEach((item) => { item.classList.toggle("is-active", item === tab); item.setAttribute("aria-selected", item === tab ? "true" : "false"); });
    panes.forEach((pane) => { pane.hidden = pane.dataset.authPane !== tab.dataset.authTab; });
  }));
}

export function openAuth({ reason = "", onSuccess = null, preferSeller = false } = {}) {
  const body = document.createElement("div");
  body.className = "auth-panel";
  body.innerHTML = `
    <div class="auth-intro"><span class="eyebrow">${preferSeller ? "商家演示" : "欢迎来到开集"}</span>
      <h3>${preferSeller ? "看看一间好店的后台" : "把喜欢的东西先放进购物车"}</h3>
      <p>${escapeHtml(reason || "登录后可跨设备保留购物车、地址与订单。演示环境不会真实扣款。")}</p>
    </div>
    <div class="auth-demos">
      <button class="demo-account" type="button" data-demo="buyer"><span class="demo-avatar">林</span><span><b>买家演示 · 林澄</b><em>已有历史订单与地址</em></span><span aria-hidden="true">→</span></button>
      <button class="demo-account demo-account--seller" type="button" data-demo="seller"><span class="demo-avatar">周</span><span><b>商家演示 · 周屿</b><em>OpenShop 自营 · 处理发货</em></span><span aria-hidden="true">→</span></button>
    </div>
    <div class="auth-separator"><span>或使用账号</span></div>
    <div class="auth-tabs" role="tablist" aria-label="登录或注册">
      <button class="auth-tab is-active" type="button" role="tab" aria-selected="true" data-auth-tab="login">登录</button>
      <button class="auth-tab" type="button" role="tab" aria-selected="false" data-auth-tab="register">注册</button>
    </div>
    <div data-auth-pane="login">
      <form class="auth-form" data-auth-form="login" novalidate>
        <label class="field"><span class="field-label">邮箱或账号</span><input class="input" name="identifier" type="text" autocomplete="username" required maxlength="120" placeholder="buyer@example.com"></label>
        <label class="field"><span class="field-label">密码</span><input class="input" name="password" type="password" autocomplete="current-password" required minlength="8" maxlength="72" placeholder="至少 8 位"></label>
        <p class="form-error" data-auth-error role="alert" hidden></p>
        <button class="btn btn--primary btn--block auth-submit" type="submit">登录开集</button>
      </form>
    </div>
    <div data-auth-pane="register" hidden>
      <form class="auth-form" data-auth-form="register" novalidate>
        <label class="field"><span class="field-label">怎么称呼你</span><input class="input" name="display_name" type="text" autocomplete="nickname" required minlength="2" maxlength="24" placeholder="2-24 个字符"></label>
        <label class="field"><span class="field-label">邮箱</span><input class="input" name="email" type="email" autocomplete="email" required maxlength="120" placeholder="name@example.com"></label>
        <label class="field"><span class="field-label">设置密码</span><input class="input" name="password" type="password" autocomplete="new-password" required minlength="8" maxlength="72" placeholder="8-72 位，首尾无空格"></label>
        <p class="field-hint">真实开源部署请使用自己的账号体系；这里仅创建本地演示账号。</p>
        <p class="form-error" data-auth-error role="alert" hidden></p>
        <button class="btn btn--primary btn--block auth-submit" type="submit">注册并登录</button>
      </form>
    </div>
    <p class="auth-footnote">演示用户无需密码。所有商品、价格与支付均为虚构演示数据。</p>`;
  const modal = openModal({ title: "进开集，慢慢挑", subtitle: "一次登录，购物车、地址与订单都有归处。", body, wide: false });
  bindAuthTab(body);

  const setBusy = (button, busy, label) => {
    button.disabled = busy;
    if (busy) { button.dataset.original = button.textContent; button.innerHTML = '<span class="spinner" aria-hidden="true"></span> 稍等'; }
    else { button.textContent = label || button.dataset.original || "继续"; }
  };

  const complete = async (session) => {
    applySession(session);
    modal.close();
    toast(session.user?.role === "seller" ? "已进入商家演示" : (session.user ? `欢迎回来，${session.user.display_name}` : "已登录"));
    if (typeof onSuccess === "function") await onSuccess(session);
  };

  const showError = (form, message, fields = {}) => {
    const target = form.querySelector("[data-auth-error]");
    target.textContent = message;
    target.hidden = false;
    for (const field of form.querySelectorAll("[name]")) {
      const error = fields[field.name];
      field.setAttribute("aria-invalid", error ? "true" : "false");
      let hint = field.parentElement.querySelector(".field-error");
      if (error) {
        if (!hint) { hint = document.createElement("span"); hint.className = "field-error"; field.parentElement.appendChild(hint); }
        hint.textContent = error;
      } else if (hint) hint.remove();
    }
  };

  const tryDemo = async (button, role) => {
    setBusy(button, true);
    button.parentElement.querySelectorAll("button").forEach((el) => { el.disabled = true; });
    try {
      const session = await api.post("/api/auth/demo", { role });
      await complete(session);
    } catch (err) {
      button.parentElement.querySelectorAll("button").forEach((el) => { el.disabled = false; });
      setBusy(button, false);
      toast(err instanceof ApiError ? err.message : "演示登录失败", { type: "error" });
    }
  };
  body.querySelectorAll("[data-demo]").forEach((button) => button.addEventListener("click", () => tryDemo(button, button.dataset.demo)));

  for (const form of body.querySelectorAll("[data-auth-form]")) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = form.querySelector("button[type=submit]");
      const values = Object.fromEntries(new FormData(form).entries());
      const errorEl = form.querySelector("[data-auth-error]");
      errorEl.hidden = true;
      setBusy(submit, true);
      try {
        let session;
        if (form.dataset.authForm === "login") session = await api.post("/api/auth/login", values);
        else session = await api.post("/api/auth/register", values);
        await complete(session);
      } catch (err) {
        setBusy(submit, false);
        showError(form, err instanceof ApiError ? err.message : "请求失败，请稍后再试", err instanceof ApiError ? err.fields : {});
        errorEl.focus?.();
      }
    });
  }
  if (preferSeller) {
    const seller = body.querySelector('[data-demo="seller"]');
    seller?.classList.add("is-featured");
  }
  return modal;
}
