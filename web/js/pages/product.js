// 商品详情：图集、多供给比较、SKU/数量、真实购物车、拼团、评价与规格。
// 服务端始终是价格/库存/成团状态的权威；页面只用 API 返回值试算展示。
import { api, ApiError } from "../core/api.js";
import { state, updateCartCount, patchState } from "../core/store.js";
import {
  emptyState,
  escapeHtml,
  formatDate,
  icon,
  money,
  openAuth,
  priceHtml,
  productCard,
  ratingStars,
  toast,
} from "../components/index.js";

const KIND_TEXT = { self: "官方自营", brand: "品牌旗舰", factory: "源头工厂" };
const REVIEW_SORT_LABELS = {
  recent: "最新评价",
  helpful: "最有帮助",
  rating_desc: "评分从高到低",
};
const asArray = (value) => (Array.isArray(value) ? value : []);
const asText = (value) => (typeof value === "string" ? value.trim() : "");
const asNumber = (value, fallback = null) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};
const isAborted = (signal) => Boolean(signal && signal.aborted);
const formatInt = (value) => {
  const n = asNumber(value);
  return n === null ? "" : n.toLocaleString("zh-CN");
};
const encodeSlug = (value) => encodeURIComponent(String(value || ""));
const categoryHref = (slug) => "/search?category=" + encodeURIComponent(String(slug || ""));

function normalizeProduct(raw) {
  const product = raw && typeof raw === "object" ? raw : {};
  const offers = [];
  const seenOffers = new Set();
  for (const offer of asArray(product.offers)) {
    const id = asNumber(offer && offer.id);
    if (id === null || !Number.isInteger(id) || seenOffers.has(id)) continue;
    seenOffers.add(id);
    offers.push({ ...offer, id });
  }
  const skus = [];
  const seenSkus = new Set();
  for (const sku of asArray(product.skus)) {
    const id = asNumber(sku && sku.id);
    if (id === null || !Number.isInteger(id) || seenSkus.has(id)) continue;
    seenSkus.add(id);
    skus.push({ ...sku, id });
  }
  const seenImages = new Set();
  const images = [];
  for (const image of asArray(product.images)) {
    const src = asText(image && image.src);
    if (!src || seenImages.has(src)) continue;
    seenImages.add(src);
    images.push({ src, alt: asText(image && image.alt) || asText(product.title) + " 商品图" });
  }
  if (!images.length && asText(product.cover)) {
    images.push({ src: asText(product.cover), alt: asText(product.cover_alt) || asText(product.title) + " 商品图" });
  }
  const summary = product.reviews && typeof product.reviews === "object" && product.reviews.summary && typeof product.reviews.summary === "object"
    ? product.reviews.summary
    : null;
  const initialReviews = product.reviews && typeof product.reviews === "object" ? asArray(product.reviews.items) : [];
  const rating = asNumber(summary && summary.rating, asNumber(product.rating));
  const reviewCount = asNumber(summary && summary.count, asNumber(product.review_count));
  const initialOffer = offers.find((offer) => offer.is_primary && asNumber(offer.stock, 0) > 0)
    || offers.find((offer) => asNumber(offer.stock, 0) > 0)
    || offers[0]
    || null;
  const initialSku = skus[0] || null;
  return {
    product,
    offers,
    skus,
    images,
    summary,
    initialReviews,
    rating,
    reviewCount,
    initialOfferId: initialOffer ? initialOffer.id : null,
    initialSkuId: initialSku ? initialSku.id : null,
    related: asArray(product.related).filter((card) => card && asText(card.slug)),
  };
}

function unitFor(offer, sku) {
  if (!offer) return null;
  const delta = asNumber(sku && sku.price_delta_cents, 0) || 0;
  const base = asNumber(offer.price_cents);
  return base === null ? null : Math.max(0, base + delta);
}

function groupUnitFor(offer, sku) {
  const group = offer && offer.group && typeof offer.group === "object" ? offer.group : null;
  if (!group) return null;
  const delta = asNumber(sku && sku.price_delta_cents, 0) || 0;
  const base = asNumber(group.price_cents);
  return base === null ? null : Math.max(0, base + delta);
}

function offerStock(offer) {
  const value = asNumber(offer && offer.stock);
  return value === null ? 0 : Math.max(0, Math.floor(value));
}

function groupFor(offer) {
  return offer && offer.group && typeof offer.group === "object" ? offer.group : null;
}

function groupStock(offer) {
  const group = groupFor(offer);
  const value = asNumber(group && group.stock);
  return value === null ? 0 : Math.max(0, Math.floor(value));
}

function deliveryParts(offer) {
  const delivery = offer && offer.delivery && typeof offer.delivery === "object" ? offer.delivery : {};
  const etaRaw = asNumber(delivery.eta_days, asNumber(offer && offer.delivery_eta_days));
  const label = asText(delivery.label) || (etaRaw !== null ? "预计 " + etaRaw + " 天送达" : "");
  const note = asText(delivery.note);
  const fee = asNumber(offer && offer.shipping_fee_cents);
  const shipping = fee === null ? "" : (fee === 0 ? "免运费" : "运费 " + money(fee));
  return { label, note, shipping };
}

function loadingMarkup() {
  return `<div class="discovery-page pdp-page" data-testid="product-root">
    <div class="container pdp-loading" role="status" aria-label="正在打开商品">
      <nav class="pdp-crumbs" aria-hidden="true"><span class="skeleton" style="width:180px;height:14px"></span></nav>
      <div class="pdp-loading__grid">
        <div class="skeleton pdp-loading__art"></div>
        <div class="pdp-loading__copy">
          <span class="skeleton" style="height:14px;width:120px"></span>
          <span class="skeleton" style="height:64px;width:82%;margin-top:18px"></span>
          <span class="skeleton" style="height:16px;width:56%;margin-top:18px"></span>
          <span class="skeleton" style="height:124px;width:100%;margin-top:28px;border-radius:18px"></span>
          <span class="skeleton" style="height:72px;width:100%;margin-top:16px;border-radius:18px"></span>
          <span class="skeleton" style="height:54px;width:100%;margin-top:16px;border-radius:999px"></span>
        </div>
      </div>
      <p class="sr-only">正在从服务端取回商品与真实在售报价。</p>
    </div>
  </div>`;
}

function errorMarkup(message, notFound) {
  return `<div class="discovery-page pdp-page" data-testid="product-root">
    <div class="container pdp-state">
      <section class="pdp-state__card" role="alert" aria-labelledby="pdp-error-title">
        <p class="eyebrow">${notFound ? "NOT FOUND" : "SERVICE"}</p>
        <h1 id="pdp-error-title">${notFound ? "这件东西不在集上了" : "商品页暂时没有打开"}</h1>
        <p class="lede">${escapeHtml(message || "请稍后再试，或回到搜索重新挑选。")}</p>
        <div class="cluster mt-3">
          <button class="btn btn--primary" type="button" data-pdp-retry>再试一次</button>
          <a class="btn btn--ghost" href="/search" data-link>去搜索逛逛</a>
        </div>
        <p class="tiny muted mt-2">所有商品与价格均为虚构演示数据。</p>
      </section>
    </div>
  </div>`;
}

function breadcrumbsMarkup(product) {
  const category = product.category && typeof product.category === "object" ? product.category : null;
  const catName = category ? asText(category.name) : "";
  const catSlug = category ? asText(category.slug) : "";
  const title = asText(product.title) || "商品详情";
  return `<nav class="pdp-crumbs" aria-label="面包屑">
    <ol>
      <li><a href="/" data-link>开集首页</a></li>
      <li><a href="/search" data-link>全部商品</a></li>
      ${catName && catSlug ? `<li><a href="${categoryHref(catSlug)}" data-link>${escapeHtml(catName)}</a></li>` : ""}
      <li aria-current="page">${escapeHtml(title)}</li>
    </ol>
  </nav>`;
}

