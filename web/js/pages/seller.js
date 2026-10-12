// Vendor workbench: real seller data, shop-scoped fulfilment and after-sales.
// Every dynamic value from the API is escaped before it reaches innerHTML.
import { api, ApiError } from "../core/api.js";
import { refreshSession, state } from "../core/store.js";
import {
  emptyState,
  escapeHtml,
  icon,
  money,
  openAuth,
  openModal,
  priceHtml,
  toast,
} from "../components/index.js";

const PAGE_SIZE = 12;
const MAX_STOCK = 1000000;
const TABS = [
  { key: "overview", label: "经营概览", icon: "grid" },
  { key: "orders", label: "订单发货", icon: "truck" },
  { key: "inventory", label: "库存调价", icon: "box" },
  { key: "refunds", label: "售后处理", icon: "return" },
];
const TAB_KEYS = TABS.map(function (tab) { return tab.key; });
const ORDER_FILTERS = [
  { key: "PENDING", label: "待发货", hint: "已付款，等物流" },
  { key: "SHIPPED", label: "已发出", hint: "运输中或已完成" },
  { key: "ALL", label: "全部", hint: "全部本店包裹" },
];
const REFUND_FILTERS = [
  { key: "ALL", label: "全部" },
  { key: "REQUESTED", label: "待处理" },
  { key: "APPROVED", label: "已同意" },
  { key: "REJECTED", label: "已驳回" },
  { key: "CANCELLED", label: "已取消" },
];
const KIND_LABELS = { self: "官方自营", brand: "品牌旗舰", factory: "源头工厂" };
const OFFER_STATUS_LABELS = { active: "在售", paused: "已下架" };
const ORDER_TONES = {
  PENDING_PAYMENT: "muted",
  GROUP_FORMING: "honey",
  PAID: "accent",
  SHIPPED: "jade",
  COMPLETED: "jade",
  REFUNDING: "honey",
  REFUNDED: "danger",
  CANCELLED: "muted",
};
const PACKAGE_TONES = { PENDING: "accent", SHIPPED: "jade", DELIVERED: "jade", CANCELLED: "muted" };
const REFUND_TONES = { REQUESTED: "honey", APPROVED: "jade", REJECTED: "danger", CANCELLED: "muted" };

