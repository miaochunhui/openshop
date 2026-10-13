// AI mediator presentation helpers. No framework, no template literals, no external assets.
import { escapeHtml, icon, money, priceHtml, KIND_LABELS } from "../components/index.js";

const EXAMPLES = [
  "想要一副降噪耳机，预算 1000 元以内，最好两天内到，自营或品牌店都可以。",
  "给刚搬的新家挑一张舒服的双人沙发，预算 4000 元，愿意等拼团。",
  "周末去露营，想买一张轻便折叠桌，700 元以内，本周能到。"
];

function safeCover(value) {
  const text = String(value || "");
  return text.startsWith("/") ? text : "/web/assets/brand/favicon.svg";
}

function kindLabel(kind) {
  return KIND_LABELS[kind] || "供给";
}

function num(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function pageShellHtml() {
  return [
    '<section class="ai-page container" data-testid="ai-page">',
    '<header class="ai-hero">',
    '<div class="ai-hero__copy">',
    '<p class="eyebrow">NEUTRAL AI BROKER · 开集中介</p>',
    '<h1>把需求说清楚，<br><em>让开集替你先挑一遍。</em></h1>',
    '<p class="ai-lede">不是另一个搜索框。AI 中介会读真实库存、报价、时效与拼团条件，替你说清该选哪一件、为什么，以及要放弃什么。</p>',
    '<div class="ai-hero__marks"><span>' + icon("shield", 15) + ' 真实供给</span><span>' + icon("wallet", 15) + ' 服务端复价</span><span>' + icon("user", 15) + ' 由你确认</span></div>',
    '</div>',
    '<aside class="ai-seal" aria-label="AI 中介原则">',
    '<span class="ai-seal__label">OPENSHOP / AI</span>',
    '<b>不替你付款。<br>只替你把问题问对。</b>',
    '<span class="ai-seal__line"></span>',
    '<small>模型给建议，服务端给事实。加入购物车之前，选择权始终在你。</small>',
    '</aside>',
    '</header>',
    '<div class="ai-workbench">',
    '<div class="ai-main">',
    '<section class="ai-card ai-brief" aria-labelledby="ai-brief-title">',
    '<div class="ai-block-head"><div><p class="eyebrow">YOUR BRIEF</p><h2 id="ai-brief-title">这次想买什么？</h2></div><span class="ai-stepno">01</span></div>',
    '<form id="ai-form" data-testid="ai-form" novalidate>',
    '<label class="ai-field-label" for="ai-input">带上预算、时间或偏好，越具体越好</label>',
    '<div class="ai-input-wrap"><textarea id="ai-input" data-testid="ai-input" class="ai-textarea" name="message" rows="4" maxlength="500" placeholder="例如：想给租房的小客厅挑一盏落地灯，400 元以内，今晚下单最好周末到，能接受拼团。" aria-describedby="ai-count ai-form-hint"></textarea><span class="ai-count"><span id="ai-count">0</span>/500</span></div>',
    '<div class="ai-examples" aria-label="试试这些委托">',
    EXAMPLES.map(function (text, index) {
      return '<button type="button" class="ai-example" data-testid="ai-example" data-example="' + escapeHtml(text) + '"><span>0' + (index + 1) + '</span>' + escapeHtml(text) + '</button>';
    }).join(""),
    '</div>',
    '<p class="ai-error" id="ai-error" data-testid="ai-error" role="alert" hidden></p>',
    '<div class="ai-submit-row"><p id="ai-form-hint" class="ai-hint">' + icon("info", 14) + ' AI 不会自动下单。方案最多保留 15 分钟，采用后仍需你在购物车里确认。</p><button class="btn btn--primary ai-submit" id="ai-submit" data-testid="ai-submit" type="submit" disabled>让 AI 比一比 ' + icon("arrowRight", 16) + '</button></div>',
    '</form>',
    '<p class="ai-disclosure" id="ai-disclosure" data-testid="ai-disclosure">提交即表示你同意将本次需求和最近对话发送给 AI 模型服务；不会发送账号、地址、订单或支付信息。AI 建议可能出错，价格与库存以服务端为准。</p>',
    '</section>',
    '<section class="ai-thread-section" id="ai-thread-section" hidden aria-label="AI 中介对话">',
    '<div class="ai-section-line"><span>对话与判断</span><span class="ai-rule"></span><button class="ai-reset" id="ai-reset" data-testid="ai-reset" type="button" hidden>重新委托</button></div>',
    '<div class="ai-thread" id="ai-thread" data-testid="ai-thread" aria-live="polite"></div>',
    '</section>',
    '<section class="ai-result" id="ai-result-region" hidden aria-label="AI 中介方案"></section>',
    '</div>',
    '<aside class="ai-side">',
    '<section class="ai-card ai-status-card" id="ai-status-card" data-testid="ai-status" aria-live="polite">',
    '<div class="ai-status-loading"><span class="ai-pulse"></span> 正在确认 AI 中介状态…</div>',
    '</section>',
    '<section class="ai-card ai-rules" aria-labelledby="ai-rules-title">',
    '<p class="eyebrow">THE HOUSE RULES</p><h2 id="ai-rules-title">中介的三条规矩</h2>',
    '<ol><li><span>01</span><div><b>不编供给</b><p>只从开店者正在售卖的 offer 与 SKU 里挑选，价格与库存回到服务端复核。</p></div></li><li><span>02</span><div><b>不拿佣金排序</b><p>自营、品牌、源头工厂同台比较，按你的预算、时间和偏好做取舍。</p></div></li><li><span>03</span><div><b>不越过你</b><p>AI 只提出方案。是否加入购物车、是否结算，始终由你确认。</p></div></li></ol>',
    '</section>',
    '</aside>',
    '</div>',
    '</section>'
  ].join("");
}

export function loadingStatusHtml() {
  return '<div class="ai-status-loading"><span class="ai-pulse"></span> 正在确认 AI 中介状态…</div>';
}

export function statusCardHtml(status, user) {
  if (!status) return loadingStatusHtml();
  const enabled = Boolean(status.enabled);
  const usage = status.usage || {};
  const limits = status.limits || {};
  const userRemaining = usage.user_remaining_today;
  const globalRemaining = usage.global_remaining_today;
  const exhausted = enabled && ((typeof globalRemaining === "number" && globalRemaining <= 0) || (user && typeof userRemaining === "number" && userRemaining <= 0));
  const badge = enabled
    ? '<span class="ai-live"><i></i> 在线 · ' + escapeHtml(status.model_label || "AI") + '</span>'
    : '<span class="ai-live ai-live--off"><i></i> 未配置</span>';
  const parts = [
    '<div class="ai-status-card__top"><div><p class="eyebrow">BROKER DESK</p><h2>中介席位</h2></div><span data-testid="ai-mode">' + badge + '</span></div>'
  ];
  if (!enabled) {
    parts.push('<p class="ai-status-message">' + escapeHtml(status.message || "AI 中介当前未配置。") + '</p>');
    parts.push('<a class="btn btn--ghost btn--small" href="/search" data-link>先去逛真实供给 ' + icon("arrowRight", 14) + '</a>');
  } else if (exhausted) {
    parts.push('<p class="ai-status-message">今日 AI 中介额度已经用完。额度在每天 00:00（北京时间）重置；传统选购、结算和商家功能不受影响。</p>');
    parts.push('<a class="btn btn--ghost btn--small" href="/search" data-link>去搜索商品 ' + icon("arrowRight", 14) + '</a>');
  } else {
    parts.push('<p class="ai-status-message">模型先读懂需求，再回真实供给里挑选。每次推荐都会回到服务端复核库存、报价和权限。</p>');
    if (!user) {
      parts.push('<div class="ai-login-callout"><span>' + icon("user", 17) + '</span><p>登录后才能把推荐安全地加入购物车，也能看到你剩余的 AI 次数。</p><button type="button" class="btn btn--primary btn--small" data-testid="ai-login" id="ai-login">登录后使用</button></div>');
    } else {
      parts.push('<div class="ai-quota-box" data-testid="ai-quota"><div><span>你今日还可委托</span><b>' + (userRemaining === null || userRemaining === undefined ? "—" : escapeHtml(userRemaining)) + ' 次</b></div><div><span>全站今日剩余</span><b>' + (globalRemaining === null || globalRemaining === undefined ? "—" : escapeHtml(globalRemaining)) + ' 次</b></div></div>');
    }
    parts.push('<p class="ai-limit-note">每账号每日 ' + escapeHtml(limits.user_daily || "—") + ' 次 · 每 IP ' + escapeHtml(limits.ip_window || "—") + ' 次 / ' + escapeHtml(Math.max(1, Math.round((limits.ip_window_seconds || 600) / 60))) + ' 分钟</p>');
  }
  parts.push('<p class="ai-privacy-brief">' + icon("shield", 14) + ' 不向模型发送账号、地址、订单或支付信息。</p>');
  return parts.join("");
}

export function userBubbleHtml(text) {
  return '<article class="ai-turn ai-turn--user" data-testid="ai-turn-user"><div class="ai-turn__meta">你的委托 <span>' + icon("user", 13) + '</span></div><div class="ai-turn__bubble">' + escapeHtml(text) + '</div></article>';
}

export function assistantBubbleHtml(text) {
  return '<article class="ai-turn ai-turn--assistant" data-testid="ai-answer"><div class="ai-turn__meta"><span class="ai-mini-mark">AI</span> AI 中介 <span>基于真实供给</span></div><div class="ai-turn__bubble">' + escapeHtml(text) + '</div></article>';
}

export function loadingBubbleHtml() {
  return '<article class="ai-turn ai-turn--assistant ai-loading" data-testid="ai-loading" role="status"><div class="ai-turn__meta"><span class="ai-mini-mark">AI</span> AI 正在读供给 <span>通常 10–30 秒</span></div><div class="ai-turn__bubble"><span class="ai-typing"><i></i><i></i><i></i></span><span>正在比对库存、时效、拼团条件和你的预算…</span></div></article>';
}

export function errorBubbleHtml(message) {
  return '<article class="ai-turn ai-turn--assistant ai-turn--error" data-testid="ai-error-bubble"><div class="ai-turn__meta">请求没有完成</div><div class="ai-turn__bubble">' + escapeHtml(message) + '</div></article>';
}

function needChips(needs) {
  if (!Array.isArray(needs) || !needs.length) return "";
  return '<div class="ai-needs" data-testid="ai-needs">' + needs.map(function (need) {
    return '<span class="chip chip--ghost">' + icon("check", 12) + ' ' + escapeHtml(need) + '</span>';
  }).join("") + '</div>';
}

function questionsBlock(questions, testid) {
  if (!Array.isArray(questions) || !questions.length) return "";
  return '<div class="ai-questions" data-testid="' + testid + '"><span class="ai-question-mark">?</span><div><b>AI 还想确认</b>' + questions.map(function (question) {
    return '<p>' + escapeHtml(question) + '</p>';
  }).join("") + '</div></div>';
}

function recommendationBlock(rec, adopted) {
  if (!rec) return "";
  const group = rec.offer && rec.offer.group;
  const groupText = group ? '<span class="ai-group-badge">' + icon("gift", 13) + ' ' + escapeHtml(group.size) + ' 人团 · 未成团</span>' : "";
  const modeLabel = rec.buy_mode === "group" ? "拼团价" : "服务端实时价";
  const delivery = num(rec.offer && rec.offer.delivery_eta_days, 0);
  const shopKind = rec.shop && rec.shop.kind ? kindLabel(rec.shop.kind) : "供给";
  const verified = rec.shop && rec.shop.verified ? '<span class="ai-verified">' + icon("shield", 12) + ' 已验证店铺</span>' : "";
  const market = rec.market_price_cents > rec.unit_price_cents
    ? '<span class="was-price">' + money(rec.market_price_cents) + '</span>'
    : "";
  const tradeoff = rec.tradeoffs ? '<div class="ai-rationale ai-rationale--tradeoff" data-testid="ai-tradeoff"><span>取舍</span><p>' + escapeHtml(rec.tradeoffs) + '</p></div>' : "";
  const groupNote = group
    ? '<p class="ai-group-note">' + icon("clock", 14) + ' 该供给按 ' + escapeHtml(group.size) + ' 人拼团展示。加入购物车后仍需按订单页完成成团与模拟支付；未成团前不承诺发货。</p>'
    : "";
  const actions = adopted
    ? '<div class="ai-adopted" data-testid="ai-adopted"><span class="ai-adopted__mark">' + icon("check", 18) + '</span><div><b>方案已加入购物车</b><p>数量、真实报价和成团信息请在确认页复核。AI 没有创建订单，也没有付款。</p><a class="btn btn--primary btn--small" href="/cart" data-link>去购物车确认 ' + icon("arrowRight", 14) + '</a></div></div>'
    : '<div class="ai-offer__actions"><a class="btn btn--ghost btn--small" href="/p/' + encodeURIComponent(rec.product.slug) + '" data-link>先看商品详情</a><button class="btn btn--primary" type="button" data-testid="ai-adopt" id="ai-adopt" data-plan-id="' + escapeHtml(rec.plan_id || "") + '">采用方案，加入购物车 ' + icon("cart", 16) + '</button></div>';
  return [
    '<article class="ai-offer" data-testid="ai-recommendation">',
    '<div class="ai-offer__media"><img src="' + escapeHtml(safeCover(rec.product.cover)) + '" alt="' + escapeHtml(rec.product.title) + '" loading="lazy" decoding="async"><span class="ai-offer__kind">' + escapeHtml(shopKind) + '</span></div>',
    '<div class="ai-offer__body">',
    '<div class="ai-offer__kicker"><span>' + escapeHtml(rec.product.brand || "开集精选") + '</span><span>' + escapeHtml(rec.product.category || "") + '</span>' + groupText + '</div>',
    '<h3>' + escapeHtml(rec.headline || rec.product.title) + '</h3>',
    '<p class="ai-offer__title">' + escapeHtml(rec.product.title) + ' · ' + escapeHtml(rec.sku.name) + '</p>',
    '<div class="ai-offer__shop"><span>' + escapeHtml(rec.shop.name) + '</span><span>' + escapeHtml(shopKind) + '</span>' + verified + '</div>',
    '<div class="ai-offer__facts"><span>' + icon("box", 14) + ' ' + escapeHtml(rec.offer.name) + '</span><span>' + icon("truck", 14) + ' 预计 ' + escapeHtml(delivery) + ' 天送达</span><span>' + icon("shield", 14) + ' 库存 ' + escapeHtml(rec.offer.stock) + '</span></div>',
    '<div class="ai-price-row"><div><small>' + escapeHtml(modeLabel) + '</small>' + priceHtml(rec.unit_price_cents, { size: "price--lg" }) + ' ' + market + '</div><span class="ai-price-note">服务端复算<br>不自动下单</span></div>',
    '<div class="ai-rationale" data-testid="ai-reason"><span>为什么是它</span><p>' + escapeHtml(rec.why) + '</p></div>',
    tradeoff,
    groupNote,
    actions,
    '<p class="ai-offer__smallprint">方案仅本人可见，' + escapeHtml(rec.plan_id ? "15" : "—") + ' 分钟内只可采纳一次；金额与库存以服务端校验为准。</p>',
    '</div>',
    '</article>'
  ].join("");
}

function alternativesBlock(alternatives) {
  if (!Array.isArray(alternatives) || !alternatives.length) return "";
  return [
    '<section class="ai-alternatives"><div class="ai-section-line"><span>也值得比较</span><span class="ai-rule"></span></div><div class="ai-alternative-grid">',
    alternatives.map(function (alt) {
      return '<article class="ai-alternative" data-testid="ai-alternative">' +
        '<img src="' + escapeHtml(safeCover(alt.product.cover)) + '" alt="" loading="lazy" decoding="async">' +
        '<div><p class="ai-alternative__shop">' + escapeHtml(alt.shop.name) + '</p><h4>' + escapeHtml(alt.product.title) + '</h4><p class="ai-alternative__why">' + escapeHtml(alt.why) + '</p><div class="ai-alternative__foot">' + priceHtml(alt.unit_price_cents, { size: "price--small" }) + '<a href="/p/' + encodeURIComponent(alt.product.slug) + '" data-link>看看 ' + icon("arrowRight", 13) + '</a></div></div>' +
        '</article>';
    }).join(""),
    '</div></section>'
  ].join("");
}

export function resultHtml(data, adopted) {
  if (!data) return "";
  const rec = data.recommendation || null;
  const questions = questionsBlock(data.questions, "ai-question");
  const parts = [
    '<div class="ai-result-inner">',
    '<div class="ai-result-head"><div><p class="eyebrow">AI MEDIATION</p><h2>一条可以自己核对的方案</h2></div><span class="ai-latency">' + escapeHtml(data.model_label || "AI") + ' · ' + escapeHtml(Math.max(1, Math.round(num(data.latency_ms) / 1000))) + 's</span></div>',
    needChips(data.needs),
    recommendationBlock(rec, adopted),
    questions,
    alternativesBlock(data.alternatives),
    '<div class="ai-result-foot"><p>' + icon("info", 14) + ' ' + escapeHtml(data.disclosure || "AI 建议仅供参考，价格与库存以服务端为准。") + '</p><span data-testid="ai-quota">本次剩余额度：' + escapeHtml((data.quota && data.quota.user_remaining_today !== undefined) ? data.quota.user_remaining_today : "—") + ' 次</span></div>',
    '</div>'
  ];
  if (!rec) parts.splice(4, 0, '<div class="ai-clarify"><span class="ai-clarify__mark">?</span><p>还不足以给出可靠推荐。补充上面的问题，或把预算、时间改得更具体一些。</p></div>');
  return parts.join("");
}

export function loginCalloutHtml() {
  return '<div class="ai-login-callout"><span>' + icon("user", 17) + '</span><p>登录后才能使用 AI 中介并采纳方案。</p><button type="button" class="btn btn--primary btn--small" data-testid="ai-login" id="ai-login">去登录</button></div>';
}
