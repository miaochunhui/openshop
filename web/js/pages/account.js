// Buyer account: orders, address CRUD, coupons, favorites and demo logout.
import { api, ApiError } from "../core/api.js";
import { logout as logoutSession, state } from "../core/store.js";
import {
  emptyState,
  escapeHtml,
  icon,
  money,
  openAuth,
  openConfirm,
  openModal,
  priceHtml,
  toast,
} from "../components/index.js";

const TABS = [
  { key: "orders", label: "订单", testid: "account-tab-orders" },
  { key: "addresses", label: "地址", testid: "account-tab-addresses" },
  { key: "coupons", label: "优惠券", testid: "account-tab-coupons" },
  { key: "favorites", label: "收藏", testid: "account-tab-favorites" },
  { key: "profile", label: "资料", testid: "account-tab-profile" },
];

const ORDER_FILTERS = [
  { key: "all", label: "全部" },
  { key: "unpaid", label: "待付款" },
  { key: "to_ship", label: "待发货" },
  { key: "to_receive", label: "待收货" },
  { key: "completed", label: "已完成" },
  { key: "after_sale", label: "售后" },
];

const ERROR_HINTS = {
  AUTH_REQUIRED: "登录状态已失效，请重新登录。",
  COUPON_EXHAUSTED: "这张券已经领完了。",
  COUPON_ALREADY_CLAIMED: "这张券已经在你的券包里。",
  VALIDATION_ERROR: "请检查填写的信息。",
};

function errMessage(err, fallback = "操作没有完成，请稍后再试。") {
  if (err instanceof ApiError) {
    const hint = ERROR_HINTS[err.code];
    return hint && hint !== err.message ? `${err.message} ${hint}` : err.message;
  }
  return fallback;
}