function galleryMarkup(images, product) {
  const count = images.length;
  const first = count ? images[0] : null;
  const badges = asArray(product.badges).slice(0, 2);
  return `<section class="pdp-gallery" aria-label="商品图片">
    <div class="pdp-gallery__stage">
      ${first
        ? `<img class="pdp-gallery__image" data-gallery-main src="${escapeHtml(first.src)}" alt="${escapeHtml(first.alt)}" fetchpriority="high" decoding="async">`
        : `<div class="pdp-gallery__placeholder"><span>${icon("box", 38)}</span><b>${escapeHtml(asText(product.title) || "开集选物")}</b><em>商品图暂缺</em></div>`}
      <div class="pdp-gallery__badges">${badges.map((badge, index) => `<span class="badge ${index ? "badge--light" : "badge--jade"}">${escapeHtml(badge)}</span>`).join("")}</div>
      ${count > 1 ? `<div class="pdp-gallery__controls">
        <button class="pdp-gallery__nav" type="button" data-gallery-step="-1" aria-label="上一张商品图">${icon("arrowLeft", 18)}</button>
        <span class="pdp-gallery__counter" aria-live="polite" data-gallery-counter>1 / ${count}</span>
        <button class="pdp-gallery__nav" type="button" data-gallery-step="1" aria-label="下一张商品图">${icon("arrowRight", 18)}</button>
      </div>` : ""}
    </div>
    ${count > 1 ? `<div class="pdp-gallery__thumbs" role="group" aria-label="商品图片缩略图">
      ${images.map((image, index) => `<button class="pdp-gallery__thumb${index === 0 ? " is-active" : ""}" type="button" data-gallery-index="${index}" aria-pressed="${index === 0 ? "true" : "false"}" aria-label="查看第 ${index + 1} 张商品图：${escapeHtml(image.alt)}"><img src="${escapeHtml(image.src)}" alt="" loading="lazy" decoding="async"></button>`).join("")}
    </div>` : ""}
    <p class="pdp-gallery__hint">${icon("camera", 13)} 图片为项目内自绘演示素材${count > 1 ? " · 可用左右方向键换图" : ""}</p>
  </section>`;
}

function badgeMarkup(badges) {
  return asArray(badges).map((badge) => `<span class="chip pdp-badge">${escapeHtml(badge)}</span>`).join("");
}

function reviewRatingText(rating) {
  const n = asNumber(rating);
  return n === null ? "暂无评分" : n.toFixed(1);
}

function ratingRowMarkup(product, rating, reviewCount) {
  const sold = asNumber(product.sold_count);
  return `<div class="pdp-rating-row">
    ${rating !== null ? `<span class="pdp-rating-stars">${ratingStars(rating)}</span><b class="pdp-rating-number">${reviewRatingText(rating)}</b>` : `<span class="muted">暂无评分</span>`}
    ${reviewCount !== null ? `<a class="pdp-review-link" href="#pdp-reviews">${formatInt(reviewCount)} 条评价</a>` : ""}
    ${sold !== null ? `<span class="pdp-sold">已售 ${formatInt(sold)}</span>` : ""}
    ${product.category && asText(product.category.name) ? `<span class="pdp-category-label">${escapeHtml(asText(product.category.name))}</span>` : ""}
  </div>`;
}

function offerCardMarkup(offer, sku, selected) {
  const id = offer.id;
  const shop = offer.shop && typeof offer.shop === "object" ? offer.shop : {};
  const shopName = asText(shop.name) || "开集店铺";
  const kind = asText(offer.kind);
  const kindLabel = asText(offer.kind_label) || KIND_TEXT[kind] || "可售供给";
  const unit = unitFor(offer, sku);
  const group = groupFor(offer);
  const gUnit = groupUnitFor(offer, sku);
  const stock = offerStock(offer);
  const delivery = deliveryParts(offer);
  const groupLabel = group
    ? `<span class="pdp-offer-group" data-offer-group-price="${id}">${gUnit !== null ? priceHtml(gUnit, { className: "price--small pdp-offer-group-price" }) : "拼团价"}<em>${formatInt(group.size)} 人团</em></span>`
    : "";
  const disabled = stock <= 0;
  return `<label class="pdp-offer-option${selected ? " is-selected" : ""}${disabled ? " is-soldout" : ""}" data-testid="offer-option" data-offer-card="${id}" data-offer-kind="${escapeHtml(kind)}">
    <input class="pdp-offer-option__radio" type="radio" name="pdp-offer" value="${id}" ${selected ? "checked" : ""} ${disabled ? "disabled" : ""} aria-label="选择${escapeHtml(kindLabel)}供给：${escapeHtml(shopName)} ${escapeHtml(asText(offer.name) || "标准报价")}">
    <span class="pdp-offer-option__body">
      <span class="pdp-offer-option__top">
        <span class="pdp-kind-tag">${icon(kind === "self" ? "shield" : kind === "factory" ? "factory" : "store", 12)} ${escapeHtml(kindLabel)}</span>
        <b>${escapeHtml(asText(offer.name) || "标准报价")}</b>
        ${offer.is_primary ? `<span class="pdp-offer-primary">主推</span>` : ""}
      </span>
      <span class="pdp-offer-shop">${escapeHtml(shopName)}${shop.verified ? `<em>${icon("check", 11)} 已认证</em>` : ""}${delivery.note ? `<span>${escapeHtml(delivery.note)}</span>` : ""}</span>
      <span class="pdp-offer-option__facts">
        <span class="pdp-offer-unit" data-offer-price="${id}">${unit !== null ? priceHtml(unit, { className: "price--small" }) : "暂不可售"}</span>
        ${groupLabel}
        <span class="pdp-offer-stock">${stock > 0 ? "现货 " + formatInt(stock) + " 件" : "暂时缺货"}</span>
      </span>
      <span class="pdp-offer-option__delivery">${icon("truck", 12)} ${escapeHtml(delivery.label || "配送时间以结算页为准")}${delivery.shipping ? ` · ${escapeHtml(delivery.shipping)}` : ""}</span>
      ${asArray(offer.services).length ? `<span class="pdp-offer-option__services">${asArray(offer.services).slice(0, 4).map((service) => `<em>${escapeHtml(service)}</em>`).join("")}</span>` : ""}
    </span>
  </label>`;
}

function skuMarkup(sku, selected) {
  const delta = asNumber(sku.price_delta_cents, 0) || 0;
  return `<button type="button" class="pdp-sku${selected ? " is-active" : ""}" data-sku-button="${sku.id}" aria-pressed="${selected ? "true" : "false"}">
    <span>${escapeHtml(asText(sku.name) || "默认规格")}</span>
    ${delta > 0 ? `<em>+${money(delta)}</em>` : ""}
  </button>`;
}