const ASIA = "Asia/Shanghai";
const NUMBER = new Intl.NumberFormat("zh-CN");
const SHORT_NUMBER = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
const DATE_TIME = new Intl.DateTimeFormat("zh-CN", {
  timeZone: ASIA, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
const SHORT_DATE = new Intl.DateTimeFormat("zh-CN", { timeZone: ASIA, month: "2-digit", day: "2-digit" });
const TODAY = new Intl.DateTimeFormat("zh-CN", {
  timeZone: ASIA, month: "long", day: "numeric", weekday: "long",
});

function esc(value) {
  return escapeHtml(value === null || value === undefined ? "" : value);
}

function num(value, fallback) {
  var n = Number(value);
  return Number.isFinite(n) ? n : (fallback === undefined ? 0 : fallback);
}

function fmtNumber(value) {
  return NUMBER.format(num(value));
}

function fmtMoney(cents) {
  return money(num(cents));
}

function fmtWhen(value, withTime) {
  if (!value) return "—";
  var date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return (withTime === false ? SHORT_DATE : DATE_TIME).format(date);
}

function fmtCnyShort(cents) {
  var yuan = num(cents) / 100;
  if (yuan >= 10000) {
    var wan = (yuan / 10000).toFixed(1);
    return "¥" + wan.replace(/\.0$/, "") + "万";
  }
  return "¥" + SHORT_NUMBER.format(yuan);
}

function sellerUser() {
  var user = state.user;
  return user && user.role === "seller" && user.shop ? user : null;
}

function normalizeTab(raw) {
  var value = String(raw || "").toLowerCase();
  return TAB_KEYS.indexOf(value) >= 0 ? value : "overview";
}

function normalizeStatus(raw, allowed, fallback) {
  var value = String(raw || "").toUpperCase();
  return allowed.indexOf(value) >= 0 ? value : fallback;
}

function readView(ctx) {
  var rawPage = parseInt(ctx.query.get("page") || "1", 10);
  var page = Number.isFinite(rawPage) && rawPage > 0 ? rawPage : 1;
  return {
    tab: normalizeTab(ctx.query.get("tab")),
    orderStatus: normalizeStatus(ctx.query.get("status"), ["PENDING", "SHIPPED", "ALL"], "PENDING"),
    refundStatus: normalizeStatus(ctx.query.get("status"), ["ALL", "REQUESTED", "APPROVED", "REJECTED", "CANCELLED"], "ALL"),
    page: page,
    q: String(ctx.query.get("q") || ""),
  };
}

function sellerHref(options) {
  var params = new URLSearchParams();
  params.set("tab", options.tab || "overview");
  if (options.status) params.set("status", String(options.status));
  if (options.q) params.set("q", String(options.q));
  if (options.page && Number(options.page) > 1) params.set("page", String(options.page));
  return "/seller?" + params.toString();
}

function tabHref(key) {
  if (key === "orders") return sellerHref({ tab: key, status: "PENDING" });
  if (key === "refunds") return sellerHref({ tab: key, status: "ALL" });
  return sellerHref({ tab: key });
}

function tabLabel(key) {
  var found = TABS.filter(function (item) { return item.key === key; })[0];
  return found ? found.label : "经营概览";
}

function pageHref(view, page) {
  return sellerHref({
    tab: view.tab,
    status: view.tab === "orders" ? view.orderStatus : (view.tab === "refunds" ? view.refundStatus : ""),
    q: view.tab === "inventory" ? view.q : "",
    page: page,
  });
}

function tabRequestPath(view) {
  if (view.tab === "orders") {
    return "/api/seller/orders?status=" + encodeURIComponent(view.orderStatus) + "&page=" + view.page + "&page_size=" + PAGE_SIZE;
  }
  if (view.tab === "inventory") {
    var params = new URLSearchParams();
    if (view.q) params.set("q", view.q);
    params.set("page", String(view.page));
    params.set("page_size", String(PAGE_SIZE));
    return "/api/seller/products?" + params.toString();
  }
  if (view.tab === "refunds") {
    return "/api/seller/refunds?status=" + encodeURIComponent(view.refundStatus) + "&page=" + view.page + "&page_size=" + PAGE_SIZE;
  }
  return "";
}

function isAborted(ctx, err) {
  return Boolean((ctx.signal && ctx.signal.aborted) || (err && err.name === "AbortError"));
}

function reload(ctx) {
  render(ctx).catch(function (err) {
    if (ctx.signal && ctx.signal.aborted) return;
    toast(err instanceof ApiError ? err.message : "刷新失败，请稍后再试", { type: "error" });
  });
}

function statusChip(label, tone) {
  return '<span class="seller-status seller-status--' + esc(tone || "muted") + '">' + esc(label) + "</span>";
}

// ---------------------------------------------------------------------------
// Page lifecycle
// ---------------------------------------------------------------------------

export async function render(ctx) {
  var view = readView(ctx);
  var user = sellerUser();
  if (!user) {
    paintGate(ctx);
    return;
  }
  await loadDashboard(ctx, view);
}

function paintLoading(ctx) {
  ctx.setTitle("商家工作台 · OpenShop 开集");
  ctx.root.innerHTML = [
    '<section class="container seller-page seller-page--loading" id="seller-root" data-testid="seller-root" aria-busy="true">',
      '<div class="seller-loading" role="status" aria-live="polite">',
        '<p class="eyebrow">MERCHANT CONSOLE · 商家工作台</p>',
        '<h1>商家工作台</h1>',
        '<p class="seller-loading__message">正在汇总本店真实经营数据…</p>',
        '<div class="seller-loading__metrics" aria-hidden="true">',
          '<div class="skeleton seller-loading__block"></div>',
          '<div class="skeleton seller-loading__block"></div>',
          '<div class="skeleton seller-loading__block"></div>',
        '</div>',
        '<div class="skeleton seller-loading__panel" aria-hidden="true"></div>',
      '</div>',
    '</section>',
  ].join("");
}

async function loadDashboard(ctx, view) {
  paintLoading(ctx);
  var tabPromise = tabRequestPath(view)
    ? api.get(tabRequestPath(view), { signal: ctx.signal })
    : Promise.resolve(null);
  try {
    var results = await Promise.all([
      api.get("/api/seller/overview", { signal: ctx.signal }),
      tabPromise,
    ]);
    if (ctx.signal && ctx.signal.aborted) return;
    var overview = results[0];
    var tabData = results[1];
    if (!overview || !Array.isArray(overview.metrics) || !overview.shop) {
      throw new Error("商家概览返回了无法识别的数据");
    }
    if (view.tab !== "overview" && (!tabData || typeof tabData !== "object")) {
      throw new Error("商家列表返回了无法识别的数据");
    }
    ctx.setTitle(tabLabel(view.tab) + " · 商家工作台 · OpenShop 开集");
    ctx.root.innerHTML = dashboardMarkup(overview, view, tabData);
    bindDashboard(ctx, view, overview, tabData);
  } catch (err) {
    if (isAborted(ctx, err)) return;
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      try {
        await refreshSession({ signal: ctx.signal });
      } catch (_) {
        if (ctx.signal && ctx.signal.aborted) return;
      }
      if (ctx.signal && ctx.signal.aborted) return;
      paintGate(ctx, "当前会话没有商家工作台权限，请使用商家演示账号。");
      return;
    }
    paintLoadError(ctx, err);
  }
}

function paintLoadError(ctx, err) {
  ctx.setTitle("商家工作台 · OpenShop 开集");
  var message = err instanceof ApiError ? err.message : "暂时无法读取商家数据，请检查服务后重试。";
  var code = err instanceof ApiError ? err.code : "LOAD_FAILED";
  ctx.root.innerHTML = [
    '<section class="container seller-page" id="seller-root" data-testid="seller-root">',
      '<div class="seller-error" role="alert">',
        '<span class="eyebrow eyebrow--jade">MERCHANT CONSOLE</span>',
        '<p class="seller-mono muted">' + esc(code) + '</p>',
        '<h1>商家工作台暂时没打开</h1>',
        '<p class="lede">' + esc(message) + '</p>',
        '<div class="cluster mt-3">',
          '<button class="btn btn--primary" type="button" data-testid="seller-retry">重新加载</button>',
          '<a class="btn btn--ghost" href="/" data-link>回开集首页</a>',
        '</div>',
        '<p class="demo-note mt-2">这里只显示你能管理的店铺数据，不会回退到他人的经营数据。</p>',
      '</div>',
    '</section>',
  ].join("");
  var retry = ctx.root.querySelector('[data-testid="seller-retry"]');
  if (retry) retry.addEventListener("click", function () { reload(ctx); });
}

function paintGate(ctx, reason) {
  var user = state.user;
  var buyer = user && user.role === "buyer";
  var identity = user
    ? "当前登录：" + (buyer ? "买家" : "已登录账号") + " · " + (user.display_name || "访客")
    : "当前为游客会话";
  ctx.setTitle("商家工作台 · OpenShop 开集");
  ctx.root.innerHTML = [
    '<section class="container seller-page" id="seller-root" data-testid="seller-root">',
      '<div class="seller-gate">',
        '<div class="seller-gate__story">',
          '<p class="eyebrow">MERCHANT CONSOLE · 商家工作台</p>',
          '<h1>把一间店，<br><em>打理得清清楚楚。</em></h1>',
          '<p class="seller-gate__lede">经营数字、待发包裹、库存水位和售后决定，在一张真实账本里看清。商品、店铺与交易均为演示数据。</p>',
          '<ul class="seller-gate__points">',
            '<li><span class="seller-gate__point-icon">' + icon("grid", 19) + '</span><span><b>看得见的经营</b><small>7 天成交、订单与浏览来自本店真实记录，不用示例数字。</small></span></li>',
            '<li><span class="seller-gate__point-icon">' + icon("truck", 19) + '</span><span><b>一件件发出</b><small>填写物流、核验运单；所有包裹发出后订单才会转为已发货。</small></span></li>',
            '<li><span class="seller-gate__point-icon">' + icon("return", 19) + '</span><span><b>售后有据可查</b><small>通过退款时按交易规则恢复库存与优惠券，驳回则回到原订单状态。</small></span></li>',
          '</ul>',
          '<div class="seller-gate__trust">' + icon("shield", 16) + '<span>店铺数据严格按 shop_id 隔离 · 买家地址不会进入商家台</span></div>',
        '</div>',
        '<aside class="seller-gate__card" aria-labelledby="seller-gate-title">',
          '<div class="seller-gate__card-head">',
            '<span class="seller-gate__mark" aria-hidden="true">周</span>',
            '<span><b>OpenShop 自营</b><small>DEMO MERCHANT · 演示商家</small></span>',
          '</div>',
          '<p class="seller-gate__eyebrow">门已经为你留好</p>',
          '<h2 id="seller-gate-title">开门做生意</h2>',
          '<p class="seller-gate__card-copy">' + (buyer
            ? "你正在以买家身份浏览。进入商家演示会切换身份，原来的购物车与订单不会被修改。"
            : "无需密码，使用种子演示商家周屿进入一间正在营业的店。") + '</p>',
          '<p class="seller-gate__identity">' + esc(identity) + '</p>',
          '<button class="btn btn--primary btn--large btn--block" type="button" data-testid="seller-login-demo">一键进入商家演示 ' + icon("arrowRight", 18) + '</button>',
          '<p class="seller-gate__fineprint">演示账号：周屿 · OpenShop 自营。登录后将显示本店真实数据。</p>',
          (reason ? '<p class="seller-gate__reason" role="status">' + esc(reason) + '</p>' : ""),
          '<div class="seller-gate__receipt" aria-hidden="true">',
            '<div class="seller-gate__receipt-bar"><span>经营账本</span><span>OPENSHOP / DEMO</span></div>',
            '<div class="seller-gate__receipt-row"><span>成交额</span><b>登录后可见</b></div>',
            '<div class="seller-gate__receipt-row"><span>待发包裹</span><b>登录后可见</b></div>',
            '<div class="seller-gate__receipt-row"><span>售后决定</span><b>登录后可见</b></div>',
            '<div class="seller-gate__receipt-line"></div>',
            '<p>真实服务端写入 · 模拟交易环境</p>',
          '</div>',
          '<a class="seller-gate__home" href="/" data-link>先回开集逛逛 ' + icon("arrowRight", 14) + '</a>',
        '</aside>',
      '</div>',
    '</section>',
  ].join("");

  var trigger = ctx.root.querySelector('[data-testid="seller-login-demo"]');
  if (!trigger) return;
  trigger.addEventListener("click", function () {
    var modal = openAuthForSeller(ctx, Boolean(buyer));
    var sellerDemo = modal.body.querySelector('[data-demo="seller"]');
    if (sellerDemo) {
      sellerDemo.setAttribute("data-testid", "seller-demo-confirm");
      sellerDemo.focus();
    }
  });
}

function openAuthForSeller(ctx, buyer) {
  var modal = openAuth({
    preferSeller: true,
    reason: buyer
      ? "当前是买家账号，进入商家演示会切换为商家身份。"
      : "商家演示账号无需密码。登录后只显示 OpenShop 自营自己的经营数据。",
    onSuccess: async function (session) {
      if (ctx.signal && ctx.signal.aborted) return;
      if (!session || !session.user || session.user.role !== "seller") {
        paintGate(ctx, "这个账号不是商家账号，请改用商家演示。");
        toast("已登录买家账号；需要商家权限才能进入工作台", { type: "info" });
        return;
      }
      await render(ctx);
    },
  });
  return modal;
}

function dashboardMarkup(overview, view, tabData) {
  return [
    '<section class="container seller-page" id="seller-root" data-testid="seller-root">',
      heroMarkup(overview),
      tabNavMarkup(view.tab, overview),
      '<div class="seller-workspace' + (view.tab === "overview" ? " seller-workspace--overview" : "") + '">',
        view.tab === "overview"
          ? overviewMarkup(overview)
          : (view.tab === "orders"
            ? ordersMarkup(view, tabData)
            : (view.tab === "inventory" ? inventoryMarkup(view, tabData) : refundsMarkup(view, tabData))),
      '</div>',
      '<p class="seller-footnote">' + icon("shield", 14) + ' 商家台只展示当前店铺的数据；买家姓名与联系方式已遮蔽。演示环境不产生真实资金或物流。</p>',
    '</section>',
  ].join("");
}

function heroMarkup(overview) {
  var shop = (overview && overview.shop) || (state.user && state.user.shop) || {};
  var user = state.user || {};
  var labels = overview && overview.series && Array.isArray(overview.series.labels) ? overview.series.labels : [];
  var range = labels.length ? labels[0] + " — " + labels[labels.length - 1] : "近 7 天";
  var chips = [];
  if (shop.kind) chips.push(KIND_LABELS[shop.kind] || shop.kind);
  if (shop.verified) chips.push("已验证");
  if (shop.rating !== undefined && shop.rating !== null) chips.push(num(shop.rating).toFixed(1) + " 店铺评分");
  if (shop.followers_count !== undefined && shop.followers_count !== null) chips.push(fmtNumber(shop.followers_count) + " 关注");
  if (shop.response_rate) chips.push("响应 " + String(shop.response_rate));
  return [
    '<header class="seller-hero">',
      '<div class="seller-hero__awning" aria-hidden="true"></div>',
      '<div class="seller-hero__glow" aria-hidden="true"></div>',
      '<div class="seller-hero__content">',
        '<p class="eyebrow eyebrow--honey">MERCHANT CONSOLE · 本店账本</p>',
        '<div class="seller-hero__title-row"><h1>商家工作台</h1><span class="seller-hero__demo">演示环境</span></div>',
        '<p class="seller-hero__greeting">' + esc(user.display_name || "掌柜") + '，这里是 <b>' + esc(shop.name || "你的店铺") + '</b> 的真实经营现场。</p>',
        '<p class="seller-hero__tagline">' + esc(shop.tagline || "") + (shop.ship_from ? ' · ' + esc(shop.ship_from) : "") + '</p>',
        '<div class="chip-row seller-hero__chips">' + chips.map(function (label) { return '<span class="seller-hero__chip">' + esc(label) + '</span>'; }).join("") + '</div>',
      '</div>',
      '<div class="seller-hero__side">',
        '<div class="seller-hero__date"><span>' + esc(TODAY.format(new Date())) + '</span><b>' + esc(range) + '</b><small>经营视窗</small></div>',
        '<button class="seller-refresh" type="button" data-testid="seller-refresh">' + icon("return", 15) + '刷新数据</button>',
        '<p class="seller-hero__note">' + icon("shield", 13) + '仅本店 · 买家信息最小化</p>',
      '</div>',
    '</header>',
  ].join("");
}

function tabNavMarkup(activeTab, overview) {
  var todos = (overview && overview.todos) || {};
  var counts = {
    orders: num(todos.to_ship),
    refunds: num(todos.refunds),
    inventory: num(todos.low_stock),
  };
  var links = TABS.map(function (tab) {
    var count = counts[tab.key] || 0;
    var badge = count > 0
      ? '<span class="seller-tab__badge" aria-label="' + esc(count + " 项") + '">' + fmtNumber(count) + '</span>'
      : "";
    return '<a class="seller-tab' + (activeTab === tab.key ? " is-active" : "") + '" href="' + esc(tabHref(tab.key)) + '" data-link data-testid="seller-tab-' + esc(tab.key) + '"' + (activeTab === tab.key ? ' aria-current="page"' : "") + '>' +
      icon(tab.icon, 17) + '<span>' + esc(tab.label) + '</span>' + badge + '</a>';
  }).join("");
  return [
    '<nav class="seller-tabbar" aria-label="商家工作台分区">',
      '<div class="seller-tabbar__scroll">' + links + '</div>',
      '<span class="seller-tabbar__meta">' + icon("shield", 14) + '店铺隔离已开启</span>',
    '</nav>',
  ].join("");
}

function bindDashboard(ctx, view, overview, tabData) {
  var refresh = ctx.root.querySelector('[data-testid="seller-refresh"]');
  if (refresh) refresh.addEventListener("click", function () { reload(ctx); });
  if (view.tab === "orders" && tabData) bindOrders(ctx, view, tabData);
  if (view.tab === "inventory" && tabData) bindInventory(ctx, view, tabData);
  if (view.tab === "refunds" && tabData) bindRefunds(ctx, view, tabData);
}

function paginationMarkup(view, data) {
  if (!data || typeof data !== "object") return "";
  var pages = Math.max(0, num(data.pages));
  var page = Math.max(1, num(data.page, 1));
  var total = Math.max(0, num(data.total));
  if (total === 0) return "";
  var prev = page > 1
    ? '<a class="seller-pagebtn" href="' + esc(pageHref(view, page - 1)) + '" data-link data-testid="seller-page-prev">上一页</a>'
    : '<span class="seller-pagebtn is-disabled" aria-disabled="true" data-testid="seller-page-prev">上一页</span>';
  var next = page < pages
    ? '<a class="seller-pagebtn" href="' + esc(pageHref(view, page + 1)) + '" data-link data-testid="seller-page-next">下一页</a>'
    : '<span class="seller-pagebtn is-disabled" aria-disabled="true" data-testid="seller-page-next">下一页</span>';
  return '<nav class="seller-pagination" aria-label="列表分页">' +
    prev +
    '<span class="seller-pagination__label">第 ' + page + ' / ' + Math.max(1, pages) + ' 页 · 共 ' + fmtNumber(total) + ' 条</span>' +
    next +
    '</nav>';
}


// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function deltaMarkup(metric) {
  var delta = metric.delta_pct;
  if (delta === null || delta === undefined) {
    return '<span class="seller-delta seller-delta--new">新周期</span>';
  }
  var value = Number(delta);
  if (!Number.isFinite(value)) return '<span class="seller-delta seller-delta--new">—</span>';
  if (value === 0) return '<span class="seller-delta seller-delta--flat">持平</span>';
  var positive = value > 0;
  return '<span class="seller-delta ' + (positive ? "seller-delta--up" : "seller-delta--down") + '">' +
    (positive ? "+" : "") + value.toFixed(1) + '%</span>';
}