function dateText(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const options = { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" };
  if (withTime) { options.hour = "2-digit"; options.minute = "2-digit"; options.hour12 = false; }
  return new Intl.DateTimeFormat("zh-CN", options).format(date);
}

function dateOnly(value) {
  return value ? String(value).slice(0, 10) : "—";
}

function amountHtml(cents) {
  return Number.isFinite(Number(cents)) && cents !== null
    ? priceHtml(cents)
    : '<span class="muted">—</span>';
}

function orderHref(orderNo) {
  return `/orders/${encodeURIComponent(String(orderNo || ""))}`;
}

function statusClass(status) {
  const known = new Set(["PENDING_PAYMENT", "GROUP_FORMING", "PAID", "SHIPPED", "COMPLETED", "CANCELLED", "REFUNDING", "REFUNDED"]);
  return known.has(status) ? `order-status--${String(status).toLowerCase()}` : "";
}

function addressText(address) {
  return [address.province, address.city, address.district, address.detail].filter(Boolean).join(" ");
}

function orderCardHtml(order) {
  const previews = Array.isArray(order.preview_items) ? order.preview_items : [];
  const group = order.group;
  const thumbs = previews.slice(0, 3).map((item) => `<div class="account-order__thumb"><img src="${escapeHtml(item.cover || "")}" alt="" loading="lazy" decoding="async"><span class="sr-only">${escapeHtml(item.title || "")}</span></div>`).join("");
  const previewLine = previews.map((item) => `${escapeHtml(item.title || "商品")} <span class="tiny muted">×${Number(item.quantity) || 1}</span>`).join("　");
  return `<article class="account-order" data-testid="order-item" data-order-no="${escapeHtml(String(order.order_no || ""))}">
    <header class="account-order__head">
      <div class="account-order__ids">
        <span class="tiny muted">${dateText(order.created_at, true)}</span>
        <b class="account-order__no">${escapeHtml(order.order_no || "")}</b>
      </div>
      <span class="order-status ${statusClass(order.status)}">${escapeHtml(order.status_label || "状态待确认")}</span>
    </header>
    <div class="account-order__body">
      <div class="account-order__thumbs">${thumbs || `<div class="account-order__thumb account-order__thumb--empty">${icon("box", 22)}</div>`}</div>
      <div class="account-order__summary">
        <p class="account-order__items">${previewLine || "商品明细以订单详情为准"}</p>
        <p class="tiny muted">${Number(order.item_count) || 0} 件商品 · ${Number(order.shop_count) || 0} 个店铺</p>
        ${group ? `<p class="tiny muted">拼团 ${escapeHtml(group.code || "")} · ${Number(group.joined_count) || 0}/${Number(group.required_size) || 0} · ${escapeHtml(group.status || "")}</p>` : ""}
      </div>
      <div class="account-order__total"><span class="tiny muted">订单金额</span>${amountHtml(order.total_cents)}</div>
    </div>
    <footer class="account-order__foot">
      <span class="tiny muted">金额、状态与可执行操作均由服务端决定</span>
      <a class="btn btn--small btn--ghost" href="${orderHref(order.order_no)}" data-link>查看订单 ${icon("arrowRight", 14)}</a>
    </footer>
  </article>`;
}

export async function render(ctx) {
  const rawTab = ctx.query.get("tab") || "orders";
  const activeTab = TABS.some((tab) => tab.key === rawTab) ? rawTab : "orders";
  const tabMeta = TABS.find((tab) => tab.key === activeTab);
  ctx.setTitle(`${tabMeta.label} · 我的开集 · OpenShop 开集`);

  let loadSeq = 0;
  let ordersFilter = "all";
  let ordersState = { items: [], counts: {}, page: 1, pages: 1, total: 0, loading: false, loadingMore: false, error: "" };
  let addresses = [];
  let logoutBusy = false;

  const tabsHtml = () => TABS.map((tab) => `<a class="tab ${tab.key === activeTab ? "is-active" : ""}" href="/account?tab=${tab.key}" data-link data-testid="${tab.testid}" ${tab.key === activeTab ? 'aria-current="page"' : ""}>${tab.label}</a>`).join("");

  function pane() {
    return ctx.root.querySelector("#account-pane");
  }

  function setPane(html, busy = false) {
    const target = pane();
    if (!target) return;
    target.innerHTML = html;
    target.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function showAlert(message) {
    const box = ctx.root.querySelector("#account-alert");
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
    box.focus?.();
  }

  function clearAlert() {
    const box = ctx.root.querySelector("#account-alert");
    if (!box) return;
    box.hidden = true;
    box.textContent = "";
  }

  function skeletonPane(label) {
    return `<div class="account-pane-skeleton" aria-hidden="true">
      <div class="skeleton" style="height:26px;width:34%"></div>
      <div class="skeleton mt-3" style="height:132px;border-radius:var(--radius)"></div>
      <div class="skeleton mt-2" style="height:132px;border-radius:var(--radius)"></div>
    </div><p class="sr-only" role="status">正在读取${escapeHtml(label)}</p>`;
  }

  function bindRetry(loader) {
    const retry = ctx.root.querySelector("[data-retry-tab]");
    retry?.addEventListener("click", loader || (() => { void loadActiveTab(); }));
  }

  function errorPane(message, loader) {
    setPane(`<div class="account-error trade-card"><p class="eyebrow">COULD NOT LOAD</p><h2>这张桌子暂时没摆好</h2><p class="error-inline" role="alert">${escapeHtml(message)}</p><button class="btn btn--primary btn--small mt-2" type="button" data-retry-tab>重新读取</button></div>`);
    bindRetry(loader);
  }

  function guestPane() {
    return `<div class="trade-lock trade-card">
      <div class="trade-lock__icon">${icon("user", 28)}</div>
      <span class="eyebrow eyebrow--jade">YOUR OPENSHOP</span>
      <h2>登录后，订单才有归处</h2>
      <p>登录后可查看订单与包裹、管理地址、领取优惠券和收藏；演示账号支持一键登录，不会真实扣款。</p>
      <div class="cluster mt-3"><button class="btn btn--primary" type="button" data-login>${icon("user", 16)} 一键买家登录</button><a class="btn btn--ghost" href="/search" data-link>先逛逛</a></div>
      <p class="tiny muted">游客购物车会在登录后自动合并，不会丢失。</p>
    </div>`;
  }

  function bindLogin() {
    ctx.root.querySelector("[data-login]")?.addEventListener("click", () => {
      openAuth({
        reason: "登录后可查看订单、地址与优惠券。买家演示无需密码，不会真实付款。",
        onSuccess: () => { if (!ctx.signal.aborted) void render(ctx); },
      });
    });
  }

  async function handleLogout(button) {
    if (logoutBusy) return;
    const decision = openConfirm({
      title: "退出当前账号？",
      message: "退出后订单仍会保留在服务端；再次登录即可继续查看。演示环境不影响真实资产。",
      confirmLabel: "退出登录",
      cancelLabel: "继续逛逛",
    });
    const confirmButton = document.querySelector("#modal-root .modal__footer .btn--primary");
    const cancelButton = document.querySelectorAll("#modal-root .modal__footer .btn")[0];
    if (confirmButton) confirmButton.setAttribute("data-testid", "logout-confirm");
    if (cancelButton && cancelButton !== confirmButton) cancelButton.setAttribute("data-testid", "logout-confirm-cancel");
    const confirmed = await decision;
    if (!confirmed || ctx.signal.aborted) return;
    logoutBusy = true;
    const previous = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner" aria-hidden="true"></span> 退出中';
    try {
      await logoutSession();
      if (ctx.signal.aborted) return;
      toast("已退出登录，购物车仍留在游客会话里");
      ctx.navigate("/");
    } catch (err) {
      if (ctx.signal.aborted) return;
      logoutBusy = false;
      button.disabled = false;
      button.innerHTML = previous;
      showAlert(errMessage(err, "退出登录没有完成，请稍后再试。"));
    }
  }

  function paintShell() {
    const user = state.user;
    ctx.root.innerHTML = `<section class="container trade-page account-page" data-testid="account-root" aria-labelledby="account-heading">
      <header class="trade-head account-head">
        <div>
          <p class="eyebrow">MY OPENSHOP · ${user ? "欢迎回来" : "登录入口"}</p>
          <h1 id="account-heading" tabindex="-1">我的开集</h1>
          <p class="lede trade-head__lede">${user ? `${escapeHtml(user.display_name || "朋友")}，从这里查看订单、地址、优惠券与收藏。` : "把订单、地址与喜欢的摊位，收在同一个地方。"}</p>
        </div>
        <div class="cluster account-head__actions">
          ${user ? `<button class="btn btn--ghost btn--small" type="button" data-testid="logout-button" data-logout>${icon("logout", 15)} 退出登录</button>` : ""}
          <a class="btn btn--ghost btn--small" href="/search" data-link>继续逛逛 ${icon("arrowRight", 14)}</a>
        </div>
      </header>
      <div class="account-shell">
        <nav class="tabs account-tabs" aria-label="我的开集分区">${tabsHtml()}</nav>
        <div class="error-inline account-alert" id="account-alert" role="alert" tabindex="-1" hidden></div>
        <div id="account-pane" class="account-pane" aria-busy="true">${skeletonPane(tabMeta.label)}</div>
      </div>
    </section>`;
    ctx.root.querySelector("[data-logout]")?.addEventListener("click", (event) => {
      if (event.currentTarget instanceof HTMLElement) void handleLogout(event.currentTarget);
    });
    bindLogin();
  }

  function orderFiltersHtml() {
    return ORDER_FILTERS.map((filter) => {
      const raw = ordersState.counts?.[filter.key];
      const count = Number(raw);
      const countText = raw !== undefined && Number.isFinite(count) ? `<span class="order-filter__count">${count}</span>` : "";
      return `<button class="order-filter ${filter.key === ordersFilter ? "is-active" : ""}" type="button" data-order-filter="${filter.key}" aria-pressed="${filter.key === ordersFilter ? "true" : "false"}">${filter.label}${countText}</button>`;
    }).join("");
  }

  function renderOrdersPane() {
    let listHtml;
    if (ordersState.loading && !ordersState.items.length) {
      listHtml = `<div class="account-pane-skeleton" aria-hidden="true"><div class="skeleton" style="height:150px;border-radius:var(--radius)"></div><div class="skeleton mt-2" style="height:150px;border-radius:var(--radius)"></div></div><p class="sr-only" role="status">正在读取订单</p>`;
    } else if (ordersState.error && !ordersState.items.length) {
      listHtml = `<div class="trade-card account-error"><p class="error-inline" role="alert">${escapeHtml(ordersState.error)}</p><button class="btn btn--primary btn--small mt-2" type="button" data-orders-retry>重新读取</button></div>`;
    } else if (!ordersState.items.length) {
      listHtml = emptyState({
        title: "这个筛选下还没有订单",
        message: "去集市看看，选好商品后会在这里看到订单与服务端状态。",
        actionLabel: "去逛逛",
        actionHref: "/search",
        iconName: "box",
      });
    } else {
      listHtml = ordersState.items.map(orderCardHtml).join("");
    }
    setPane(`<section class="account-section" aria-labelledby="account-orders-title">
      <div class="account-pane-head">
        <div><p class="eyebrow">ORDERS</p><h2 id="account-orders-title">我的订单</h2><p>状态、金额与下一步操作以服务端为准。</p></div>
        <span class="tiny muted">共 ${Number(ordersState.total) || ordersState.items.length} 笔</span>
      </div>
      <div class="order-filters" role="group" aria-label="订单状态筛选">${orderFiltersHtml()}</div>
      <div class="orders-list" data-testid="orders-list">
        ${ordersState.error && ordersState.items.length ? `<p class="error-inline mt-2" role="alert">${escapeHtml(ordersState.error)} <button class="btn btn--small btn--quiet" type="button" data-orders-retry>重试</button></p>` : ""}
        ${listHtml}
        ${ordersState.page < ordersState.pages && !ordersState.loading && !ordersState.loadingMore ? '<button class="btn btn--ghost btn--block orders-more" type="button" data-orders-more>加载更多订单</button>' : ""}
        ${ordersState.loadingMore ? '<p class="account-loading-more" role="status"><span class="spinner" aria-hidden="true"></span> 正在加载更多订单</p>' : ""}
      </div>
    </section>`, ordersState.loading || ordersState.loadingMore);
    ctx.root.querySelectorAll("[data-order-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        if (ordersState.loading || button.dataset.orderFilter === ordersFilter) return;
        ordersFilter = button.dataset.orderFilter || "all";
        ordersState.items = [];
        ordersState.error = "";
        ordersState.page = 1;
        ordersState.pages = 1;
        void loadOrdersPage(1, false);
      });
    });
    ctx.root.querySelector("[data-orders-retry]")?.addEventListener("click", () => {
      ordersState.error = "";
      void loadOrdersPage(1, false);
    });
    ctx.root.querySelector("[data-orders-more]")?.addEventListener("click", (event) => {
      if (event.currentTarget instanceof HTMLButtonElement) event.currentTarget.disabled = true;
      void loadOrdersPage(ordersState.page + 1, true);
    });
  }

  async function loadOrdersPage(page, append) {
    if (!state.user) return;
    const seq = ++loadSeq;
    ordersState.loading = !append;
    ordersState.loadingMore = append;
    ordersState.error = "";
    renderOrdersPane();
    try {
      const data = await api.get(`/api/orders?tab=${encodeURIComponent(ordersFilter)}&page=${page}`, { signal: ctx.signal });
      if (ctx.signal.aborted || seq !== loadSeq) return;
      if (!data || !Array.isArray(data.items)) throw new Error("订单列表响应缺少 items");
      ordersState.items = append ? ordersState.items.concat(data.items) : data.items;
      ordersState.counts = data.counts && typeof data.counts === "object" ? data.counts : {};
      ordersState.page = Number(data.page) || page;
      ordersState.pages = Number(data.pages) || 1;
      ordersState.total = Number(data.total) || ordersState.items.length;
      ordersState.loading = false;
      ordersState.loadingMore = false;
      renderOrdersPane();
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError" || seq !== loadSeq) return;
      ordersState.loading = false;
      ordersState.loadingMore = false;
      ordersState.error = errMessage(err, "订单暂时没有读出来。");
      renderOrdersPane();
    }
  }

  async function loadOrdersTab() {
    ordersState = { ...ordersState, items: [], loading: true, loadingMore: false, error: "" };
    renderOrdersPane();
    await loadOrdersPage(1, false);
  }

  function addressCardHtml(address, defaultId) {
    const isDefault = String(address.id) === String(defaultId) || Boolean(address.is_default);
    return `<article class="address-card" data-address-id="${escapeHtml(String(address.id))}">
      <div class="cluster"><b>${escapeHtml(address.receiver || "收件人")}</b><span class="muted">${escapeHtml(address.phone || "")}</span>${isDefault ? '<span class="badge badge--jade">默认地址</span>' : ""}${address.label ? `<span class="chip">${escapeHtml(address.label)}</span>` : ""}</div>
      <p class="address-card__line">${escapeHtml(addressText(address))}</p>
      <div class="address-card__actions">
        <button class="btn btn--small btn--ghost" type="button" data-address-edit="${escapeHtml(String(address.id))}">编辑</button>
        ${isDefault ? "" : `<button class="btn btn--small btn--ghost" type="button" data-address-default="${escapeHtml(String(address.id))}">设为默认</button>`}
        <button class="btn btn--small btn--quiet text-accent" type="button" data-address-delete="${escapeHtml(String(address.id))}">删除</button>
      </div>
    </article>`;
  }

  function bindAddressActions() {
    ctx.root.querySelector("[data-address-add]")?.addEventListener("click", () => openAddressModal(null));
    ctx.root.querySelectorAll("[data-address-edit]").forEach((button) => {
      button.addEventListener("click", () => {
        const address = addresses.find((entry) => String(entry.id) === String(button.dataset.addressEdit));
        if (address) openAddressModal(address);
      });
    });
    ctx.root.querySelectorAll("[data-address-default]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.innerHTML = '<span class="spinner" aria-hidden="true"></span> 设置中';
        try {
          await api.patch(`/api/addresses/${encodeURIComponent(button.dataset.addressDefault)}`, { is_default: true }, { signal: ctx.signal });
          if (ctx.signal.aborted) return;
          toast("默认地址已更新");
          await loadAddressesTab();
        } catch (err) {
          if (ctx.signal.aborted) return;
          button.disabled = false;
          button.textContent = "设为默认";
          showAlert(errMessage(err, "默认地址没有更新成功。"));
        }
      });
    });
    ctx.root.querySelectorAll("[data-address-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        const id = button.dataset.addressDelete;
        const address = addresses.find((entry) => String(entry.id) === String(id));
        const confirmed = await openConfirm({
          title: "删除这个收货地址？",
          message: address ? `${address.receiver} · ${addressText(address)}。历史订单中的地址快照不会改变。` : "历史订单中的地址快照不会改变。",
          confirmLabel: "删除地址",
          cancelLabel: "保留",
          danger: true,
        });
        if (!confirmed || ctx.signal.aborted) return;
        button.disabled = true;
        try {
          await api.del(`/api/addresses/${encodeURIComponent(id)}`, { signal: ctx.signal });
          if (ctx.signal.aborted) return;
          toast("地址已删除");
          await loadAddressesTab();
        } catch (err) {
          if (ctx.signal.aborted) return;
          button.disabled = false;
          showAlert(errMessage(err, "地址没有删除成功。"));
        }
      });
    });
  }

  function renderAddressesPane(defaultId) {
    const cards = addresses.map((address) => addressCardHtml(address, defaultId)).join("");
    setPane(`<section class="account-section" aria-labelledby="account-addresses-title">
      <div class="account-pane-head">
        <div><p class="eyebrow">ADDRESS BOOK</p><h2 id="account-addresses-title">收货地址</h2><p>地址修改只影响之后的订单，已发生订单保留原有快照。</p></div>
        <button class="btn btn--primary btn--small" type="button" data-testid="address-add" data-address-add>+ 新增地址</button>
      </div>
      ${cards ? `<div class="address-grid">${cards}</div>` : emptyState({ title: "还没有收货地址", message: "添加一个地址，结算时就能直接选用。演示电话请使用 11 位大陆手机号。", iconName: "mapPin" })}
    </section>`);
    bindAddressActions();
  }

  async function loadAddressesTab() {
    const seq = ++loadSeq;
    setPane(skeletonPane("地址"), true);
    try {
      const data = await api.get("/api/addresses", { signal: ctx.signal });
      if (ctx.signal.aborted || seq !== loadSeq) return;
      addresses = Array.isArray(data?.items) ? data.items : [];
      renderAddressesPane(data?.default_id ?? null);
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError" || seq !== loadSeq) return;
      errorPane(errMessage(err, "地址暂时没有读出来。"), () => { void loadAddressesTab(); });
    }
  }

  function openAddressModal(address) {
    const editing = Boolean(address);
    const form = document.createElement("form");
    form.className = "trade-form";
    form.setAttribute("data-testid", "address-form");
    form.innerHTML = `<div class="form-grid form-grid--2">
      <label class="field"><span class="field-label">收件人</span><input class="input" data-testid="recipient-input" name="receiver" required minlength="2" maxlength="20" autocomplete="name" value="${escapeHtml(address?.receiver || "")}" placeholder="2-20 个字"></label>
      <label class="field"><span class="field-label">手机号</span><input class="input" data-testid="phone-input" name="phone" type="tel" inputmode="numeric" required pattern="1[3-9][0-9]{9}" maxlength="11" title="请输入 11 位大陆手机号" autocomplete="tel" value="${escapeHtml(address?.phone || "")}" placeholder="11 位演示手机号"></label>
      <label class="field"><span class="field-label">省</span><input class="input" data-testid="province-input" name="province" required maxlength="20" autocomplete="address-level1" value="${escapeHtml(address?.province || "")}" placeholder="上海市"></label>
      <label class="field"><span class="field-label">市</span><input class="input" data-testid="city-input" name="city" required maxlength="20" autocomplete="address-level2" value="${escapeHtml(address?.city || "")}" placeholder="上海市"></label>
      <label class="field"><span class="field-label">区 / 县</span><input class="input" data-testid="district-input" name="district" required maxlength="20" autocomplete="address-level3" value="${escapeHtml(address?.district || "")}" placeholder="徐汇区"></label>
      <label class="field"><span class="field-label">详细地址</span><input class="input" data-testid="detail-input" name="detail" required minlength="5" maxlength="120" autocomplete="street-address" value="${escapeHtml(address?.detail || "")}" placeholder="街道、门牌号、楼栋与房间"></label>
      <label class="field"><span class="field-label">地址标签 <span class="tiny muted">选填</span></span><input class="input" data-testid="address-label" name="label" maxlength="10" value="${escapeHtml(address?.label || "")}" placeholder="家 / 公司"></label>
    </div>
    <label class="check"><input type="checkbox" name="is_default" value="1" ${address?.is_default ? "checked" : ""}> 设为默认地址</label>
    <p class="form-error" role="alert" tabindex="-1" hidden></p>
    <div class="trade-form__actions"><button class="btn btn--ghost" type="button" data-cancel>取消</button><button class="btn btn--primary" type="submit" data-testid="address-save" data-save>${editing ? "保存修改" : "保存地址"}</button></div>`;
    const errorBox = form.querySelector(".form-error");
    let saving = false;
    const modal = openModal({
      title: editing ? "编辑收货地址" : "新增收货地址",
      subtitle: "仅用于演示履约；历史订单中的地址为快照。",
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
        if (editing) await api.patch(`/api/addresses/${encodeURIComponent(String(address.id))}`, body, { signal: ctx.signal });
        else await api.post("/api/addresses", body, { signal: ctx.signal });
        if (ctx.signal.aborted) return;
        modal.close();
        toast(editing ? "地址已更新" : "地址已保存");
        await loadAddressesTab();
      } catch (err) {
        if (ctx.signal.aborted) return;
        saving = false;
        save.disabled = false;
        save.textContent = editing ? "保存修改" : "保存地址";
        errorBox.textContent = errMessage(err, "地址没有保存成功。");
        errorBox.hidden = false;
        errorBox.focus?.();
      }
    });
    requestAnimationFrame(() => form.querySelector("input")?.focus());
  }

  function couponMineHtml(coupon) {
    const status = coupon.status || "";
    const statusLabel = status === "AVAILABLE" ? "可使用" : status === "USED" ? "已使用" : status === "EXPIRED" ? "已过期" : status || "状态未知";
    return `<article class="coupon-ticket ${status === "AVAILABLE" ? "" : "coupon-ticket--muted"}">
      <div class="coupon-ticket__value"><span class="coupon-ticket__amount">${amountHtml(coupon.discount_cents)}</span><span class="tiny">满 ${money(coupon.threshold_cents)} 可用</span></div>
      <div class="coupon-ticket__body">
        <div class="between"><b>${escapeHtml(coupon.title || coupon.code || "优惠券")}</b><span class="chip ${status === "AVAILABLE" ? "chip--jade" : "chip--ghost"}">${escapeHtml(statusLabel)}</span></div>
        <p>${escapeHtml(coupon.description || "")}</p>
        <span class="tiny muted">${coupon.expires_at ? `有效期至 ${dateText(coupon.expires_at)}` : "有效期以领取记录为准"} · ${escapeHtml(coupon.code || "")}</span>
      </div>
    </article>`;
  }

  function couponAvailableHtml(coupon) {
    const claimed = coupon.claimed === true;
    const claimable = coupon.claimable === true && !claimed;
    const soldOut = Number(coupon.remaining) === 0;
    const label = claimed ? "已在券包" : soldOut ? "已领完" : claimable ? "立即领取" : "暂不可领";
    const action = claimable
      ? `<button class="btn btn--primary btn--small" type="button" data-coupon-claim="${escapeHtml(coupon.code || "")}">${label}</button>`
      : `<span class="chip ${claimed ? "chip--jade" : "chip--ghost"}">${label}</span>`;
    return `<article class="coupon-ticket coupon-ticket--available">
      <div class="coupon-ticket__value"><span class="coupon-ticket__amount">${amountHtml(coupon.discount_cents)}</span><span class="tiny">满 ${money(coupon.threshold_cents)} 可用</span></div>
      <div class="coupon-ticket__body">
        <b>${escapeHtml(coupon.title || coupon.code || "优惠券")}</b>
        <p>${escapeHtml(coupon.description || "")}</p>
        <span class="tiny muted">${coupon.expires_at ? `有效期至 ${dateText(coupon.expires_at)}` : "领取后以券面有效期为准"}${coupon.remaining !== null && coupon.remaining !== undefined && Number.isFinite(Number(coupon.remaining)) ? ` · 剩余 ${Number(coupon.remaining)} 张` : ""}</span>
        <div class="coupon-ticket__actions">${action}</div>
      </div>
    </article>`;
  }

  function bindCouponActions() {
    ctx.root.querySelectorAll("[data-coupon-claim]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.innerHTML = '<span class="spinner" aria-hidden="true"></span> 领取中';
        try {
          await api.post(`/api/coupons/${encodeURIComponent(button.dataset.couponClaim)}/claim`, {}, { signal: ctx.signal });
          if (ctx.signal.aborted) return;
          toast("优惠券已放入券包");
          await loadCouponsTab();
        } catch (err) {
          if (ctx.signal.aborted) return;
          button.disabled = false;
          button.textContent = "重新领取";
          showAlert(errMessage(err, "优惠券没有领取成功。"));
        }
      });
    });
  }

  function renderCouponsPane(data) {
    const mine = Array.isArray(data?.mine) ? data.mine : [];
    const available = Array.isArray(data?.available) ? data.available : [];
    setPane(`<section class="account-section" aria-labelledby="account-coupons-title">
      <div class="account-pane-head"><div><p class="eyebrow">COUPONS</p><h2 id="account-coupons-title">优惠券</h2><p>优惠的适用条件由服务端在报价时复核。</p></div><a class="btn btn--ghost btn--small" href="/search" data-link>去逛逛</a></div>
      <h3 class="account-subhead">我的券包 <span class="tiny muted">${mine.length} 张</span></h3>
      ${mine.length ? `<div class="coupon-list">${mine.map(couponMineHtml).join("")}</div>` : '<p class="muted small account-blank">还没有领取优惠券。</p>'}
      <h3 class="account-subhead mt-4">可领取 <span class="tiny muted">${available.length} 张</span></h3>
      ${available.length ? `<div class="coupon-list">${available.map(couponAvailableHtml).join("")}</div>` : '<p class="muted small account-blank">当前没有可领取的优惠券。</p>'}
    </section>`);
    bindCouponActions();
  }

  async function loadCouponsTab() {
    const seq = ++loadSeq;
    setPane(skeletonPane("优惠券"), true);
    try {
      const data = await api.get("/api/coupons", { signal: ctx.signal });
      if (ctx.signal.aborted || seq !== loadSeq) return;
      if (!data || !Array.isArray(data.mine)) throw new Error("优惠券响应缺少 mine");
      renderCouponsPane(data);
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError" || seq !== loadSeq) return;
      errorPane(errMessage(err, "优惠券暂时没有读出来。"), () => { void loadCouponsTab(); });
    }
  }

  function favoriteCardHtml(card) {
    const was = Number(card.market_price_cents) > Number(card.price_cents) ? `<span class="was-price">${money(card.market_price_cents)}</span>` : "";
    return `<article class="favorite-card" data-favorite-slug="${escapeHtml(card.slug || "")}">
      <a class="favorite-card__art" href="/p/${encodeURIComponent(card.slug || "")}" data-link>
        <img src="${escapeHtml(card.cover || "")}" alt="${escapeHtml(card.cover_alt || card.title || "商品")}" loading="lazy" decoding="async">
      </a>
      <div class="favorite-card__body">
        <p class="eyebrow eyebrow--jade">${escapeHtml(card.brand || "开集")}</p>
        <a class="favorite-card__title" href="/p/${encodeURIComponent(card.slug || "")}" data-link>${escapeHtml(card.title || "商品")}</a>
        <div class="favorite-card__price">${amountHtml(card.price_cents)}${was}</div>
        <button class="btn btn--quiet btn--small favorite-card__remove" type="button" data-favorite-remove="${escapeHtml(card.slug || "")}" aria-label="取消收藏 ${escapeHtml(card.title || "商品")}">${icon("heart", 15)} 取消收藏</button>
      </div>
    </article>`;
  }

  function bindFavoriteActions() {
    ctx.root.querySelectorAll("[data-favorite-remove]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        button.innerHTML = '<span class="spinner" aria-hidden="true"></span> 更新中';
        try {
          const result = await api.post(`/api/favorites/${encodeURIComponent(button.dataset.favoriteRemove)}/toggle`, {}, { signal: ctx.signal });
          if (ctx.signal.aborted) return;
          toast(result?.favorited === false ? "已取消收藏" : "收藏状态已更新");
          await loadFavoritesTab();
        } catch (err) {
          if (ctx.signal.aborted) return;
          button.disabled = false;
          button.textContent = "取消收藏";
          showAlert(errMessage(err, "收藏没有更新成功。"));
        }
      });
    });
  }

  async function loadFavoritesTab() {
    const seq = ++loadSeq;
    setPane(`<div class="account-pane-skeleton" aria-hidden="true"><div class="skeleton" style="height:26px;width:30%"></div><div class="favorites-grid mt-3"><div class="skeleton" style="aspect-ratio:1;border-radius:var(--radius)"></div><div class="skeleton" style="aspect-ratio:1;border-radius:var(--radius)"></div><div class="skeleton" style="aspect-ratio:1;border-radius:var(--radius)"></div></div></div><p class="sr-only" role="status">正在读取收藏</p>`, true);
    try {
      const data = await api.get("/api/favorites", { signal: ctx.signal });
      if (ctx.signal.aborted || seq !== loadSeq) return;
      const items = Array.isArray(data?.items) ? data.items : null;
      if (!items) throw new Error("收藏响应缺少 items");
      setPane(`<section class="account-section" aria-labelledby="account-favorites-title">
        <div class="account-pane-head"><div><p class="eyebrow">SAVED</p><h2 id="account-favorites-title">我的收藏</h2><p>库存与价格以当前服务端数据为准。</p></div><span class="tiny muted">${Number(data.total) || items.length} 件</span></div>
        ${items.length ? `<div class="favorites-grid">${items.map(favoriteCardHtml).join("")}</div>` : emptyState({ title: "还没有收藏", message: "逛到喜欢的商品，点一下小心心就会收在这里。", actionLabel: "去发现", actionHref: "/search", iconName: "heart" })}
      </section>`);
      bindFavoriteActions();
    } catch (err) {
      if (ctx.signal.aborted || err?.name === "AbortError" || seq !== loadSeq) return;
      errorPane(errMessage(err, "收藏暂时没有读出来。"), () => { void loadFavoritesTab(); });
    }
  }

  function renderProfilePane() {
    const user = state.user || {};
    const role = user.role === "seller" ? "商家账号" : "买家账号";
    setPane(`<section class="account-section profile-section" aria-labelledby="account-profile-title">
      <div class="account-pane-head"><div><p class="eyebrow">PROFILE</p><h2 id="account-profile-title">账户资料</h2><p>资料来自服务端会话；演示环境不提供资料编辑。</p></div></div>
      <div class="trade-card profile-card">
        <div class="profile-card__avatar">${user.avatar ? `<img src="${escapeHtml(user.avatar)}" alt="" loading="lazy">` : escapeHtml((user.display_name || "客").slice(0, 1))}</div>
        <div class="profile-card__body">
          <h3>${escapeHtml(user.display_name || "开集用户")}</h3>
          <p class="muted">@${escapeHtml(user.handle || "")} · ${escapeHtml(user.email || "")}</p>
          <dl class="profile-facts">
            <div><dt>身份</dt><dd>${escapeHtml(role)}</dd></div>
            <div><dt>加入开集</dt><dd>${escapeHtml(user.member_since ? dateOnly(user.member_since) : "—")}</dd></div>
            ${user.shop ? `<div><dt>所属店铺</dt><dd>${escapeHtml(user.shop.name || "")}</dd></div>` : ""}
          </dl>
          ${user.role === "seller" ? `<a class="btn btn--jade btn--small" href="/seller" data-link>进入商家工作台 ${icon("arrowRight", 14)}</a>` : '<p class="tiny muted">资料编辑不在本次演示范围；地址可在“地址”标签中自行维护。</p>'}
        </div>
      </div>
      <div class="trade-card profile-note"><b>关于演示账号</b><p>订单、优惠与收藏都是真实写入本地数据库的记录；支付为模拟流程，不会产生资金变动。退出登录入口位于页面右上。</p><p class="tiny muted">需要彻底退出时，也可以关闭浏览器清除当前会话。</p></div>
    </section>`);
  }

  async function loadActiveTab() {
    if (!state.user) {
      setPane(guestPane());
      bindLogin();
      return;
    }
    clearAlert();
    if (activeTab === "orders") { await loadOrdersTab(); return; }
    if (activeTab === "addresses") { await loadAddressesTab(); return; }
    if (activeTab === "coupons") { await loadCouponsTab(); return; }
    if (activeTab === "favorites") { await loadFavoritesTab(); return; }
    renderProfilePane();
  }

  paintShell();
  if (!state.user) {
    setPane(guestPane());
    bindLogin();
    return;
  }
  await loadActiveTab();
}