function reviewCardMarkup(review) {
  if (!review || typeof review !== "object") return "";
  const rating = asNumber(review.rating);
  const created = asText(review.created_at);
  const tags = asArray(review.tags);
  const helpful = asNumber(review.helpful_count);
  const author = asText(review.author) || "开集用户";
  return `<article class="pdp-review-card">
    <header class="pdp-review-card__head">
      <span class="pdp-review-avatar" aria-hidden="true">${escapeHtml(author.slice(0, 1))}</span>
      <div class="pdp-review-card__who">
        <b>${escapeHtml(author)}</b>
        <span>${review.verified ? "已购 · 真实购买" : "体验分享"}${review.sku_label ? " · " + escapeHtml(review.sku_label) : ""}</span>
      </div>
      ${dateLabel(created)}
    </header>
    <div class="pdp-review-card__meta">
      ${rating !== null ? ratingStars(rating) : ""}
      ${rating !== null ? `<span class="pdp-review-score">${rating.toFixed(1)}</span>` : ""}
    </div>
    ${asText(review.title) ? `<h4>${escapeHtml(asText(review.title))}</h4>` : ""}
    ${asText(review.body) ? `<p>${escapeHtml(asText(review.body))}</p>` : ""}
    <footer class="pdp-review-card__foot">
      ${tags.length ? `<span class="pdp-review-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</span>` : ""}
      ${helpful !== null ? `<span class="pdp-review-helpful">${icon("chat", 13)} ${formatInt(helpful)} 人觉得有帮助</span>` : ""}
    </footer>
  </article>`;
}

function dateLabel(value) {
  const raw = asText(value);
  if (!raw) return "";
  return `<time class="pdp-review-time" datetime="${escapeHtml(raw)}">${escapeHtml(formatDate(raw, { short: true }))}</time>`;
}

function reviewSummaryMarkup(summary, product, rating, reviewCount) {
  const distribution = asArray(summary && summary.distribution);
  const tags = asArray(summary && summary.tags);
  const total = asNumber(summary && summary.count, reviewCount);
  return `<div class="pdp-review-summary">
    <div class="pdp-review-scorebox">
      <span class="pdp-review-scorebox__number">${reviewRatingText(rating)}</span>
      <div>${rating !== null ? ratingStars(rating) : ""}<p>${total !== null ? formatInt(total) + " 条来自" : "来自"}已购用户的评价</p></div>
    </div>
    ${distribution.length ? `<div class="pdp-review-distribution" aria-label="评分分布">
      ${distribution.map((item) => {
        const percent = Math.max(0, Math.min(100, asNumber(item.percent, 0)));
        return `<div class="pdp-review-distribution__row"><span>${formatInt(item.stars)} 星</span><span class="pdp-review-distribution__track"><i style="--bar:${percent}%"></i></span><em>${formatInt(item.count)}</em></div>`;
      }).join("")}
    </div>` : ""}
    ${tags.length ? `<div class="pdp-review-tags-summary"><span>大家常提到</span><div>${tags.slice(0, 6).map((tag) => `<span class="chip">${escapeHtml(asText(tag.label) || tag)}${asNumber(tag.count) !== null ? `<em>${formatInt(tag.count)}</em>` : ""}</span>`).join("")}</div></div>` : ""}
    ${!distribution.length && !tags.length ? `<p class="pdp-review-summary__empty">这件商品暂时还没有评价摘要，第一条真实评价会出现在这里。</p>` : ""}
  </div>`;
}

function reviewsSectionMarkup(model) {
  const { product, summary, rating, reviewCount, initialReviews } = model;
  const distribution = asArray(summary && summary.distribution);
  const distributionByStar = new Map(distribution.map((item) => [asNumber(item.stars), asNumber(item.count)]));
  const ratingButtons = [0, 5, 4, 3, 2, 1].map((score) => {
    const count = score ? distributionByStar.get(score) : null;
    return `<button type="button" class="pdp-review-filter${score === 0 ? " is-active" : ""}" data-review-rating="${score}" aria-pressed="${score === 0 ? "true" : "false"}">${score === 0 ? "全部" : score + " 星"}${count !== null && count !== undefined ? `<span>${formatInt(count)}</span>` : ""}</button>`;
  }).join("");
  const initialList = initialReviews.map(reviewCardMarkup).filter(Boolean).join("");
  return `<section class="pdp-section pdp-reviews" id="pdp-reviews" data-testid="reviews-section" aria-labelledby="pdp-reviews-title">
    <div class="pdp-section-heading">
      <div><p class="eyebrow">REVIEWS · 买家评价</p><h2 id="pdp-reviews-title">用过的人，怎么说</h2></div>
      <span class="pdp-review-total" data-review-total>${reviewCount !== null ? formatInt(reviewCount) + " 条评价" : "评价来自服务端"}</span>
    </div>
    <div class="pdp-reviews__layout">
      ${reviewSummaryMarkup(summary, product, rating, reviewCount)}
      <div class="pdp-reviews__main">
        <div class="pdp-review-toolbar">
          <div class="pdp-review-filters" role="group" aria-label="按评分筛选评价">${ratingButtons}</div>
          <label class="pdp-review-sort">排序
            <select data-review-sort>
              ${Object.entries(REVIEW_SORT_LABELS).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
            </select>
          </label>
        </div>
        <p class="pdp-review-status" role="status" aria-live="polite" data-review-status>正在加载最新评价…</p>
        <div class="pdp-review-error error-inline" data-review-error role="alert" hidden><span data-review-error-text></span><button type="button" class="btn btn--ghost btn--small" data-review-retry>重试</button></div>
        <div class="pdp-review-list" data-review-list aria-busy="true">${initialList || `<p class="pdp-review-empty">暂无评价内容。</p>`}</div>
        <button type="button" class="btn btn--ghost pdp-review-more" data-review-more hidden>加载更多评价</button>
      </div>
    </div>
  </section>`;
}

function jumpMarkup(flags) {
  const items = [];
  if (flags.details) items.push({ href: "#pdp-details", label: "商品详情" });
  if (flags.specs) items.push({ href: "#pdp-specs", label: "规格参数" });
  if (flags.faq) items.push({ href: "#pdp-faq", label: "常见问题" });
  items.push({ href: "#pdp-reviews", label: "评价" });
  items.push({ href: "#pdp-related", label: "相关推荐" });
  return `<nav class="pdp-jump" aria-label="本页内容导航"><span>继续了解</span>${items.map((item) => `<a href="${item.href}">${item.label}</a>`).join("")}</nav>`;
}

function detailsMarkup(product, images) {
  const highlights = asArray(product.highlights).filter((item) => item && (asText(item.title) || asText(item.body)));
  const specs = asArray(product.specs).filter((group) => group && asArray(group.items).length);
  const faq = asArray(product.faq).filter((item) => item && asText(item.q));
  const detailSections = asArray(product.detail_sections).filter((section) => section && (asText(section.title) || asText(section.body)));
  const tags = asArray(product.tags).filter((tag) => asText(tag));
  const hasDetails = Boolean(asText(product.description) || asText(product.story) || detailSections.length || tags.length);
  const detailImage = images.length > 1 ? images[1] : null;
  const highlightMarkup = highlights.length ? `<section class="pdp-highlights" aria-labelledby="pdp-highlights-title">
    <div class="pdp-section-heading"><div><p class="eyebrow">WHY IT WORKS · 选物理由</p><h2 id="pdp-highlights-title">值得留意的细节</h2></div></div>
    <div class="pdp-highlights__grid">${highlights.map((item, index) => `<article class="pdp-highlight">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <h3>${escapeHtml(asText(item.title) || "细节")}</h3>
      <p>${escapeHtml(asText(item.body))}</p>
    </article>`).join("")}</div>
  </section>` : "";
  const details = hasDetails || detailImage ? `<section class="pdp-section pdp-details" id="pdp-details" aria-labelledby="pdp-details-title">
    <div class="pdp-section-heading"><div><p class="eyebrow">THE DETAILS · 商品详情</p><h2 id="pdp-details-title">关于这件东西</h2></div></div>
    <div class="pdp-details__layout">
      <div class="pdp-details__copy">
        ${asText(product.description) ? `<p class="pdp-details__lead">${escapeHtml(asText(product.description))}</p>` : ""}
        ${asText(product.story) ? `<blockquote class="pdp-story">${escapeHtml(asText(product.story))}</blockquote>` : ""}
        ${detailSections.map((section) => `<article class="pdp-detail-section">${asText(section.title) ? `<h3>${escapeHtml(asText(section.title))}</h3>` : ""}${asText(section.body) ? `<p>${escapeHtml(asText(section.body))}</p>` : ""}</article>`).join("")}
        ${tags.length ? `<div class="pdp-tag-row">${tags.map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join("")}</div>` : ""}
      </div>
      ${detailImage ? `<figure class="pdp-detail-figure"><img src="${escapeHtml(detailImage.src)}" alt="${escapeHtml(detailImage.alt)}" loading="lazy" decoding="async"><figcaption>${escapeHtml(detailImage.alt)}</figcaption></figure>` : ""}
    </div>
  </section>` : "";
  const specsMarkup = specs.length ? `<section class="pdp-section pdp-specs" id="pdp-specs" aria-labelledby="pdp-specs-title">
    <div class="pdp-section-heading"><div><p class="eyebrow">SPECS · 规格参数</p><h2 id="pdp-specs-title">把它看清楚</h2></div></div>
    <div class="pdp-specs__grid">${specs.map((group) => `<div class="pdp-spec-card">
      ${asText(group.title) ? `<h3>${escapeHtml(asText(group.title))}</h3>` : ""}
      <dl>${asArray(group.items).filter((item) => item && asText(item.label)).map((item) => `<div><dt>${escapeHtml(asText(item.label))}</dt><dd>${escapeHtml(asText(item.value))}</dd></div>`).join("")}</dl>
    </div>`).join("")}</div>
  </section>` : "";
  const faqMarkup = faq.length ? `<section class="pdp-section pdp-faq" id="pdp-faq" aria-labelledby="pdp-faq-title">
    <div class="pdp-section-heading"><div><p class="eyebrow">FAQ · 常见问题</p><h2 id="pdp-faq-title">买之前，先问清楚</h2></div></div>
    <div class="pdp-faq__list">${faq.map((item, index) => `<details${index === 0 ? " open" : ""}><summary>${escapeHtml(asText(item.q))}<span aria-hidden="true">${icon("chevronDown", 18)}</span></summary><div class="pdp-faq__answer"><p>${escapeHtml(asText(item.a))}</p></div></details>`).join("")}</div>
  </section>` : "";
  return {
    highlights: highlightMarkup,
    details,
    specs: specsMarkup,
    faq: faqMarkup,
    flags: { details: Boolean(details), specs: Boolean(specsMarkup), faq: Boolean(faqMarkup) },
  };
}

function relatedMarkup(model) {
  return `<section class="pdp-section pdp-related" id="pdp-related" aria-labelledby="pdp-related-title">
    <div class="pdp-section-heading"><div><p class="eyebrow">MORE TO EXPLORE · 相关推荐</p><h2 id="pdp-related-title">顺着这条线，再看几件</h2></div><a class="link-arrow" href="/search?sort=featured" data-link>逛全部 ${icon("arrowRight", 15)}</a></div>
    ${model.related.length ? `<div class="product-grid pdp-related__grid" data-related-grid></div>` : emptyState({ title: "相关推荐还在准备", message: "先从全部商品里找一个新方向。", actionLabel: "去逛逛", actionHref: "/search", iconName: "grid" })}
  </section>`;
}

function purchasePanelMarkup(model) {
  const { product, offers, skus, images, summary, rating, reviewCount } = model;
  const initialOffer = offers.find((offer) => offer.id === model.initialOfferId) || null;
  const initialSku = skus.find((sku) => sku.id === model.initialSkuId) || null;
  const unit = unitFor(initialOffer, initialSku);
  const market = asNumber(initialOffer && initialOffer.market_price_cents);
  const save = unit !== null && market !== null && market > unit ? market - unit : 0;
  const stock = initialOffer ? offerStock(initialOffer) : 0;
  const initialGroup = groupFor(initialOffer);
  const initialGroupUnit = groupUnitFor(initialOffer, initialSku);
  const delivery = deliveryParts(initialOffer);
  const services = asArray(initialOffer && initialOffer.services);
  const title = asText(product.title) || "商品详情";
  const subtitle = asText(product.subtitle);
  const brand = asText(product.brand);
  const description = asText(product.description);
  const badges = badgeMarkup(product.badges);
  return `<section class="pdp-buy" aria-labelledby="pdp-title">
    <div class="pdp-buy__head">
      <div class="pdp-buy__brandline">
        <span class="pdp-brand">${escapeHtml(brand || "OpenShop 开集")}</span>
        ${badges}
      </div>
      <div class="pdp-title-row">
        <h1 id="pdp-title">${escapeHtml(title)}</h1>
        <button class="pdp-favorite" type="button" data-favorite data-testid="favorite-toggle" aria-pressed="false" aria-label="收藏该商品">
          ${icon("heart", 19)}<span data-favorite-label>收藏</span>
        </button>
      </div>
      ${subtitle ? `<p class="pdp-subtitle">${escapeHtml(subtitle)}</p>` : ""}
      ${ratingRowMarkup(product, rating, reviewCount)}
      <p class="pdp-favorite-error" data-favorite-error role="alert" hidden></p>
    </div>

    <div class="pdp-price-panel" aria-label="商品报价">
      <div class="pdp-price-panel__main">
        <div>
          <span class="pdp-price-label">当前报价</span>
          <span class="pdp-main-price" data-main-price>${unit !== null ? priceHtml(unit, { className: "price--lg" }) : "暂无可售报价"}</span>
        </div>
        <div class="pdp-price-side">
          <span class="was-price" data-market-price ${market !== null && market > (unit || 0) ? "" : "hidden"}>${market !== null ? money(market) : ""}</span>
          <span class="pdp-save-note" data-save-note ${save > 0 ? "" : "hidden"}>${save > 0 ? "省 " + money(save) : ""}</span>
        </div>
      </div>
      <p class="pdp-line-total" data-line-total>${unit !== null ? "共 1 件 · 商品小计 " + money(unit) + "（未计结算优惠）" : "选择可售规格后查看价格"}</p>
      <p class="pdp-price-note">${icon("info", 13)} 页面金额按所选供给与规格试算；最终价格、优惠与运费以服务端结算为准。</p>
      <span class="sr-only" aria-live="polite" data-price-announcement></span>
    </div>

    <fieldset class="pdp-choice pdp-choice--offers">
      <legend><span>选择供给</span><em>${offers.length} 个报价</em></legend>
      ${offers.length ? `<div class="pdp-offer-list">${offers.map((offer) => offerCardMarkup(offer, initialSku, offer.id === model.initialOfferId)).join("")}</div>` : `<p class="pdp-choice-empty">这件商品暂时没有可售供给，先去逛逛别的。</p>`}
    </fieldset>

    <fieldset class="pdp-choice pdp-choice--sku">
      <legend><span>选择规格</span><em data-selected-sku>${initialSku ? escapeHtml(asText(initialSku.name)) : "暂无规格"}</em></legend>
      ${skus.length ? `<div class="pdp-sku-list" data-testid="sku-select" role="group" aria-label="选择商品规格">${skus.map((sku) => skuMarkup(sku, sku.id === model.initialSkuId)).join("")}</div>` : `<p class="pdp-choice-empty">规格信息暂不可用，暂时无法加入购物车。</p>`}
    </fieldset>

    <div class="pdp-delivery" data-delivery>
      ${icon("truck", 17)}
      <span>${escapeHtml(delivery.label || "配送时间以结算页为准")}${delivery.note ? ` · ${escapeHtml(delivery.note)}` : ""}${delivery.shipping ? ` · ${escapeHtml(delivery.shipping)}` : ""}</span>
    </div>

    <div class="pdp-quantity-row">
      <div class="pdp-quantity-label"><span>数量</span><em>每单按当前库存可购</em></div>
      <div class="quantity quantity--lg">
        <button type="button" data-qty-step="-1" aria-label="减少数量">${icon("minus", 16)}</button>
        <label class="sr-only" for="pdp-quantity-input">购买数量</label>
        <input id="pdp-quantity-input" data-qty-input data-testid="quantity-input" type="text" inputmode="numeric" pattern="[0-9]*" value="1" aria-label="购买数量">
        <button type="button" data-qty-step="1" aria-label="增加数量">${icon("plus", 16)}</button>
      </div>
      <span class="pdp-stock-note" id="pdp-stock-note" data-stock-note>${stock > 0 ? "现货 " + formatInt(stock) + " 件" : "暂时缺货"}</span>
    </div>

    <p class="pdp-action-error error-inline" data-action-error role="alert" hidden></p>
    <div class="pdp-actions">
      <button class="btn btn--ghost btn--large" type="button" data-add-to-cart data-testid="add-to-cart">${icon("cart", 18)} 加入购物车</button>
      <button class="btn btn--primary btn--large" type="button" data-buy-now data-testid="buy-now">立即购买</button>
    </div>
    <p class="pdp-actions-note">立即购买会先将所选商品加入购物车，再前往结算；结算时服务端会重新核价。</p>

    <div class="pdp-group-box" data-group-box ${initialGroup ? "" : "hidden"}>
      <div class="pdp-group-box__intro">
        <span class="pdp-group-box__icon">${icon("gift", 21)}</span>
        <div><span class="eyebrow">GROUP BUY · 一起拼</span><b>一起拼，<span data-group-size>${initialGroup ? (formatInt(initialGroup.size) || "多") : ""}</span> 人成团</b></div>
        <span class="pdp-group-price" data-group-price>${initialGroupUnit !== null ? priceHtml(initialGroupUnit, { className: "price--lg" }) : ""}</span>
      </div>
      <div class="pdp-group-box__foot">
        <p><span data-group-stock>${initialGroup ? "拼团库存 " + formatInt(groupStock(initialOffer)) + " 件" : ""}</span> · 拼团每单限 1 件，发起后由服务端生成真实邀请码。</p>
        <button class="btn btn--jade" type="button" data-group-buy data-testid="group-buy">发起拼团</button>
      </div>
    </div>

    <div class="pdp-service-strip" data-service-strip>
      ${services.length ? services.map((service) => `<span class="chip chip--ghost">${icon("check", 12)} ${escapeHtml(service)}</span>`).join("") : ""}
    </div>
    <div class="pdp-buy__footer">
      <p>${icon("shield", 14)} 所有价格、库存与评分来自服务端；商品与评价均为虚构演示内容。</p>
      <p>${icon("card", 14)} 支付为模拟流程，不产生真实扣款。</p>
    </div>
  </section>`;
}

function pageMarkup(model) {
  const { product, images, related } = model;
  const details = detailsMarkup(product, images);
  return `<div class="discovery-page pdp-page" data-testid="product-root">
    <div class="container">
      ${breadcrumbsMarkup(product)}
      <div class="pdp-main">
        ${galleryMarkup(images, product)}
        ${purchasePanelMarkup(model)}
      </div>
      ${jumpMarkup(details.flags)}
      ${details.highlights}
      ${details.details}
      ${details.specs}
      ${details.faq}
      ${reviewsSectionMarkup(model)}
      ${relatedMarkup({ related })}
      <p class="pdp-page-note">OpenShop 开集 · 价格与可售状态随服务端数据变化；在结算页再次确认。</p>
    </div>
  </div>`;
}

async function render(ctx) {
  const slug = asText(ctx.params && ctx.params.slug);
  ctx.setTitle("商品详情 · OpenShop 开集");
  ctx.root.innerHTML = loadingMarkup();

  const loadProduct = async () => {
    if (isAborted(ctx.signal)) return;
    ctx.root.innerHTML = loadingMarkup();
    try {
      const raw = await api.get("/api/products/" + encodeSlug(slug), { signal: ctx.signal });
      if (isAborted(ctx.signal)) return;
      if (!raw || typeof raw !== "object") throw new Error("商品服务返回了无法识别的数据");
      const model = normalizeProduct(raw);
      const title = asText(raw.title) || "商品详情";
      const brand = asText(raw.brand);
      ctx.setTitle((brand ? brand + " · " : "") + title + " · OpenShop 开集");
      ctx.root.innerHTML = pageMarkup(model);
      mountPage(ctx, slug, raw, model);
    } catch (err) {
      if (isAborted(ctx.signal) || (err && err.name === "AbortError")) return;
      const isApi = err instanceof ApiError;
      const notFound = isApi && err.status === 404;
      ctx.setTitle(notFound ? "没找到这件商品 · OpenShop 开集" : "商品页暂时不可用 · OpenShop 开集");
      ctx.root.innerHTML = errorMarkup(isApi ? err.message : err.message || "商品页暂时连不上服务，请稍后再试。", notFound);
      ctx.root.querySelector("[data-pdp-retry]")?.addEventListener("click", loadProduct);
    }
  };

  await loadProduct();
}

function mountPage(ctx, slug, rawProduct, model) {
  const page = ctx.root.querySelector(".pdp-page");
  if (!page) return;
  const selection = {
    offerId: model.initialOfferId,
    skuId: model.initialSkuId,
    quantity: 1,
    gallery: 0,
  };
  const images = model.images;
  let purchaseBusy = false;
  let favoriteBusy = false;
  let favoriteActive = false;
  let activeActionButton = null;
  const reviewState = { page: 1, pages: 1, sort: "recent", rating: 0, busy: false, count: 0 };
  let reviewRequestSeq = 0;

  const mainPrice = page.querySelector("[data-main-price]");
  const marketPrice = page.querySelector("[data-market-price]");
  const saveNote = page.querySelector("[data-save-note]");
  const lineTotal = page.querySelector("[data-line-total]");
  const priceAnnouncement = page.querySelector("[data-price-announcement]");
  const selectedSkuLabel = page.querySelector("[data-selected-sku]");
  const deliveryLine = page.querySelector("[data-delivery]");
  const stockNote = page.querySelector("[data-stock-note]");
  const quantityInput = page.querySelector("[data-qty-input]");
  const qtyButtons = [...page.querySelectorAll("[data-qty-step]")];
  const offerInputs = [...page.querySelectorAll('input[name="pdp-offer"]')];
  const skuButtons = [...page.querySelectorAll("[data-sku-button]")];
  const addButton = page.querySelector("[data-add-to-cart]");
  const buyButton = page.querySelector("[data-buy-now]");
  const groupButton = page.querySelector("[data-group-buy]");
  const groupBox = page.querySelector("[data-group-box]");
  const groupSizeEl = page.querySelector("[data-group-size]");
  const groupPriceEl = page.querySelector("[data-group-price]");
  const groupStockEl = page.querySelector("[data-group-stock]");
  const actionError = page.querySelector("[data-action-error]");
  const favoriteButton = page.querySelector("[data-favorite]");
  const favoriteLabel = page.querySelector("[data-favorite-label]");
  const favoriteError = page.querySelector("[data-favorite-error]");
  const serviceStrip = page.querySelector("[data-service-strip]");
  const galleryMain = page.querySelector("[data-gallery-main]");
  const galleryCounter = page.querySelector("[data-gallery-counter]");
  const galleryThumbs = [...page.querySelectorAll("[data-gallery-index]")];
  const reviewList = page.querySelector("[data-review-list]");
  const reviewStatus = page.querySelector("[data-review-status]");
  const reviewError = page.querySelector("[data-review-error]");
  const reviewErrorText = page.querySelector("[data-review-error-text]");
  const reviewMore = page.querySelector("[data-review-more]");
  const reviewSort = page.querySelector("[data-review-sort]");
  const reviewFilterButtons = [...page.querySelectorAll("[data-review-rating]")];
  const reviewTotalLabel = page.querySelector("[data-review-total]");
  let reviewsLoadedCount = 0;

  function findOffer(id = selection.offerId) {
    return model.offers.find((offer) => String(offer.id) === String(id)) || null;
  }
  function findSku(id = selection.skuId) {
    return model.skus.find((sku) => String(sku.id) === String(id)) || null;
  }
  function currentAvailability() {
    const offer = findOffer();
    const sku = findSku();
    const stock = offer ? offerStock(offer) : 0;
    const group = groupFor(offer);
    const gStock = offer ? groupStock(offer) : 0;
    return {
      offer,
      sku,
      stock,
      group,
      gStock,
      canBuy: Boolean(offer && sku && stock > 0),
      canGroup: Boolean(offer && sku && group && gStock > 0 && stock > 0),
      maxQuantity: Math.max(1, stock),
    };
  }
  function clearActionError() {
    if (!actionError) return;
    actionError.hidden = true;
    actionError.textContent = "";
  }
  function showActionError(message) {
    if (!actionError) return;
    actionError.textContent = message;
    actionError.hidden = false;
  }
  function setFavoriteError(message) {
    if (!favoriteError) return;
    favoriteError.textContent = message || "";
    favoriteError.hidden = !message;
  }
  function announcePrice(message) {
    if (priceAnnouncement) priceAnnouncement.textContent = message;
  }
  function syncGallery() {
    if (!images.length) return;
    const normalized = ((selection.gallery % images.length) + images.length) % images.length;
    selection.gallery = normalized;
    const image = images[normalized];
    if (galleryMain) {
      galleryMain.src = image.src;
      galleryMain.alt = image.alt;
    }
    if (galleryCounter) galleryCounter.textContent = (normalized + 1) + " / " + images.length;
    galleryThumbs.forEach((thumb, index) => {
      const active = index === normalized;
      thumb.classList.toggle("is-active", active);
      thumb.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }
  function syncPurchaseUI() {
    const { offer, sku, stock, group, gStock, canBuy, canGroup, maxQuantity } = currentAvailability();
    const unit = unitFor(offer, sku);
    const market = asNumber(offer && offer.market_price_cents);
    const delta = asNumber(sku && sku.price_delta_cents, 0) || 0;
    const groupUnit = groupUnitFor(offer, sku);
    selection.quantity = Math.max(1, Math.min(Math.floor(selection.quantity) || 1, maxQuantity));

    if (mainPrice) {
      if (unit !== null) mainPrice.innerHTML = priceHtml(unit, { className: "price--lg" });
      else mainPrice.textContent = "暂无可售报价";
    }
    if (marketPrice) {
      const showMarket = unit !== null && market !== null && market > unit;
      marketPrice.hidden = !showMarket;
      marketPrice.textContent = showMarket ? money(market) : "";
    }
    if (saveNote) {
      const save = unit !== null && market !== null ? Math.max(0, market - unit) : 0;
      saveNote.hidden = !(save > 0);
      saveNote.textContent = save > 0 ? "省 " + money(save) : "";
    }
    if (lineTotal) {
      lineTotal.textContent = unit !== null
        ? "共 " + selection.quantity + " 件 · 商品小计 " + money(unit * selection.quantity) + "（未计结算优惠）"
        : "选择可售规格后查看价格";
    }
    if (selectedSkuLabel) selectedSkuLabel.textContent = sku ? asText(sku.name) : "暂无规格";
    if (quantityInput) {
      quantityInput.value = String(selection.quantity);
      quantityInput.max = String(maxQuantity);
      quantityInput.disabled = purchaseBusy || !canBuy;
    }
    qtyButtons.forEach((button) => {
      const step = Number(button.dataset.qtyStep);
      button.disabled = purchaseBusy || !canBuy || (step < 0 ? selection.quantity <= 1 : selection.quantity >= maxQuantity);
    });
    offerInputs.forEach((input) => {
      const offerEntry = findOffer(input.value);
      input.disabled = purchaseBusy || (offerEntry ? offerStock(offerEntry) <= 0 : true);
      input.closest(".pdp-offer-option")?.classList.toggle("is-selected", String(selection.offerId) === input.value);
      if (String(selection.offerId) === input.value) input.checked = true;
    });
    skuButtons.forEach((button) => {
      const active = String(selection.skuId) === button.dataset.skuButton;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
      button.disabled = purchaseBusy;
    });
    page.querySelectorAll("[data-offer-price]").forEach((el) => {
      const offerId = el.dataset.offerPrice;
      const entry = findOffer(offerId);
      const price = entry ? unitFor(entry, sku) : null;
      if (price !== null) el.innerHTML = priceHtml(price, { className: "price--small" });
    });
    page.querySelectorAll("[data-offer-group-price]").forEach((el) => {
      const offerId = el.dataset.offerGroupPrice;
      const entry = findOffer(offerId);
      const g = groupFor(entry);
      if (!g) {
        el.innerHTML = "";
        return;
      }
      const price = groupUnitFor(entry, sku);
      const size = asNumber(g.size);
      el.innerHTML = (price !== null ? priceHtml(price, { className: "price--small pdp-offer-group-price" }) : "拼团价") + (size !== null ? `<em>${formatInt(size)} 人团</em>` : "");
    });
    if (deliveryLine) {
      const delivery = deliveryParts(offer);
      deliveryLine.innerHTML = icon("truck", 17) + `<span>${escapeHtml(delivery.label || "配送时间以结算页为准")}${delivery.note ? " · " + escapeHtml(delivery.note) : ""}${delivery.shipping ? " · " + escapeHtml(delivery.shipping) : ""}</span>`;
    }
    if (stockNote) {
      stockNote.textContent = stock > 0 ? "现货 " + formatInt(stock) + " 件" : "暂时缺货";
    }
    if (serviceStrip) {
      const services = asArray(offer && offer.services);
      serviceStrip.innerHTML = services.map((service) => `<span class="chip chip--ghost">${icon("check", 12)} ${escapeHtml(service)}</span>`).join("");
      serviceStrip.hidden = services.length === 0;
    }
    if (groupBox) groupBox.hidden = !group;
    if (group && groupSizeEl) groupSizeEl.textContent = formatInt(group.size) || "多";
    if (group && groupPriceEl) groupPriceEl.innerHTML = groupUnit !== null ? priceHtml(groupUnit, { className: "price--lg" }) : "";
    if (group && groupStockEl) groupStockEl.textContent = gStock > 0 ? "拼团库存 " + formatInt(gStock) + " 件" : "拼团暂时售罄";
    if (addButton) addButton.disabled = purchaseBusy || !canBuy;
    if (buyButton) buyButton.disabled = purchaseBusy || !canBuy;
    if (groupButton) groupButton.disabled = purchaseBusy || !canGroup;
    [addButton, buyButton, groupButton].forEach((button) => button && button.setAttribute("aria-busy", purchaseBusy ? "true" : "false"));
    if (favoriteButton) favoriteButton.disabled = favoriteBusy;
    if (priceAnnouncement) {
      const parts = [];
      if (offer) parts.push("已选 " + (asText(offer.name) || "报价"));
      if (sku) parts.push(asText(sku.name));
      if (unit !== null) parts.push("单价 " + money(unit));
      if (canGroup && groupUnit !== null) parts.push("拼团价 " + money(groupUnit));
      priceAnnouncement.textContent = parts.join("，");
    }
    if (quantityInput && quantityInput.value !== String(selection.quantity)) quantityInput.value = String(selection.quantity);
    if (selectedSkuLabel && sku) selectedSkuLabel.title = asText(sku.name);
  }
  function setPurchaseBusy(busy) {
    purchaseBusy = busy;
    syncPurchaseUI();
  }
  function setSelectionFromOffer(id) {
    const offer = findOffer(id);
    if (!offer || offerStock(offer) <= 0) return;
    selection.offerId = offer.id;
    selection.quantity = 1;
    clearActionError();
    offerInputs.forEach((input) => { input.checked = String(offer.id) === input.value; });
    syncPurchaseUI();
    announcePrice("已切换供给：" + (asText(offer.name) || "报价"));
  }
  function setSelectionFromSku(id) {
    const sku = findSku(id);
    if (!sku) return;
    selection.skuId = sku.id;
    clearActionError();
    skuButtons.forEach((button) => {
      const active = String(sku.id) === button.dataset.skuButton;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    syncPurchaseUI();
    announcePrice("已切换规格：" + asText(sku.name));
  }
  function setQuantity(value) {
    const { maxQuantity } = currentAvailability();
    const parsed = Number(value);
    if (!Number.isInteger(parsed) || parsed < 1) {
      if (quantityInput) quantityInput.value = String(selection.quantity);
      return;
    }
    const next = Math.min(parsed, maxQuantity);
    selection.quantity = next;
    syncPurchaseUI();
  }

  function redirectToCheckout() {
    if (isAborted(ctx.signal)) return;
    if (state.user) {
      ctx.navigate("/checkout");
      return;
    }
    openAuth({
      reason: "去结算前先登录。刚才选择的商品已经放进购物车；演示买家无需密码。",
      onSuccess: () => { if (!isAborted(ctx.signal)) ctx.navigate("/checkout"); },
    });
  }

  async function addCartItem(mode, goCheckout, trigger) {
    if (purchaseBusy) return;
    clearActionError();
    const { offer, sku, canBuy, canGroup } = currentAvailability();
    if (!offer || !sku) {
      showActionError("暂时无法加入购物车：这件商品缺少可售报价或规格。");
      return;
    }
    if (mode === "group" && !canGroup) {
      showActionError("当前报价的拼团暂时不可用，请选择带拼团的供给或稍后再试。");
      return;
    }
    if (mode !== "group" && !canBuy) {
      showActionError("当前报价暂时缺货，无法加入购物车。");
      return;
    }
    activeActionButton = trigger || null;
    setPurchaseBusy(true);
    const body = {
      offer_id: offer.id,
      sku_id: sku.id,
      quantity: mode === "group" ? 1 : selection.quantity,
      buy_mode: mode,
    };
    if (mode === "group") body.group_code = null;
    try {
      const cart = await api.post("/api/cart/items", body, { signal: ctx.signal });
      if (isAborted(ctx.signal)) return;
      const count = asNumber(cart && cart.summary && cart.summary.item_count);
      if (count !== null) updateCartCount(count);
      toast(mode === "group" ? "拼团商品已加入购物车" : "已加入购物车", { type: "success" });
      if (goCheckout) redirectToCheckout();
    } catch (err) {
      if (isAborted(ctx.signal) || (err && err.name === "AbortError")) return;
      const message = err instanceof ApiError ? err.message : "暂时连不上购物车服务，请稍后再试。";
      showActionError((mode === "group" ? "发起拼团失败：" : "加入购物车失败：") + message);
      toast(message, { type: "error" });
    } finally {
      setPurchaseBusy(false);
      if (!isAborted(ctx.signal) && activeActionButton && activeActionButton.isConnected) {
        activeActionButton.focus({ preventScroll: true });
      }
      activeActionButton = null;
    }
  }

  function setFavoriteState(active) {
    favoriteActive = Boolean(active);
    if (favoriteButton) {
      favoriteButton.setAttribute("aria-pressed", favoriteActive ? "true" : "false");
      favoriteButton.classList.toggle("is-active", favoriteActive);
      favoriteButton.setAttribute("aria-label", favoriteActive ? "取消收藏该商品" : "收藏该商品");
    }
    if (favoriteLabel) favoriteLabel.textContent = favoriteActive ? "已收藏" : "收藏";
  }

  async function loadFavoriteState() {
    if (!state.user || isAborted(ctx.signal)) return;
    try {
      const data = await api.get("/api/favorites", { signal: ctx.signal });
      if (isAborted(ctx.signal)) return;
      const items = asArray(data && data.items);
      const found = items.some((item) => item && asText(item.slug) === slug);
      setFavoriteState(found);
      const total = asNumber(data && data.total);
      if (total !== null) patchState({ favoriteCount: total });
    } catch (err) {
      if (isAborted(ctx.signal) || (err && err.name === "AbortError")) return;
      // 收藏状态读取失败不覆盖按钮的乐观状态；用户主动切换时仍会得到真实响应。
    }
  }

  async function toggleFavorite() {
    if (favoriteBusy) return;
    if (!state.user) {
      openAuth({
        reason: "登录以后就能把这件东西留进收藏；演示买家无需密码。",
        onSuccess: async () => {
          if (isAborted(ctx.signal)) return;
          await loadFavoriteState();
          if (!favoriteActive && !isAborted(ctx.signal)) await toggleFavorite();
        },
      });
      return;
    }
    favoriteBusy = true;
    setFavoriteError("");
    if (favoriteButton) {
      favoriteButton.disabled = true;
      favoriteButton.setAttribute("aria-busy", "true");
    }
    try {
      const result = await api.post("/api/favorites/" + encodeSlug(slug) + "/toggle", {}, { signal: ctx.signal });
      if (isAborted(ctx.signal)) return;
      setFavoriteState(Boolean(result && result.favorited));
      const total = asNumber(result && result.total);
      if (total !== null) patchState({ favoriteCount: total });
      toast(favoriteActive ? "已收藏这件商品" : "已取消收藏", { type: "success" });
    } catch (err) {
      if (isAborted(ctx.signal) || (err && err.name === "AbortError")) return;
      const message = err instanceof ApiError ? err.message : "收藏没有保存成功，请稍后再试。";
      setFavoriteError(message);
      toast(message, { type: "error" });
    } finally {
      favoriteBusy = false;
      if (favoriteButton && !isAborted(ctx.signal)) {
        favoriteButton.disabled = false;
        favoriteButton.removeAttribute("aria-busy");
        favoriteButton.focus({ preventScroll: true });
      }
    }
  }

  function setReviewStatus(message) {
    if (reviewStatus) reviewStatus.textContent = message || "";
  }
  function hideReviewError() {
    if (reviewError) reviewError.hidden = true;
    if (reviewErrorText) reviewErrorText.textContent = "";
  }
  function showReviewError(message) {
    if (!reviewError) return;
    if (reviewErrorText) reviewErrorText.textContent = message;
    reviewError.hidden = false;
  }
  function renderReviewItems(items) {
    if (!reviewList) return;
    reviewList.innerHTML = items.length ? items.map(reviewCardMarkup).filter(Boolean).join("") : `<p class="pdp-review-empty">当前条件下还没有评价。选择别的星级看看，或稍后再来。</p>`;
  }
  function updateReviewControls() {
    reviewFilterButtons.forEach((button) => {
      const active = Number(button.dataset.reviewRating) === reviewState.rating;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    if (reviewSort && REVIEW_SORT_LABELS[reviewState.sort]) reviewSort.value = reviewState.sort;
    if (reviewMore) {
      reviewMore.hidden = reviewState.page >= reviewState.pages;
      reviewMore.disabled = reviewState.busy;
    }
    if (reviewList) reviewList.setAttribute("aria-busy", reviewState.busy ? "true" : "false");
  }
  async function loadReviews({ reset = true, auto = false } = {}) {
    if (!reviewList) return;
    const seq = ++reviewRequestSeq;
    reviewState.busy = true;
    hideReviewError();
    if (reset) {
      reviewsLoadedCount = 0;
      reviewState.page = 1;
      if (!auto) renderReviewItems([]);
    }
    setReviewStatus(reset ? "正在加载评价…" : "正在加载更多评价…");
    updateReviewControls();
    const targetPage = reset ? 1 : reviewState.page + 1;
    const params = new URLSearchParams();
    params.set("page", String(targetPage));
    params.set("page_size", "5");
    params.set("sort", reviewState.sort);
    if (reviewState.rating > 0) params.set("rating", String(reviewState.rating));
    try {
      const data = await api.get("/api/products/" + encodeSlug(slug) + "/reviews?" + params.toString(), { signal: ctx.signal });
      if (isAborted(ctx.signal) || seq !== reviewRequestSeq) return;
      if (!data || typeof data !== "object" || !Array.isArray(data.items)) throw new Error("评价服务返回了无法识别的数据");
      const items = data.items.filter((item) => item && typeof item === "object");
      const total = asNumber(data.total, asNumber(model.summary && model.summary.count, model.reviewCount));
      const page = asNumber(data.page, targetPage) || targetPage;
      const pageSize = asNumber(data.page_size, 5) || 5;
      const pages = asNumber(data.pages, Math.max(1, Math.ceil((total || 0) / pageSize))) || 1;
      reviewState.page = page;
      reviewState.pages = Math.max(1, pages);
      reviewState.count = total === null ? items.length : total;
      if (reset) {
        reviewsLoadedCount = items.length;
        renderReviewItems(items);
      } else {
        if (items.length) {
          reviewList.insertAdjacentHTML("beforeend", items.map(reviewCardMarkup).filter(Boolean).join(""));
          reviewsLoadedCount += items.length;
        }
      }
      if (reviewTotalLabel && total !== null) reviewTotalLabel.textContent = formatInt(total) + " 条评价";
      setReviewStatus(items.length
        ? "已加载 " + formatInt(reviewsLoadedCount) + " / " + formatInt(reviewState.count) + " 条评价"
        : "当前条件下还没有评价。");
      reviewState.busy = false;
      updateReviewControls();
    } catch (err) {
      if (isAborted(ctx.signal) || seq !== reviewRequestSeq || (err && err.name === "AbortError")) return;
      reviewState.busy = false;
      const message = err instanceof ApiError ? err.message : "评价列表暂时没有加载完成。";
      setReviewStatus("评价列表加载失败，商品页预载评价仍可阅读。");
      showReviewError(message);
      updateReviewControls();
    }
  }

  function setupQuantityEvents() {
    if (quantityInput) {
      quantityInput.addEventListener("input", () => {
        if (purchaseBusy) return;
        const raw = quantityInput.value;
        if (raw === "") return;
        const parsed = Number(raw);
        if (!Number.isInteger(parsed)) return;
        const { maxQuantity } = currentAvailability();
        const next = Math.min(Math.max(1, parsed), maxQuantity);
        if (next !== selection.quantity) {
          selection.quantity = next;
          syncPurchaseUI();
        }
      });
      quantityInput.addEventListener("change", () => setQuantity(quantityInput.value));
      quantityInput.addEventListener("blur", () => setQuantity(quantityInput.value));
      quantityInput.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        setQuantity(quantityInput.value);
        quantityInput.blur();
      });
    }
    qtyButtons.forEach((button) => {
      button.addEventListener("click", () => {
        if (purchaseBusy) return;
        const { maxQuantity } = currentAvailability();
        const step = Number(button.dataset.qtyStep);
        const next = Math.min(maxQuantity, Math.max(1, selection.quantity + (step > 0 ? 1 : -1)));
        if (next === selection.quantity) return;
        selection.quantity = next;
        syncPurchaseUI();
      });
    });
  }

  function setupGallery() {
    page.querySelectorAll("[data-gallery-step]").forEach((button) => {
      button.addEventListener("click", () => {
        selection.gallery += Number(button.dataset.galleryStep) || 1;
        syncGallery();
      });
    });
    galleryThumbs.forEach((button, index) => {
      button.addEventListener("click", () => {
        selection.gallery = index;
        syncGallery();
      });
    });
    if (galleryThumbs.length > 1) {
      page.querySelector(".pdp-gallery")?.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        selection.gallery += event.key === "ArrowLeft" ? -1 : 1;
        syncGallery();
      });
    }
    syncGallery();
  }

  page.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.matches("[data-favorite]")) { void toggleFavorite(); return; }
    if (button.matches("[data-qty-step]")) return; // handled in setupQuantityEvents
    if (button.matches("[data-add-to-cart]")) { void addCartItem("standard", false, button); return; }
    if (button.matches("[data-buy-now]")) { void addCartItem("standard", true, button); return; }
    if (button.matches("[data-group-buy]")) { void addCartItem("group", true, button); return; }
    if (button.matches("[data-sku-button]")) { setSelectionFromSku(button.dataset.skuButton); return; }
    if (button.matches("[data-review-rating]")) {
      const next = Number(button.dataset.reviewRating) || 0;
      reviewState.rating = reviewState.rating === next ? 0 : next;
      updateReviewControls();
      void loadReviews({ reset: true });
      return;
    }
    if (button.matches("[data-review-more]")) {
      if (reviewState.page < reviewState.pages && !reviewState.busy) void loadReviews({ reset: false });
      return;
    }
    if (button.matches("[data-review-retry]")) {
      void loadReviews({ reset: true });
    }
  });

  page.addEventListener("change", (event) => {
    const radio = event.target.closest('input[name="pdp-offer"]');
    if (radio) {
      const id = asNumber(radio.value);
      if (id !== null) setSelectionFromOffer(id);
      return;
    }
    if (event.target === quantityInput) {
      setQuantity(quantityInput.value);
      return;
    }
    if (event.target.matches("[data-review-sort]")) {
      const next = asText(event.target.value);
      if (REVIEW_SORT_LABELS[next] && next !== reviewState.sort) {
        reviewState.sort = next;
        void loadReviews({ reset: true });
      }
    }
  });

  // Related cards use the shared card component. Populate before first interaction.
  const relatedGrid = page.querySelector("[data-related-grid]");
  if (relatedGrid) {
    for (const card of model.related) {
      const node = productCard(card);
      if (card.slug && node && node.dataset) node.dataset.productSlug = asText(card.slug);
      relatedGrid.appendChild(node);
    }
  }

  setupGallery();
  setupQuantityEvents();
  // qtyButtons also have page-level click guard; no duplicate state change.
  syncPurchaseUI();

  // 阅读足迹与收藏状态：失败静默，不阻塞商品主内容。
  api.post("/api/events/view", { product_slug: slug }, { signal: ctx.signal }).catch(() => {});
  if (state.user) void loadFavoriteState();
  void loadReviews({ reset: true, auto: true });
}

export { render };