function sparkMarkup(values, labels, isMoneyMetric) {
  var series = Array.isArray(values) ? values.slice(-7) : [];
  var names = Array.isArray(labels) ? labels.slice(-7) : [];
  while (series.length < 7) series.unshift(0);
  while (names.length < 7) names.unshift("—");
  var numbers = series.map(function (value) { return Math.max(0, num(value)); });
  var max = Math.max.apply(null, numbers.concat([0]));
  var pairs = names.map(function (name, index) {
    var shown = isMoneyMetric ? fmtMoney(numbers[index]) : fmtNumber(numbers[index]);
    return name + " " + shown;
  }).join("；");
  var bars = numbers.map(function (value) {
    var height = max > 0 ? Math.max(5, Math.round(value * 100 / max)) : 0;
    if (value === 0) height = 0;
    return '<span class="seller-spark__bar' + (height === 0 ? " is-zero" : "") + '" style="height:' + height + '%"></span>';
  }).join("");
  return '<div class="seller-spark" role="img" aria-label="逐日走势：' + esc(pairs) + '">' + bars + '</div>';
}

function metricMarkup(metric, labels) {
  var key = String(metric.key || "metric");
  var knownKey = ["gmv", "orders", "views"].indexOf(key) >= 0 ? key : "other";
  var isMoneyMetric = metric.value_cents !== undefined && metric.value_cents !== null;
  var value = isMoneyMetric ? num(metric.value_cents) : num(metric.value);
  var previous = isMoneyMetric ? num(metric.previous_cents) : num(metric.previous);
  var label = String(metric.label || (knownKey === "gmv" ? "近 7 天成交额" : knownKey === "orders" ? "近 7 天成交单" : "近 7 天商品浏览"));
  var iconName = knownKey === "gmv" ? "wallet" : (knownKey === "orders" ? "package" : "search");
  var unit = knownKey === "orders" ? "单" : (knownKey === "views" ? "次" : "");
  var valueHtml = isMoneyMetric
    ? '<span class="seller-metric__money">' + priceHtml(value, { className: "seller-metric__price" }) + '</span>'
    : '<span class="seller-metric__number">' + fmtNumber(value) + '</span>' + (unit ? '<span class="seller-metric__unit">' + unit + '</span>' : "");
  var previousText = isMoneyMetric ? fmtMoney(previous) : fmtNumber(previous);
  return [
    '<article class="seller-metric seller-metric--' + knownKey + '" data-testid="overview-metric" data-metric="' + esc(key) + '">',
      '<div class="seller-metric__head"><span class="seller-metric__label">' + esc(label) + '</span><span class="seller-metric__icon">' + icon(iconName, 19) + '</span></div>',
      '<div class="seller-metric__value">' + valueHtml + '</div>',
      '<div class="seller-metric__foot">' + deltaMarkup(metric) + '<span class="seller-metric__previous">上期 ' + esc(previousText) + '</span></div>',
      sparkMarkup(metric.spark, labels, isMoneyMetric),
    '</article>',
  ].join("");
}

function metricsMarkup(overview) {
  var labels = overview && overview.series && Array.isArray(overview.series.labels) ? overview.series.labels : [];
  var metrics = Array.isArray(overview.metrics) ? overview.metrics : [];
  if (!metrics.length) {
    return '<div class="seller-panel"><p class="seller-empty-inline">暂时没有可展示的经营指标。</p></div>';
  }
  return '<div class="seller-metrics">' + metrics.map(function (metric) {
    return metricMarkup(metric, labels);
  }).join("") + '</div>';
}

function trendMarkup(overview) {
  var series = (overview && overview.series) || {};
  var labels = Array.isArray(series.labels) ? series.labels : [];
  var gmvs = Array.isArray(series.gmv_cents) ? series.gmv_cents.map(function (v) { return Math.max(0, num(v)); }) : [];
  var counts = Array.isArray(series.order_counts) ? series.order_counts.map(function (v) { return Math.max(0, num(v)); }) : [];
  var length = Math.max(labels.length, gmvs.length, counts.length, 7);
  while (labels.length < length) labels.push("—");
  while (gmvs.length < length) gmvs.push(0);
  while (counts.length < length) counts.push(0);
  labels = labels.slice(-length);
  gmvs = gmvs.slice(-length);
  counts = counts.slice(-length);
  var max = Math.max.apply(null, gmvs.concat([0]));
  var totalGmv = gmvs.reduce(function (sum, value) { return sum + value; }, 0);
  var totalOrders = counts.reduce(function (sum, value) { return sum + value; }, 0);
  var peakIndex = 0;
  for (var i = 1; i < gmvs.length; i += 1) {
    if (gmvs[i] > gmvs[peakIndex]) peakIndex = i;
  }
  var chart = labels.map(function (label, index) {
    var value = gmvs[index];
    var height = max > 0 ? Math.max(5, Math.round(value * 100 / max)) : 0;
    if (value === 0) height = 0;
    return [
      '<div class="seller-trend__col' + (index === labels.length - 1 ? " is-today" : "") + '">',
        '<span class="seller-trend__amount">' + (value > 0 ? esc(fmtCnyShort(value)) : "—") + '</span>',
        '<div class="seller-trend__track"><span class="seller-trend__bar' + (value === 0 ? " is-zero" : "") + '" style="height:' + height + '%"></span></div>',
        '<span class="seller-trend__day">' + esc(label) + '</span>',
        '<span class="seller-trend__count">' + fmtNumber(counts[index]) + ' 单</span>',
      '</div>',
    ].join("");
  }).join("");
  var aria = labels.map(function (label, index) {
    return label + " 成交 " + fmtMoney(gmvs[index]) + "，" + fmtNumber(counts[index]) + " 单";
  }).join("；");
  var peakText = totalGmv > 0 ? esc(labels[peakIndex]) + " · " + fmtMoney(gmvs[peakIndex]) : "暂无成交";
  return [
    '<section class="seller-panel seller-panel--trend" aria-labelledby="seller-trend-title">',
      '<div class="seller-panel__head">',
        '<div><p class="eyebrow eyebrow--jade">RHYTHM</p><h2 id="seller-trend-title">7 日成交节奏</h2></div>',
        '<span class="seller-chip-note">按付款日聚合 · 已退款不计入</span>',
      '</div>',
      '<div class="seller-trend__stats">',
        '<div><span>7 日合计</span><b>' + fmtMoney(totalGmv) + '</b></div>',
        '<div><span>成交单量</span><b>' + fmtNumber(totalOrders) + ' <small>单</small></b></div>',
        '<div><span>峰值日</span><b class="seller-trend__peak">' + peakText + '</b></div>',
      '</div>',
      '<div class="seller-trend__chart" role="img" aria-label="' + esc(aria) + '">' + chart + '</div>',
      totalGmv === 0
        ? '<p class="seller-trend__empty">这 7 天还没有成交记录，图表中的 0 是真实的 0。发货后订单会在买家确认后计入新的节奏。</p>'
        : '<p class="seller-trend__caption">柱高代表当日成交额，柱下数字为成交单量；最后一天是今天。</p>',
    '</section>',
  ].join("");
}

function todosMarkup(overview) {
  var todos = (overview && overview.todos) || {};
  var threshold = num(overview && overview.low_stock_threshold, 5);
  var items = [
    {
      key: "to_ship",
      label: "待发货包裹",
      note: "已付款，且尚未全部发出的本店包裹",
      href: sellerHref({ tab: "orders", status: "PENDING" }),
      iconName: "truck",
    },
    {
      key: "refunds",
      label: "待处理售后",
      note: "买家已发起，等待你的决定",
      href: sellerHref({ tab: "refunds", status: "REQUESTED" }),
      iconName: "return",
    },
    {
      key: "low_stock",
      label: "低库存报价",
      note: "库存不高于 " + threshold + " 的仍在售报价",
      href: sellerHref({ tab: "inventory" }),
      iconName: "box",
    },
  ];
  var total = items.reduce(function (sum, item) { return sum + num(todos[item.key]); }, 0);
  var rows = items.map(function (item) {
    var count = Math.max(0, num(todos[item.key]));
    return [
      '<a class="seller-todo' + (count > 0 ? " has-work" : "") + '" href="' + esc(item.href) + '" data-link data-testid="seller-todo-' + esc(item.key) + '">',
        '<span class="seller-todo__icon">' + icon(item.iconName, 19) + '</span>',
        '<span class="seller-todo__copy"><b>' + esc(item.label) + '</b><small>' + esc(item.note) + '</small></span>',
        '<span class="seller-todo__count">' + fmtNumber(count) + '</span>',
        icon("chevronRight", 15, "seller-todo__chevron"),
      '</a>',
    ].join("");
  }).join("");
  return [
    '<section class="seller-panel seller-panel--todos" aria-labelledby="seller-todos-title">',
      '<div class="seller-panel__head">',
        '<div><p class="eyebrow eyebrow--jade">TODAY</p><h2 id="seller-todos-title">今天先做这些</h2></div>',
        '<span class="seller-count-bubble">' + fmtNumber(total) + '</span>',
      '</div>',
      '<div class="seller-todo-list">' + rows + '</div>',
      '<p class="seller-panel__caption">' + (total === 0 ? "待办已经清空。数字为当前状态实时计算。" : "按当前数据库状态实时计算，不预设示例数量。") + '</p>',
    '</section>',
  ].join("");
}

function recentMarkup(overview) {
  var items = overview && Array.isArray(overview.recent_packages) ? overview.recent_packages : [];
  var rows = items.map(function (item) {
    var status = String(item.status || "");
    var href = sellerHref({ tab: "orders", status: status === "PAID" ? "PENDING" : "ALL" });
    var titles = Array.isArray(item.item_titles) && item.item_titles.length
      ? item.item_titles.join("、")
      : "本店商品";
    return [
      '<a class="seller-recent__row" href="' + esc(href) + '" data-link>',
        '<span class="seller-recent__top"><span class="seller-mono">' + esc(item.order_no) + '</span>' + statusChip(item.status_label || item.status, ORDER_TONES[status] || "muted") + '</span>',
        '<span class="seller-recent__meta"><b>' + esc(item.buyer_display || "买家") + '</b><span>' + esc(fmtWhen(item.created_at)) + '</span></span>',
        '<span class="seller-recent__title">' + esc(titles) + '</span>',
        '<span class="seller-recent__bottom"><span>' + esc(item.package_status_label || "包裹") + ' · 包裹 #' + esc(item.package_id) + '</span><b>' + fmtMoney(item.total_cents) + '</b></span>',
      '</a>',
    ].join("");
  }).join("");
  return [
    '<section class="seller-panel seller-panel--recent" aria-labelledby="seller-recent-title">',
      '<div class="seller-panel__head">',
        '<div><p class="eyebrow eyebrow--jade">RECENT</p><h2 id="seller-recent-title">最近的包裹</h2></div>',
        '<a class="seller-link" href="' + esc(sellerHref({ tab: "orders", status: "ALL" })) + '" data-link>全部订单 ' + icon("arrowRight", 14) + '</a>',
      '</div>',
      items.length ? '<div class="seller-recent">' + rows + '</div>' : '<p class="seller-empty-inline">还没有已付款的本店包裹。</p>',
    '</section>',
  ].join("");
}

function lowStockMarkup(overview) {
  var threshold = num(overview && overview.low_stock_threshold, 5);
  var items = overview && Array.isArray(overview.low_stock) ? overview.low_stock : [];
  var rows = items.map(function (item) {
    var href = sellerHref({ tab: "inventory", q: item.product_title || "", page: 1 });
    return [
      '<a class="seller-stockrow" href="' + esc(href) + '" data-link>',
        '<span class="seller-stockrow__dot" aria-hidden="true"></span>',
        '<span class="seller-stockrow__copy"><b>' + esc(item.product_title || "商品") + '</b><small>' + esc(item.offer_name || "") + (item.sku_hint ? " · " + esc(item.sku_hint) : "") + '</small></span>',
        '<span class="seller-stockrow__count">' + fmtNumber(item.stock) + '<small>件</small></span>',
      '</a>',
    ].join("");
  }).join("");
  var openCount = num(overview && overview.todos ? overview.todos.low_stock : 0);
  return [
    '<section class="seller-panel seller-panel--low" aria-labelledby="seller-low-title">',
      '<div class="seller-panel__head">',
        '<div><p class="eyebrow eyebrow--jade">STOCK</p><h2 id="seller-low-title">库存水位</h2></div>',
        '<a class="seller-link" href="' + esc(sellerHref({ tab: "inventory" })) + '" data-link>去库存页 ' + icon("arrowRight", 14) + '</a>',
      '</div>',
      items.length ? '<div class="seller-stocklist">' + rows + '</div>' : '<p class="seller-empty-inline">没有库存不高于 ' + threshold + ' 件的在售报价。</p>',
      '<p class="seller-panel__caption">当前共有 <b>' + fmtNumber(openCount) + '</b> 个低库存报价 · 阈值 ≤ ' + threshold + ' 件</p>',
    '</section>',
  ].join("");
}

function overviewMarkup(overview) {
  var labels = overview && overview.series && Array.isArray(overview.series.labels) ? overview.series.labels : [];
  var range = labels.length ? labels[0] + " — " + labels[labels.length - 1] : "近 7 天";
  return [
    '<div class="seller-sectionhead seller-sectionhead--overview">',
      '<div><p class="eyebrow eyebrow--jade">AT A GLANCE</p><h2>7 日经营概览</h2><p class="seller-sectionhead__copy">来自本店真实付款订单与商品浏览。没有发生的交易就是 0，不用装饰性数字。</p></div>',
      '<span class="seller-range">' + icon("clock", 15) + esc(range) + '</span>',
    '</div>',
    metricsMarkup(overview),
    '<div class="seller-overview__grid">',
      trendMarkup(overview),
      todosMarkup(overview),
      recentMarkup(overview),
      lowStockMarkup(overview),
    '</div>',
  ].join("");
}

// ---------------------------------------------------------------------------
// Orders and shipping
// ---------------------------------------------------------------------------

function orderFilterMarkup(view, data) {
  var counts = (data && data.counts) || {};
  var links = ORDER_FILTERS.map(function (filter) {
    var active = view.orderStatus === filter.key;
    var count = Math.max(0, num(counts[filter.key]));
    return '<a class="seller-filter__item' + (active ? " is-active" : "") + '" href="' + esc(sellerHref({ tab: "orders", status: filter.key })) + '" data-link data-testid="seller-order-filter-' + esc(filter.key) + '"' + (active ? ' aria-current="page"' : "") + ' title="' + esc(filter.hint) + '">' +
      '<span>' + esc(filter.label) + '</span><em>' + fmtNumber(count) + '</em></a>';
  }).join("");
  return '<nav class="seller-filters" aria-label="订单状态筛选">' + links + '</nav>';
}

function orderItemsMarkup(order) {
  var items = Array.isArray(order.items) ? order.items : [];
  if (!items.length) {
    return '<p class="seller-empty-inline">本店商品明细暂缺。</p>';
  }
  return '<div class="seller-orderitems">' + items.map(function (item) {
    var cover = item.cover
      ? '<img src="' + esc(item.cover) + '" alt="' + esc(item.title) + ' 商品封面" loading="lazy">'
      : '<span class="seller-orderitem__fallback" aria-hidden="true">' + icon("package", 20) + '</span>';
    return [
      '<div class="seller-orderitem">',
        '<span class="seller-orderitem__art">' + cover + '</span>',
        '<span class="seller-orderitem__copy"><b>' + esc(item.title) + '</b><small>' + esc(item.sku_label) + ' · ×' + fmtNumber(item.quantity) + '</small></span>',
        '<span class="seller-orderitem__price">' + priceHtml(item.unit_price_cents, { className: "seller-orderitem__money" }) + '</span>',
      '</div>',
    ].join("");
  }).join("") + '</div>';
}

function shipActionMarkup(order) {
  if (order.can_ship) {
    return '<div class="seller-order__action">' +
      '<p class="seller-order__hint">' + icon("alert", 15) + '核对物流后再发货；所有包裹发出后订单才会转为已发货。</p>' +
      '<button class="btn btn--primary" type="button" data-testid="seller-ship-button" data-package-id="' + esc(order.package_id) + '">填写物流并发货 ' + icon("truck", 17) + '</button>' +
    '</div>';
  }
  if (order.carrier && order.tracking_no) {
    return '<div class="seller-order__shipping">' + icon("truck", 16) + '<b>' + esc(order.carrier) + '</b><span class="seller-mono">' + esc(order.tracking_no) + '</span><small>' + esc(fmtWhen(order.shipped_at)) + '</small></div>';
  }
  var message = "当前包裹不能发货";
  if (order.order_status === "REFUNDING") message = "订单售后处理中，暂不发货";
  else if (order.package_status !== "PENDING") message = "包裹已发出或在其他状态";
  else if (order.order_status !== "PAID") message = "订单尚未付款或已结束，暂不发货";
  return '<div class="seller-order__shipping is-muted">' + icon("shield", 16) + '<span>' + esc(message) + '</span></div>';
}

function orderCardMarkup(order) {
  var orderTone = ORDER_TONES[order.order_status] || "muted";
  var packageTone = PACKAGE_TONES[order.package_status] || "muted";
  return [
    '<article class="seller-order" data-testid="seller-order-row" data-package-id="' + esc(order.package_id) + '">',
      '<div class="seller-order__top">',
        '<div class="seller-order__identity">',
          '<span class="seller-order__package">本店包裹 #' + esc(order.package_id) + '</span>',
          '<span class="seller-mono seller-order__no">' + esc(order.order_no) + '</span>',
        '</div>',
        '<div class="seller-order__status">',
          statusChip(order.package_status_label || order.package_status, packageTone),
          '<span class="seller-status seller-status--ghost">订单 · ' + esc(order.order_status_label || order.order_status) + '</span>',
        '</div>',
      '</div>',
      '<div class="seller-order__meta">',
        '<span>' + icon("user", 14) + '买家 ' + esc(order.buyer_display || "匿名买家") + '</span>',
        '<span>' + icon("clock", 14) + esc(fmtWhen(order.created_at)) + '</span>',
        '<span>共 ' + fmtNumber(order.item_count) + ' 件本店商品</span>',
      '</div>',
      orderItemsMarkup(order),
      '<div class="seller-order__foot">',
        '<div class="seller-order__amount"><span>本店商品合计 · ' + fmtNumber(order.item_count) + ' 件</span>' + priceHtml(order.total_cents, { className: "seller-order__total" }) + '</div>',
        '<span class="seller-mono seller-order__orderno-small">' + esc(order.order_no) + '</span>',
      '</div>',
      shipActionMarkup(order),
    '</article>',
  ].join("");
}

function ordersMarkup(view, data) {
  var items = data && Array.isArray(data.items) ? data.items : [];
  var total = num(data && data.total);
  var emptyTitle = view.orderStatus === "PENDING"
    ? "没有待发货的包裹"
    : (view.orderStatus === "SHIPPED" ? "还没有发出的包裹" : "还没有本店订单");
  var emptyMessage = view.orderStatus === "PENDING"
    ? "已付款的订单会出现在这里；发货后可在「已发出」里继续追踪。"
    : "交易产生后，这里只会显示属于当前店铺的包裹，不会混入其他店铺的数据。";
  var actionHref = view.orderStatus === "PENDING"
    ? sellerHref({ tab: "orders", status: "ALL" })
    : sellerHref({ tab: "orders", status: "PENDING" });
  var actionLabel = view.orderStatus === "PENDING" ? "查看全部订单" : "查看待发货";
  return [
    '<section class="seller-view seller-view--orders" aria-labelledby="seller-orders-title">',
      '<div class="seller-sectionhead">',
        '<div><p class="eyebrow eyebrow--jade">FULFILMENT</p><h2 id="seller-orders-title">订单发货</h2><p class="seller-sectionhead__copy">只显示本店包裹。买家姓名与地址在服务端就已遮蔽，发货不依赖地址。</p></div>',
        orderFilterMarkup(view, data),
      '</div>',
      '<div class="seller-listmeta"><span>' + fmtNumber(total) + ' 个本店包裹</span><span>' + icon("shield", 14) + '地址与电话不出现在这个页面</span></div>',
      '<div class="seller-orders" data-testid="seller-orders-list">',
        items.length
          ? items.map(orderCardMarkup).join("")
          : emptyState({ title: emptyTitle, message: emptyMessage, actionLabel: actionLabel, actionHref: actionHref, iconName: "package" }),
      '</div>',
      paginationMarkup(view, data),
    '</section>',
  ].join("");
}

function bindOrders(ctx, view, data) {
  var byPackage = {};
  (Array.isArray(data.items) ? data.items : []).forEach(function (item) {
    byPackage[String(item.package_id)] = item;
  });
  var carriers = Array.isArray(data.carriers) ? data.carriers.map(String) : [];
  ctx.root.querySelectorAll('[data-testid="seller-ship-button"]').forEach(function (button) {
    button.addEventListener("click", function () {
      var packageId = button.getAttribute("data-package-id") || "";
      var order = byPackage[packageId];
      if (order) openShipModal(ctx, order, carriers);
    });
  });
}

function openShipModal(ctx, order, carriers) {
  var options = ['<option value="">请选择物流公司</option>'].concat(
    carriers.map(function (carrier) {
      return '<option value="' + esc(carrier) + '">' + esc(carrier) + '</option>';
    })
  ).join("");
  var formHtml = [
    '<form id="seller-ship-form" class="seller-form" novalidate>',
      '<div class="seller-modal-summary">',
        '<span><small>订单</small><b class="seller-mono">' + esc(order.order_no) + '</b></span>',
        '<span><small>买家</small><b>' + esc(order.buyer_display || "匿名买家") + '</b></span>',
        '<span><small>本店包裹</small><b>#' + esc(order.package_id) + '</b></span>',
      '</div>',
      '<label class="field"><span class="field-label">物流公司</span>',
        '<select class="select" name="carrier" required data-testid="carrier-select">' + options + '</select>',
        '<span class="field-hint">固定承运商：' + esc(carriers.join("、") || "加载中") + '</span>',
      '</label>',
      '<label class="field"><span class="field-label">物流单号</span>',
        '<input class="input" name="tracking_no" type="text" minlength="6" maxlength="24" pattern="[A-Za-z0-9]{6,24}" autocomplete="off" placeholder="6-24 位字母或数字" required data-testid="tracking-input">',
        '<span class="field-hint">例如 QB202610120001。仅接受字母与数字。</span>',
      '</label>',
      '<p class="form-error" role="alert" data-ship-error hidden></p>',
      '<p class="demo-note">这里登记的是演示物流信息，不会调用真实承运商或产生费用。</p>',
    '</form>',
  ].join("");
  var footer = '<button class="btn btn--ghost" type="button" data-cancel>取消</button><button class="btn btn--primary" type="submit" form="seller-ship-form" data-testid="ship-submit">确认发货</button>';
  var modal = openModal({
    title: "登记发货物流",
    subtitle: "所有本店包裹发出后，订单才会整体转为已发货。",
    body: formHtml,
    footer: footer,
  });
  modal.dialog.classList.add("seller-modal");
  modal.footer.classList.add("seller-modal__footer");
  var form = modal.body.querySelector("#seller-ship-form");
  var errorEl = modal.body.querySelector("[data-ship-error]");
  var submit = modal.footer.querySelector('[data-testid="ship-submit"]');
  var cancel = modal.footer.querySelector("[data-cancel]");
  if (cancel) cancel.addEventListener("click", function () { modal.close(); });
  if (!form || !submit) return;
  var busy = false;
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (busy) return;
    var carrierEl = form.querySelector('[data-testid="carrier-select"]');
    var trackingEl = form.querySelector('[data-testid="tracking-input"]');
    var carrier = carrierEl ? carrierEl.value : "";
    var tracking = trackingEl ? trackingEl.value.trim() : "";
    function fail(message, target) {
      if (errorEl) {
        errorEl.textContent = message;
        errorEl.hidden = false;
      }
      if (target) target.focus();
    }
    if (!carrier) { fail("请选择一家物流公司。", carrierEl); return; }
    if (!/^[A-Za-z0-9]{6,24}$/.test(tracking)) {
      fail("物流单号必须是 6-24 位字母或数字。", trackingEl);
      return;
    }
    busy = true;
    submit.disabled = true;
    submit.textContent = "正在登记…";
    if (errorEl) errorEl.hidden = true;
    try {
      await api.post("/api/seller/orders/" + encodeURIComponent(String(order.package_id)) + "/ship", {
        carrier: carrier,
        tracking_no: tracking,
      });
      modal.close();
      toast("已登记发货：" + carrier + " · " + tracking);
      reload(ctx);
    } catch (err) {
      busy = false;
      submit.disabled = false;
      submit.textContent = "确认发货";
      var message = err instanceof ApiError ? err.message : "发货登记失败，请稍后再试";
      fail(message, trackingEl);
    }
  });
}


// ---------------------------------------------------------------------------
// Inventory / offers
// ---------------------------------------------------------------------------

function inventoryMarkup(view, data) {
  var items = data && Array.isArray(data.items) ? data.items : [];
  var total = num(data && data.total);
  var empty = emptyState({
    title: view.q ? "没有找到匹配的报价" : "这家店还没有报价",
    message: view.q ? "换一个商品词，或清除筛选查看全部本店报价。" : "商品报价会在这里出现，随后可以调价、改库存或下架。",
    actionLabel: view.q ? "清除筛选" : "回到概览",
    actionHref: view.q ? sellerHref({ tab: "inventory" }) : sellerHref({ tab: "overview" }),
    iconName: "box",
  });
  return [
    '<section class="seller-view seller-view--inventory" aria-labelledby="seller-inventory-title">',
      '<div class="seller-sectionhead seller-sectionhead--search">',
        '<div><p class="eyebrow eyebrow--jade">STOCKROOM</p><h2 id="seller-inventory-title">库存调价</h2><p class="seller-sectionhead__copy">价格以元填写，保存时由服务端换算成分并复核范围。所有修改只作用于本店报价。</p></div>',
        '<form class="seller-search" role="search" aria-label="搜索本店报价">',
          '<label class="sr-only" for="seller-inventory-q">搜索商品或报价</label>',
          '<span class="seller-search__icon">' + icon("search", 16) + '</span>',
          '<input id="seller-inventory-q" name="q" type="search" maxlength="60" placeholder="搜索本店商品或报价" value="' + esc(view.q) + '" data-testid="seller-inventory-search">',
          '<button class="btn btn--primary btn--small" type="submit">搜索</button>',
          (view.q ? '<a class="seller-search__clear" href="' + esc(sellerHref({ tab: "inventory" })) + '" data-link>清除</a>' : ""),
        '</form>',
      '</div>',
      '<div class="seller-listmeta"><span>共 ' + fmtNumber(total) + ' 个本店报价' + (view.q ? ' · 筛选“' + esc(view.q) + '”' : "") + '</span><span>' + icon("alert", 14) + '低库存阈值：≤ 5 件</span></div>',
      '<div class="seller-inventory" data-testid="seller-inventory-list">' + (items.length ? items.map(offerRowMarkup).join("") : empty) + '</div>',
      paginationMarkup(view, data),
    '</section>',
  ].join("");
}

function offerRowMarkup(item) {
  var offerId = num(item.offer_id);
  var title = String(item.product_title || "商品");
  var price = num(item.price_cents);
  var stock = Math.max(0, num(item.stock));
  var minPrice = Math.max(1, num(item.min_price_cents, 1));
  var maxPrice = Math.max(minPrice, num(item.max_price_cents, price));
  var statusValue = item.status === "paused" ? "paused" : "active";
  var statusLabel = OFFER_STATUS_LABELS[statusValue] || item.status_label || "在售";
  var group = item.group_price_cents !== null && item.group_price_cents !== undefined
    ? '<span class="seller-offer__group">拼团价 ' + fmtMoney(item.group_price_cents) + '</span>'
    : "";
  var offerName = item.name ? '<span class="seller-offer__name">' + esc(item.name) + '</span>' : "";
  var skuHint = item.sku_hint ? '<span class="seller-offer__sku">' + esc(item.sku_hint) + '</span>' : "";
  var lowBadge = item.low_stock ? '<span class="seller-offer__low">低库存</span>' : "";
  return [
    '<article class="seller-offer" data-testid="seller-product-row" data-offer-id="' + esc(offerId) + '" data-min-cents="' + esc(minPrice) + '" data-max-cents="' + esc(maxPrice) + '">',
      '<div class="seller-offer__main">',
        '<span class="seller-offer__art"><img src="' + esc(item.cover) + '" alt="' + esc(title) + ' 商品封面" loading="lazy"></span>',
        '<div class="seller-offer__copy">',
          '<p class="seller-offer__brand">' + esc(item.product_brand || item.kind_label || "OpenShop") + ' · ' + esc(item.kind_label || "本店报价") + '</p>',
          '<h3>' + esc(title) + '</h3>',
          '<p class="seller-offer__meta">' + offerName + skuHint + '</p>',
          '<div class="chip-row seller-offer__chips">',
            '<span class="seller-status seller-status--' + (statusValue === "active" ? "jade" : "muted") + '">' + esc(statusLabel) + '</span>',
            lowBadge,
            group,
            '<span class="seller-mono seller-offer__id">报价 #' + esc(offerId) + '</span>',
          '</div>',
        '</div>',
        '<div class="seller-offer__price"><small>当前标价</small>' + priceHtml(price, { className: "seller-offer__price-value" }) + '<span>划线价 ' + fmtMoney(item.market_price_cents) + '</span></div>',
      '</div>',
      '<div class="seller-offer__editor">',
        '<div class="seller-offer__fields">',
          '<label class="field seller-field">',
            '<span class="field-label">价格（元）</span>',
            '<span class="seller-input-money"><i aria-hidden="true">¥</i><input class="input" type="number" inputmode="decimal" step="0.01" min="' + esc((minPrice / 100).toFixed(2)) + '" max="' + esc((maxPrice / 100).toFixed(2)) + '" value="' + esc((price / 100).toFixed(2)) + '" data-min-cents="' + esc(minPrice) + '" data-max-cents="' + esc(maxPrice) + '" data-testid="seller-price-input" aria-label="' + esc(title) + ' 价格（元）"></span>',
            '<span class="field-hint">允许 ' + fmtMoney(minPrice) + ' — ' + fmtMoney(maxPrice) + '</span>',
          '</label>',
          '<label class="field seller-field">',
            '<span class="field-label">库存（件）</span>',
            '<input class="input" type="number" inputmode="numeric" step="1" min="0" max="1000000" value="' + esc(stock) + '" data-testid="seller-stock-input" aria-label="' + esc(title) + ' 库存（件）">',
            '<span class="field-hint">' + (lowBadge ? "低于提醒线，建议尽快补货" : "库存为 0 时商品会显示无货") + '</span>',
          '</label>',
          '<label class="field seller-field">',
            '<span class="field-label">报价状态</span>',
            '<select class="select" data-testid="seller-status-select" aria-label="' + esc(title) + ' 报价状态">',
              '<option value="active"' + (statusValue === "active" ? " selected" : "") + '>在售</option>',
              '<option value="paused"' + (statusValue === "paused" ? " selected" : "") + '>已下架</option>',
            '</select>',
            '<span class="field-hint">下架后买家不再看到此报价</span>',
          '</label>',
          '<button class="btn btn--jade seller-offer__save" type="button" data-testid="seller-save-offer">保存调整</button>',
        '</div>',
        '<p class="seller-row-error" role="alert" data-row-error hidden></p>',
        '<p class="seller-offer__updated">上次更新 ' + esc(fmtWhen(item.updated_at)) + '</p>',
      '</div>',
    '</article>',
  ].join("");
}

function bindInventory(ctx, view, data) {
  var form = ctx.root.querySelector(".seller-search");
  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var input = form.querySelector("input[name='q']");
      var q = input ? input.value.trim() : "";
      ctx.navigate(sellerHref({ tab: "inventory", q: q, page: 1 }));
    });
  }
  ctx.root.querySelectorAll('[data-testid="seller-save-offer"]').forEach(function (button) {
    button.addEventListener("click", async function () {
      var row = button.closest('[data-testid="seller-product-row"]');
      if (!row) return;
      var priceEl = row.querySelector('[data-testid="seller-price-input"]');
      var stockEl = row.querySelector('[data-testid="seller-stock-input"]');
      var statusEl = row.querySelector('[data-testid="seller-status-select"]');
      var errorEl = row.querySelector("[data-row-error]");
      var minCents = Number(priceEl ? priceEl.getAttribute("data-min-cents") : row.getAttribute("data-min-cents"));
      var maxCents = Number(priceEl ? priceEl.getAttribute("data-max-cents") : row.getAttribute("data-max-cents"));
      var priceRaw = priceEl ? String(priceEl.value).trim() : "";
      var stockRaw = stockEl ? String(stockEl.value).trim() : "";
      var statusValue = statusEl ? statusEl.value : "active";
      function fail(message, target) {
        if (errorEl) {
          errorEl.textContent = message;
          errorEl.hidden = false;
        }
        if (target) target.focus();
      }
      if (errorEl) errorEl.hidden = true;
      var yuan = Number(priceRaw);
      if (!priceRaw || !Number.isFinite(yuan)) {
        fail("请填写有效的价格（元）。", priceEl);
        return;
      }
      var priceCents = Math.round(yuan * 100);
      if (priceCents < minCents || priceCents > maxCents) {
        fail("价格需在 " + fmtMoney(minCents) + " 到 " + fmtMoney(maxCents) + " 之间。", priceEl);
        return;
      }
      if (!/^[0-9]+$/.test(stockRaw) || Number(stockRaw) > MAX_STOCK) {
        fail("库存必须是 0 到 1000000 之间的整数。", stockEl);
        return;
      }
      if (statusValue !== "active" && statusValue !== "paused") {
        fail("请选择有效的报价状态。", statusEl);
        return;
      }
      var offerId = row.getAttribute("data-offer-id") || "";
      button.disabled = true;
      var oldLabel = button.textContent;
      button.textContent = "保存中…";
      row.setAttribute("aria-busy", "true");
      if (priceEl) priceEl.setAttribute("aria-invalid", "false");
      if (stockEl) stockEl.setAttribute("aria-invalid", "false");
      try {
        await api.patch("/api/seller/products/" + encodeURIComponent(offerId), {
          price_cents: priceCents,
          stock: Number(stockRaw),
          status: statusValue,
        });
        toast("价格与库存已更新");
        reload(ctx);
      } catch (err) {
        button.disabled = false;
        button.textContent = oldLabel || "保存调整";
        row.removeAttribute("aria-busy");
        var fields = err instanceof ApiError && err.fields ? err.fields : {};
        if (fields.price_cents && priceEl) priceEl.setAttribute("aria-invalid", "true");
        if (fields.stock && stockEl) stockEl.setAttribute("aria-invalid", "true");
        fail(err instanceof ApiError ? err.message : "保存失败，请稍后再试", priceEl);
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Refunds
// ---------------------------------------------------------------------------

function refundFilterMarkup(view, data) {
  var counts = (data && data.counts) || {};
  var links = REFUND_FILTERS.map(function (filter) {
    var active = view.refundStatus === filter.key;
    var count = Math.max(0, num(counts[filter.key]));
    return '<a class="seller-filter__item' + (active ? " is-active" : "") + '" href="' + esc(sellerHref({ tab: "refunds", status: filter.key })) + '" data-link data-testid="seller-refund-filter-' + esc(filter.key) + '"' + (active ? ' aria-current="page"' : "") + '>' +
      '<span>' + esc(filter.label) + '</span><em>' + fmtNumber(count) + '</em></a>';
  }).join("");
  return '<nav class="seller-filters" aria-label="售后状态筛选">' + links + '</nav>';
}

function refundItemsMarkup(refund) {
  var items = Array.isArray(refund.items) ? refund.items : [];
  if (!items.length) return "";
  return '<div class="seller-refund__items">' + items.map(function (item) {
    return '<span class="seller-refund__item"><span class="seller-refund__item-art">' + icon("package", 16) + '</span><span><b>' + esc(item.title) + '</b><small>' + esc(item.sku_label) + ' · ×' + fmtNumber(item.quantity) + '</small></span><em>' + fmtMoney(item.unit_price_cents) + '</em></span>';
  }).join("") + '</div>';
}

function refundCardMarkup(refund) {
  var tone = REFUND_TONES[refund.status] || "muted";
  var canDecide = Boolean(refund.can_decide);
  var buttons = canDecide
    ? '<div class="seller-refund__actions">' +
        '<button class="btn btn--ghost btn--small" type="button" data-testid="refund-reject" data-refund-id="' + esc(refund.id) + '">驳回售后</button>' +
        '<button class="btn btn--primary btn--small" type="button" data-testid="refund-approve" data-refund-id="' + esc(refund.id) + '">同意退款</button>' +
      '</div>'
    : '<p class="seller-refund__decided">' + (refund.decision_note ? "处理备注：" + esc(refund.decision_note) : "处理结果已记录，无需再次决定。") + '</p>';
  return [
    '<article class="seller-refund" data-testid="refund-row" data-refund-id="' + esc(refund.id) + '">',
      '<div class="seller-refund__top">',
        '<div><span class="seller-refund__number">售后 #' + esc(refund.id) + '</span><span class="seller-mono seller-refund__order">' + esc(refund.order_no) + '</span></div>',
        statusChip(refund.status_label || refund.status, tone),
      '</div>',
      '<div class="seller-refund__summary">',
        '<span><small>买家</small><b>' + esc(refund.buyer_display || "匿名买家") + '</b></span>',
        '<span><small>申请退款</small><b>' + fmtMoney(refund.amount_cents) + '</b></span>',
        '<span><small>类型</small><b>' + esc(refund.kind_label || refund.kind) + '</b></span>',
        '<span><small>订单状态</small><b>' + esc(refund.order_status_label || refund.order_status) + '</b></span>',
      '</div>',
      refundItemsMarkup(refund),
      '<div class="seller-refund__reason"><span class="seller-refund__quote">买家说明</span><p>' + esc(refund.reason) + '</p>' + (refund.detail ? '<p class="seller-refund__detail">' + esc(refund.detail) + '</p>' : "") + '</div>',
      '<div class="seller-refund__footer">',
        '<div class="seller-refund__dates"><span>申请于 ' + esc(fmtWhen(refund.created_at)) + '</span><span>更新于 ' + esc(fmtWhen(refund.updated_at)) + '</span></div>',
        buttons,
      '</div>',
    '</article>',
  ].join("");
}

function refundsMarkup(view, data) {
  var items = data && Array.isArray(data.items) ? data.items : [];
  var total = num(data && data.total);
  var empty = emptyState({
    title: "没有符合条件的售后",
    message: view.refundStatus === "REQUESTED" ? "当前没有等待决定的售后申请。" : "换一个筛选条件，或回到全部售后查看处理记录。",
    actionLabel: view.refundStatus === "REQUESTED" ? "查看全部售后" : "回到待处理",
    actionHref: view.refundStatus === "REQUESTED" ? sellerHref({ tab: "refunds", status: "ALL" }) : sellerHref({ tab: "refunds", status: "REQUESTED" }),
    iconName: "return",
  });
  return [
    '<section class="seller-view seller-view--refunds" aria-labelledby="seller-refunds-title">',
      '<div class="seller-sectionhead">',
        '<div><p class="eyebrow eyebrow--jade">AFTER-SALES</p><h2 id="seller-refunds-title">售后处理</h2><p class="seller-sectionhead__copy">整单单店售后。通过后恢复库存与可用优惠券；驳回会把订单放回发起退款前的状态。</p></div>',
        refundFilterMarkup(view, data),
      '</div>',
      '<div class="seller-listmeta"><span>' + fmtNumber(total) + ' 条本店售后</span><span>' + icon("shield", 14) + '整单处理 · 单店铺 · 买家地址已隐藏</span></div>',
      '<div class="seller-refunds" data-testid="seller-refunds-list">' + (items.length ? items.map(refundCardMarkup).join("") : empty) + '</div>',
      paginationMarkup(view, data),
    '</section>',
  ].join("");
}

function bindRefunds(ctx, view, data) {
  var byId = {};
  (Array.isArray(data.items) ? data.items : []).forEach(function (item) {
    byId[String(item.id)] = item;
  });
  ctx.root.querySelectorAll('[data-testid="refund-approve"], [data-testid="refund-reject"]').forEach(function (button) {
    button.addEventListener("click", function () {
      var refundId = button.getAttribute("data-refund-id") || "";
      var refund = byId[refundId];
      if (!refund) return;
      var action = button.getAttribute("data-testid") === "refund-approve" ? "approve" : "reject";
      openRefundModal(ctx, refund, action);
    });
  });
}

function openRefundModal(ctx, refund, action) {
  var approving = action === "approve";
  var title = approving ? "同意这笔退款？" : "驳回这笔售后？";
  var submitLabel = approving ? "确认同意退款" : "确认驳回售后";
  var copy = approving
    ? "通过后，订单会从售后处理中转为已退款；整单商品库存会被补回，已用优惠券按是否仍在有效期内恢复为可用或过期。"
    : "驳回后，订单会回到买家发起退款前的状态；库存与优惠券不会恢复。买家仍会看到保留的原因与处理记录。";
  var items = Array.isArray(refund.items) ? refund.items : [];
  var itemsText = items.length
    ? items.map(function (item) { return esc(item.title) + ' ×' + fmtNumber(item.quantity); }).join("；")
    : "本店订单商品";
  var formHtml = [
    '<form id="seller-refund-form" class="seller-form" novalidate>',
      '<div class="seller-modal-summary">',
        '<span><small>订单</small><b class="seller-mono">' + esc(refund.order_no) + '</b></span>',
        '<span><small>退款金额</small><b>' + fmtMoney(refund.amount_cents) + '</b></span>',
        '<span><small>买家</small><b>' + esc(refund.buyer_display || "匿名买家") + '</b></span>',
      '</div>',
      '<p class="seller-modal-copy">' + esc(copy) + '</p>',
      '<p class="seller-modal-items">' + icon("package", 15) + '<span>' + itemsText + '</span></p>',
      '<label class="field"><span class="field-label">处理备注（可选，最长 200 字）</span>',
        '<textarea class="textarea" name="note" maxlength="200" rows="3" placeholder="给买家一个清楚的处理说明" data-testid="refund-note-input"></textarea>',
      '</label>',
      '<p class="form-error" role="alert" data-refund-error hidden></p>',
    '</form>',
  ].join("");
  var footer = '<button class="btn btn--ghost" type="button" data-cancel>取消</button><button class="btn ' + (approving ? "btn--primary" : "btn--danger") + '" type="submit" form="seller-refund-form" data-testid="refund-decision-submit">' + esc(submitLabel) + '</button>';
  var modal = openModal({ title: title, subtitle: "整单、单店铺售后；操作会写入订单事件。", body: formHtml, footer: footer });
  modal.dialog.classList.add("seller-modal");
  modal.footer.classList.add("seller-modal__footer");
  var form = modal.body.querySelector("#seller-refund-form");
  var errorEl = modal.body.querySelector("[data-refund-error]");
  var submit = modal.footer.querySelector('[data-testid="refund-decision-submit"]');
  var cancel = modal.footer.querySelector("[data-cancel]");
  if (cancel) cancel.addEventListener("click", function () { modal.close(); });
  if (!form || !submit) return;
  var busy = false;
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (busy) return;
    var noteEl = form.querySelector('[data-testid="refund-note-input"]');
    var note = noteEl ? noteEl.value.trim() : "";
    if (note.length > 200) {
      if (errorEl) {
        errorEl.textContent = "处理备注最多 200 个字符。";
        errorEl.hidden = false;
      }
      if (noteEl) noteEl.focus();
      return;
    }
    busy = true;
    submit.disabled = true;
    submit.textContent = "处理中…";
    if (errorEl) errorEl.hidden = true;
    try {
      await api.post("/api/seller/refunds/" + encodeURIComponent(String(refund.id)) + "/decision", {
        action: action,
        note: note,
      });
      modal.close();
      toast(approving ? "已同意退款，订单与资源已更新" : "已驳回售后，订单已恢复原状态");
      reload(ctx);
    } catch (err) {
      busy = false;
      submit.disabled = false;
      submit.textContent = submitLabel;
      if (errorEl) {
        errorEl.textContent = err instanceof ApiError ? err.message : "售后处理失败，请稍后再试";
        errorEl.hidden = false;
      }
    }
  });
}

