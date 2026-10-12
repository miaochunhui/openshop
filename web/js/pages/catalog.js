// 搜索与发现：URL 是筛选真源；每次筛选/排序/翻页都向目录 API 重新请求。
import { api, ApiError } from "../core/api.js";
import { state as storeState } from "../core/store.js";
import {
  emptyState,
  escapeHtml,
  icon,
  money,
  productCard,
  skeletonCards,
  KIND_LABELS,
} from "../components/index.js";

const PAGE_SIZE = 12;
const VALID_SORTS = ["featured", "newest", "price_asc", "price_desc", "rating", "sales"];
const SORT_LABELS = {
  featured: "精选推荐",
  newest: "最新上架",
  price_asc: "价格从低到高",
  price_desc: "价格从高到低",
  rating: "评分优先",
  sales: "销量优先",
};
const KIND_OPTIONS = ["self", "brand", "factory"];
const KIND_LABELS_LOCAL = { self: "官方自营", brand: "品牌旗舰", factory: "源头工厂" };
const RATING_OPTIONS = [
  { value: 0, label: "不限" },
  { value: 5, label: "5 星" },
  { value: 4, label: "4 星+" },
  { value: 3, label: "3 星+" },
  { value: 2, label: "2 星+" },
  { value: 1, label: "1 星+" },
];
const asArray = (value) => (Array.isArray(value) ? value : []);
const asText = (value) => (typeof value === "string" ? value.trim() : "");
const asNumber = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};
const formatInt = (value) => {
  const n = asNumber(value);
  return n === null ? "" : n.toLocaleString("zh-CN");
};
const isAborted = (signal) => Boolean(signal && signal.aborted);

function sanitizeSlug(value) {
  const raw = asText(value);
  return /^[a-z0-9-]{2,32}$/.test(raw) ? raw : "";
}

function parseCents(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0 || n > 1000000000 || !Number.isInteger(n)) return null;
  return n;
}

function parsePositiveInt(value, fallback = 1) {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 && n <= 100000 ? n : fallback;
}

function readView(query) {
  const rawSort = asText(query.get("sort"));
  const rawRating = Number(query.get("min_rating"));
  const minPrice = parseCents(query.get("min_price_cents"));
  const maxPrice = parseCents(query.get("max_price_cents"));
  return {
    q: (asText(query.get("q")) || "").slice(0, 60),
    category: sanitizeSlug(query.get("category")),
    kind: KIND_OPTIONS.includes(asText(query.get("kind"))) ? asText(query.get("kind")) : "",
    sort: VALID_SORTS.includes(rawSort) ? rawSort : "featured",
    minPrice,
    maxPrice,
    minRating: Number.isInteger(rawRating) && rawRating >= 1 && rawRating <= 5 ? rawRating : 0,
    page: parsePositiveInt(query.get("page"), 1),
  };
}

function syncUrl(view) {
  const params = new URLSearchParams();
  if (view.q) params.set("q", view.q);
  if (view.category) params.set("category", view.category);
  if (view.kind) params.set("kind", view.kind);
  if (view.sort !== "featured") params.set("sort", view.sort);
  if (view.minPrice !== null) params.set("min_price_cents", String(view.minPrice));
  if (view.maxPrice !== null) params.set("max_price_cents", String(view.maxPrice));
  if (view.minRating > 0) params.set("min_rating", String(view.minRating));
  if (view.page > 1) params.set("page", String(view.page));
  const next = "/search" + (params.toString() ? "?" + params.toString() : "");
  history.replaceState({}, "", next);
}

function buildApiUrl(view) {
  const params = new URLSearchParams();
  if (view.q) params.set("q", view.q);
  if (view.category) params.set("category", view.category);
  if (view.kind) params.set("kind", view.kind);
  if (view.sort) params.set("sort", view.sort);
  if (view.minPrice !== null) params.set("min_price_cents", String(view.minPrice));
  if (view.maxPrice !== null) params.set("max_price_cents", String(view.maxPrice));
  if (view.minRating > 0) params.set("min_rating", String(view.minRating));
  params.set("page", String(view.page));
  params.set("page_size", String(PAGE_SIZE));
  return "/api/products?" + params.toString();
}

function sortOptionsMarkup(selected) {
  return VALID_SORTS.map((key) => `<option value="${key}"${key === selected ? " selected" : ""}>${escapeHtml(SORT_LABELS[key])}</option>`).join("");
}

function centsToInput(cents) {
  if (cents === null) return "";
  const value = cents / 100;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function filterSkeletonMarkup() {
  return `<div class="catalog-filter-skeleton" aria-hidden="true">
    ${Array.from({ length: 6 }, () => `<span class="skeleton" style="height:38px;border-radius:10px"></span>`).join("")}
    <span class="skeleton" style="height:14px;width:52%"></span>
    <span class="skeleton" style="height:46px;width:100%"></span>
    <span class="skeleton" style="height:46px;width:100%"></span>
  </div>`;
}

function shellMarkup(view) {
  return `<div class="discovery-page catalog-view" data-testid="search-root">
    <section class="catalog-masthead">
      <div class="container catalog-masthead__inner">
        <div class="catalog-intro">
          <p class="eyebrow">SEARCH THE MARKET · 搜索开集</p>
          <h1 class="catalog-title">找一件，<br><span>值得带回家的东西。</span></h1>
          <p class="catalog-lede">按关键词、品类、供给与价格筛选。每一次选择都会向在售目录重新请求，不拿静态清单糊弄你。</p>
        </div>
        <div class="catalog-search-wrap">
          <form class="catalog-search" role="search" data-search-form novalidate>
            <span class="catalog-search__icon" aria-hidden="true">${icon("search", 21)}</span>
            <label class="sr-only" for="catalog-search-input">搜索商品、品牌或分类</label>
            <input id="catalog-search-input" type="search" name="q" maxlength="60" autocomplete="off" placeholder="搜耳机、沙发、背包……" value="${escapeHtml(view.q)}" data-focus-key="search-input">
            <button class="btn btn--primary catalog-search__submit" type="submit">搜索</button>
          </form>
          <div class="catalog-search__hints"><span>按标题、品牌与标签匹配</span><span>结果来自实时目录服务</span></div>
        </div>
      </div>
      <div class="container catalog-masthead__foot">
        <span>${icon("shield", 14)} 全站包邮</span>
        <span>${icon("return", 14)} 7 天无理由</span>
        <span>${icon("card", 14)} 支付为模拟流程</span>
        <span class="catalog-masthead__note">© 所有商品、价格与评价均为虚构演示数据</span>
      </div>
    </section>

    <div class="container catalog-layout">
      <aside class="catalog-filters" id="search-filter-panel" aria-label="筛选商品">
        <div class="catalog-filters__head">
          <div><p class="eyebrow">NARROW DOWN</p><h2>筛选</h2></div>
          <button class="icon-btn catalog-filters__close" type="button" data-filter-close aria-label="收起筛选">${icon("x", 20)}</button>
        </div>
        <div class="catalog-filters__body" data-filter-body>${filterSkeletonMarkup()}</div>
        <p class="catalog-filters__footnote">筛选项与数量由 API facets 计算；当前结果始终来自服务端。</p>
      </aside>

      <section class="catalog-results" aria-labelledby="catalog-result-heading">
        <div class="catalog-toolbar">
          <div class="catalog-toolbar__summary">
            <p class="eyebrow">RESULTS · 发现</p>
            <h2 tabindex="-1" id="catalog-result-heading" data-result-heading>正在取回商品</h2>
            <p class="catalog-result-summary" data-result-summary role="status" aria-live="polite">正在请求目录服务…</p>
          </div>
          <div class="catalog-toolbar__actions">
            <button class="catalog-filter-toggle" type="button" data-filter-toggle aria-expanded="false" aria-controls="search-filter-panel">${icon("filter", 16)} 筛选</button>
            <div class="catalog-sort">
              <label for="search-sort">排序</label>
              <select id="search-sort" data-testid="search-sort" data-search-sort>${sortOptionsMarkup(view.sort)}</select>
            </div>
          </div>
        </div>
        <div class="catalog-active-filters" data-active-filters></div>
        <div class="catalog-result-region" id="search-result-region" data-testid="search-results" data-result-region role="region" aria-label="搜索结果" aria-busy="true">
          <div class="catalog-loading" role="status">
            ${skeletonCards(6)}
            <p class="sr-only">正在加载商品结果</p>
          </div>
        </div>
        <p class="catalog-demo-note">${icon("info", 14)} 价格、库存与可售状态都由服务端返回；打开商品后可比较具体供给与服务。</p>
      </section>
    </div>
  </div>`;
}

function categoryLabel(view, facets) {
  if (!view.category) return "";
  const facetMatch = asArray(facets && facets.categories).find((cat) => cat && cat.slug === view.category);
  if (facetMatch && asText(facetMatch.name)) return asText(facetMatch.name);
  const known = asArray(storeState.categories).find((cat) => cat && cat.slug === view.category);
  return known && asText(known.name) ? asText(known.name) : view.category;
}

function categoriesForFilter(view, facets) {
  const facetCats = asArray(facets && facets.categories)
    .filter((cat) => cat && asText(cat.slug))
    .map((cat) => ({ slug: asText(cat.slug), name: asText(cat.name) || asText(cat.slug), count: asNumber(cat.count) }));
  const seen = new Set(facetCats.map((cat) => cat.slug));
  if (view.category && !seen.has(view.category)) {
    const known = asArray(storeState.categories).find((cat) => cat && cat.slug === view.category);
    facetCats.unshift({ slug: view.category, name: known && asText(known.name) ? asText(known.name) : view.category, count: null });
    seen.add(view.category);
  }
  if (!facetCats.length) {
    return asArray(storeState.categories)
      .filter((cat) => cat && asText(cat.slug))
      .map((cat) => ({ slug: asText(cat.slug), name: asText(cat.name) || asText(cat.slug), count: null }));
  }
  return facetCats;
}

function kindsForFilter(facets) {
  const facetKinds = asArray(facets && facets.kinds)
    .filter((item) => item && KIND_OPTIONS.includes(asText(item.kind)))
    .map((item) => ({ kind: asText(item.kind), count: asNumber(item.count) }));
  return facetKinds.length ? facetKinds : KIND_OPTIONS.map((kind) => ({ kind, count: null }));
}

function filtersMarkup(view, facets) {
  const categories = categoriesForFilter(view, facets);
  const kinds = kindsForFilter(facets);
  const categoryButtons = [
    `<button type="button" class="catalog-filter-chip${view.category ? "" : " is-active"}" data-category-filter="" data-focus-key="category:all" data-testid="search-category-filter-all" aria-pressed="${view.category ? "false" : "true"}">全部<span class="catalog-filter-chip__count"></span></button>`,
    ...categories.map((cat) => `<button type="button" class="catalog-filter-chip${view.category === cat.slug ? " is-active" : ""}" data-category-filter="${escapeHtml(cat.slug)}" data-focus-key="category:${escapeHtml(cat.slug)}" data-testid="search-category-filter-${escapeHtml(cat.slug)}" aria-pressed="${view.category === cat.slug ? "true" : "false"}">${escapeHtml(cat.name)}${cat.count !== null ? `<span class="catalog-filter-chip__count">${formatInt(cat.count)}</span>` : ""}</button>`),
  ].join("");
  const kindButtons = [
    `<button type="button" class="catalog-filter-chip${view.kind ? "" : " is-active"}" data-kind-filter="" data-focus-key="kind:all" aria-pressed="${view.kind ? "false" : "true"}">全部</button>`,
    ...kinds.map((item) => `<button type="button" class="catalog-filter-chip${view.kind === item.kind ? " is-active" : ""}" data-kind-filter="${escapeHtml(item.kind)}" data-focus-key="kind:${escapeHtml(item.kind)}" aria-pressed="${view.kind === item.kind ? "true" : "false"}">${escapeHtml(KIND_LABELS_LOCAL[item.kind] || KIND_LABELS[item.kind] || item.kind)}${item.count !== null ? `<span class="catalog-filter-chip__count">${formatInt(item.count)}</span>` : ""}</button>`),
  ].join("");
  const facetMin = parseCents(facets && facets.price_min_cents);
  const facetMax = parseCents(facets && facets.price_max_cents);
  const facetBandText = facetMin !== null && facetMax !== null && facetMax > 0
    ? ` · 本期待售价格带 ${escapeHtml(money(facetMin))} – ${escapeHtml(money(facetMax))}`
    : "";
  const ratingButtons = RATING_OPTIONS.map((option) => `<button type="button" class="catalog-filter-chip${view.minRating === option.value ? " is-active" : ""}" data-rating-filter="${option.value}" data-focus-key="rating:${option.value}" aria-pressed="${view.minRating === option.value ? "true" : "false"}">${option.label}</button>`).join("");
  const hasActiveFilters = Boolean(view.category || view.kind || view.minPrice !== null || view.maxPrice !== null || view.minRating > 0);
  return `<div class="catalog-filters__form" data-filter-form>
    <fieldset class="catalog-filter-group" data-testid="search-category-filter">
      <legend>品类</legend>
      <div class="catalog-filter-chips">${categoryButtons}</div>
    </fieldset>
    <fieldset class="catalog-filter-group">
      <legend>供给类型</legend>
      <div class="catalog-filter-chips">${kindButtons}</div>
    </fieldset>
    <fieldset class="catalog-filter-group">
      <legend>价格区间</legend>
      <form class="catalog-price-form" data-price-form novalidate>
        <div class="catalog-price-form__inputs">
          <label class="catalog-price-field"><span>最低价</span><input id="catalog-price-min" type="number" min="0" step="1" inputmode="decimal" name="min_price" placeholder="${facetMin !== null && facetMin > 0 ? escapeHtml(String(facetMin / 100)) : "不限"}" value="${escapeHtml(centsToInput(view.minPrice))}" data-price-min data-focus-key="price"></label>
          <span class="catalog-price-form__dash" aria-hidden="true">—</span>
          <label class="catalog-price-field"><span>最高价</span><input id="catalog-price-max" type="number" min="0" step="1" inputmode="decimal" name="max_price" placeholder="${facetMax !== null && facetMax > 0 ? escapeHtml(String(facetMax / 100)) : "不限"}" value="${escapeHtml(centsToInput(view.maxPrice))}" data-price-max></label>
        </div>
        <p class="catalog-price-hint" data-facets-price>单位：元${facetBandText}</p>
        <p class="catalog-price-error field-error" data-price-error role="alert" hidden></p>
        <button type="submit" class="btn btn--ghost btn--small catalog-price-apply" data-price-apply>应用价格</button>
      </form>
    </fieldset>
    <fieldset class="catalog-filter-group">
      <legend>评分</legend>
      <div class="catalog-filter-chips">${ratingButtons}</div>
    </fieldset>
    <div class="catalog-filter-group__foot">
      <button type="button" class="catalog-filter-clear" data-clear-filters ${hasActiveFilters ? "" : "disabled"}>清除筛选</button>
      <button type="button" class="btn btn--primary catalog-filter-done" data-filter-close>看结果</button>
    </div>
  </div>`;
}

function chipsMarkup(view, facets) {
  const chips = [];
  if (view.q) chips.push({ key: "q", label: "关键词：" + view.q });
  if (view.category) chips.push({ key: "category", label: "品类：" + categoryLabel(view, facets) });
  if (view.kind) chips.push({ key: "kind", label: "供给：" + (KIND_LABELS_LOCAL[view.kind] || view.kind) });
  if (view.minPrice !== null || view.maxPrice !== null) {
    const low = view.minPrice !== null ? money(view.minPrice) : "不限";
    const high = view.maxPrice !== null ? money(view.maxPrice) : "不限";
    chips.push({ key: "price", label: "价格：" + low + " – " + high });
  }
  if (view.minRating > 0) chips.push({ key: "rating", label: "评分：" + view.minRating + " 星+" });
  if (!chips.length) return "";
  return `<div class="catalog-active-filters__head"><span>已选条件</span><button type="button" class="catalog-active-filters__clear" data-clear-filters>全部清除</button></div>
    <div class="catalog-active-filters__chips">${chips.map((chip) => `<button type="button" class="catalog-active-chip" data-remove-filter="${chip.key}" aria-label="移除筛选：${escapeHtml(chip.label)}">${escapeHtml(chip.label)} ${icon("x", 12)}</button>`).join("")}</div>`;
}

function resultTitle(view, facets) {
  if (view.q) return "“" + view.q + "”的搜索结果";
  if (view.category) return categoryLabel(view, facets) || "品类结果";
  return "全部在售商品";
}

function pageTitle(view, facets) {
  if (view.q) return `${view.q} · 搜索与发现 · OpenShop 开集`;
  if (view.category) return `${categoryLabel(view, facets)} · 搜索与发现 · OpenShop 开集`;
  return "搜索与发现 · OpenShop 开集";
}

function paginationMarkup(data, view) {
  const pages = Math.max(1, Number(data.pages) || 1);
  if (pages <= 1) return "";
  const current = Math.min(Math.max(1, Number(data.page) || view.page), pages);
  const candidates = [...new Set([1, pages, current - 1, current, current + 1].filter((p) => p >= 1 && p <= pages))].sort((a, b) => a - b);
  let last = 0;
  const pageBits = [];
  for (const pageNumber of candidates) {
    if (last && pageNumber - last > 1) pageBits.push('<span class="catalog-page-ellipsis" aria-hidden="true">…</span>');
    pageBits.push(`<button type="button" class="catalog-page${pageNumber === current ? " is-current" : ""}" data-page="${pageNumber}" aria-label="第 ${pageNumber} 页"${pageNumber === current ? ' aria-current="page"' : ""}>${pageNumber}</button>`);
    last = pageNumber;
  }
  return `<nav class="catalog-pagination" aria-label="搜索结果分页">
    <button type="button" class="catalog-page catalog-page--arrow" data-page="prev" data-testid="search-prev" aria-label="上一页"${current <= 1 ? " disabled" : ""}>${icon("arrowLeft", 15)}</button>
    ${pageBits.join("")}
    <button type="button" class="catalog-page catalog-page--arrow" data-page="next" data-testid="search-next" aria-label="下一页"${current >= pages ? " disabled" : ""}>${icon("arrowRight", 15)}</button>
    <span class="catalog-pagination__status">第 ${current} / ${pages} 页</span>
  </nav>`;
}

function categorySuggestions(view, facets) {
  const cats = categoriesForFilter(view, facets).filter((cat) => cat.slug !== view.category).slice(0, 4);
  if (!cats.length) return "";
  return `<div class="catalog-empty__cats"><span>换个品类：</span>${cats.map((cat) => `<button type="button" class="catalog-empty__cat" data-category-filter="${escapeHtml(cat.slug)}" data-focus-key="category:${escapeHtml(cat.slug)}" aria-label="只看${escapeHtml(cat.name)}">${escapeHtml(cat.name)}</button>`).join("")}</div>`;
}

function emptyMarkup(data, view) {
  const total = Number(data.total) || 0;
  const pages = Math.max(1, Number(data.pages) || 1);
  const outOfRange = view.page > pages && total > 0;
  const title = outOfRange ? "这一页已经翻过去了" : (view.q ? "没有找到“" + view.q + "”" : "这组筛选暂时没有结果");
  const message = outOfRange
    ? "当前页没有可展示的商品，回到第一页继续看吧。"
    : "可以换一个关键词，或减少一两个筛选条件。目录会按服务端当前在售状态重新查询。";
  return `<div class="catalog-empty" role="status">
    ${emptyState({ title, message, iconName: "search" })}
    <div class="catalog-empty__actions">
      ${outOfRange ? `<button type="button" class="btn btn--primary" data-page="1">回到第一页</button>` : `<button type="button" class="btn btn--primary" data-reset-search>清空全部条件</button>`}
      <a class="btn btn--ghost" href="/search" data-link>看全部商品</a>
    </div>
    ${!outOfRange ? categorySuggestions(view, data.facets || {}) : ""}
  </div>`;
}

function errorMarkup(message, isApi) {
  return `<div class="catalog-error-state" role="alert">
    <span class="catalog-error-state__icon">${icon("alert", 24)}</span>
    <div><p class="eyebrow">${isApi ? "SERVICE ERROR" : "CONNECTION"}</p><h3>这次筛选没有取回来</h3><p>${escapeHtml(message || "目录服务暂时不可用，请稍后再试。")}</p></div>
    <button type="button" class="btn btn--primary btn--small" data-retry-results>重新加载</button>
  </div>`;
}

function resultSummaryText(data, view) {
  const total = Number(data.total) || 0;
  const pages = Math.max(1, Number(data.pages) || 1);
  const current = Math.min(Math.max(1, Number(data.page) || view.page), pages);
  if (!total) return "0 件符合当前条件 · " + SORT_LABELS[view.sort];
  return `共 ${formatInt(total)} 件在售 · 第 ${current} / ${pages} 页 · ${SORT_LABELS[view.sort]}`;
}

function focusByName(page, key) {
  if (!key) return;
  let target = null;
  if (key === "sort") target = page.querySelector("[data-search-sort]");
  else if (key === "search-input") target = page.querySelector("#catalog-search-input");
  else if (key === "results") target = page.querySelector("[data-result-heading]");
  else {
    const selector = '[data-focus-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key.replaceAll('"', '\\"')) + '"]';
    target = page.querySelector(selector);
  }
  if (target && typeof target.focus === "function") target.focus({ preventScroll: true });
}

async function render(ctx) {
  const view = readView(ctx.query);
  ctx.setTitle(pageTitle(view, {}));
  ctx.root.innerHTML = shellMarkup(view);
  const page = ctx.root.querySelector(".catalog-view");
  const filterBody = page.querySelector("[data-filter-body]");
  const resultsRegion = page.querySelector("[data-result-region]");
  const resultHeading = page.querySelector("[data-result-heading]");
  const resultSummary = page.querySelector("[data-result-summary]");
  const activeFilters = page.querySelector("[data-active-filters]");
  const filterPanel = page.querySelector("#search-filter-panel");
  const sortSelect = page.querySelector("[data-search-sort]");
  const filterToggle = page.querySelector("[data-filter-toggle]");
  const searchInput = page.querySelector("#catalog-search-input");
  let requestSeq = 0;
  let didLoadFilters = false;
  let lastData = null;
  let lastPages = 1;

  function setFilterOpen(open) {
    if (!filterPanel || !filterToggle) return;
    filterPanel.classList.toggle("is-open", open);
    filterToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      filterPanel.querySelector("[data-filter-close]")?.focus({ preventScroll: true });
      if (typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) filterPanel.scrollIntoView({ block: "start" });
      else filterPanel.scrollIntoView({ block: "start", behavior: "smooth" });
    } else {
      filterToggle.focus({ preventScroll: false });
    }
  }

  function updateSortSelect() {
    if (sortSelect) sortSelect.value = view.sort;
  }

  function updateFilters(facets) {
    if (!filterBody) return;
    filterBody.innerHTML = filtersMarkup(view, facets || {});
    didLoadFilters = true;
  }

  function updateActiveFilters(facets) {
    if (!activeFilters) return;
    activeFilters.innerHTML = chipsMarkup(view, facets || {});
  }

  function applyTitle(facets) {
    ctx.setTitle(pageTitle(view, facets || {}));
  }

  function showLoading() {
    if (resultSummary) resultSummary.textContent = "正在请求目录服务…";
    if (resultsRegion) {
      resultsRegion.setAttribute("aria-busy", "true");
      resultsRegion.innerHTML = `<div class="catalog-loading" role="status">${skeletonCards(6)}<p class="sr-only">正在加载商品结果</p></div>`;
    }
  }

  function showError(message) {
    if (resultSummary) resultSummary.textContent = "目录服务暂时不可用";
    if (resultsRegion) {
      resultsRegion.setAttribute("aria-busy", "false");
      resultsRegion.innerHTML = errorMarkup(message, true);
    }
    if (!didLoadFilters && filterBody) {
      filterBody.innerHTML = `<div class="catalog-filter-offline">${icon("alert", 18)} 筛选条件暂时不可用。重新加载结果后再试。</div>`;
    } else if (lastData) {
      // 当前 URL/state 的筛选意图必须反映在控件上，不能留下上一次成功结果的外观。
      updateFilters((lastData && lastData.facets) || {});
      updateActiveFilters((lastData && lastData.facets) || {});
    }
    if (resultHeading) resultHeading.textContent = "暂时没有取回结果";
    page.querySelector("[data-retry-results]")?.focus({ preventScroll: true });
  }

  function showResults(data) {
    if (!resultsRegion) return;
    const items = asArray(data.items).filter((item) => item && asText(item.slug));
    const summaryLabel = resultSummaryText(data, view);
    if (resultHeading) resultHeading.textContent = resultTitle(view, data.facets || {});
    if (resultSummary) resultSummary.textContent = summaryLabel;
    resultsRegion.setAttribute("aria-busy", "false");

    if (!items.length) {
      resultsRegion.innerHTML = emptyMarkup(data, view);
      return;
    }

    resultsRegion.innerHTML = "";
    const grid = document.createElement("div");
    grid.className = "product-grid catalog-result-grid";
    for (const card of items) {
      const node = productCard(card, { compact: false, showDelivery: true });
      node.dataset.productSlug = asText(card.slug);
      grid.appendChild(node);
    }
    resultsRegion.appendChild(grid);
    const pager = document.createElement("div");
    pager.innerHTML = paginationMarkup(data, view);
    if (pager.firstElementChild) resultsRegion.appendChild(pager.firstElementChild);
  }

  function finishLoad(data, { focusKey = "", focusResult = false, scroll = false } = {}) {
    lastData = data;
    lastPages = Math.max(1, Number(data.pages) || 1);
    view.page = Math.max(1, Number(data.page) || view.page);
    updateSortSelect();
    updateFilters(data.facets || {});
    updateActiveFilters(data.facets || {});
    showResults(data);
    applyTitle(data.facets || {});
    updateSortSelect();
    if (focusKey) window.requestAnimationFrame(() => focusByName(page, focusKey));
    if (focusResult) {
      window.requestAnimationFrame(() => {
        focusByName(page, "results");
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        resultHeading?.scrollIntoView({ block: "start", behavior: reduced ? "auto" : "smooth" });
      });
    } else if (scroll) {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      resultHeading?.scrollIntoView({ block: "start", behavior: reduced ? "auto" : "smooth" });
    }
  }

  async function loadProducts(options = {}) {
    const seq = ++requestSeq;
    showLoading();
    try {
      const data = await api.get(buildApiUrl(view), { signal: ctx.signal });
      if (isAborted(ctx.signal) || seq !== requestSeq) return;
      if (!data || typeof data !== "object" || !Array.isArray(data.items)) {
        throw new Error("目录服务返回了无法识别的结果");
      }
      const normalized = {
        items: data.items,
        total: asNumber(data.total) || 0,
        page: parsePositiveInt(data.page, view.page),
        page_size: parsePositiveInt(data.page_size, PAGE_SIZE),
        pages: parsePositiveInt(data.pages, 1),
        query: asText(data.query),
        sort: VALID_SORTS.includes(asText(data.sort)) ? asText(data.sort) : view.sort,
        facets: data.facets && typeof data.facets === "object" ? data.facets : {},
      };
      view.page = normalized.page;
      finishLoad(normalized, options);
    } catch (err) {
      if (isAborted(ctx.signal) || seq !== requestSeq || (err && err.name === "AbortError")) return;
      const isApi = err instanceof ApiError;
      showError(isApi ? err.message : err.message || "目录服务暂时不可用");
      ctx.setTitle("搜索暂时不可用 · OpenShop 开集");
    }
  }

  function changeAndReload({ focusKey = "", focusResult = false } = {}) {
    view.page = 1;
    syncUrl(view);
    if (searchInput) searchInput.value = view.q;
    loadProducts({ focusKey, focusResult });
  }

  function clearFilters({ focusKey = "category:all" } = {}) {
    view.category = "";
    view.kind = "";
    view.minPrice = null;
    view.maxPrice = null;
    view.minRating = 0;
    view.page = 1;
    syncUrl(view);
    loadProducts({ focusKey });
  }

  function resetSearch() {
    view.q = "";
    view.category = "";
    view.kind = "";
    view.sort = "featured";
    view.minPrice = null;
    view.maxPrice = null;
    view.minRating = 0;
    view.page = 1;
    if (searchInput) searchInput.value = "";
    if (sortSelect) sortSelect.value = view.sort;
    syncUrl(view);
    loadProducts({ focusResult: true });
  }

  function applyPriceFilter() {
    const minEl = page.querySelector("[data-price-min]");
    const maxEl = page.querySelector("[data-price-max]");
    const errorEl = page.querySelector("[data-price-error]");
    const readPrice = (el) => {
      const raw = el ? el.value.trim() : "";
      if (!raw) return { value: null };
      const n = Number(raw);
      if (!Number.isFinite(n) || n < 0) return { error: "请输入不小于 0 的数字。" };
      return { value: Math.round(n * 100) };
    };
    const min = readPrice(minEl);
    const max = readPrice(maxEl);
    let error = min.error || max.error || "";
    if (!error && min.value !== null && max.value !== null && min.value > max.value) error = "最低价不能高于最高价。";
    if (error) {
      if (errorEl) { errorEl.textContent = error; errorEl.hidden = false; }
      (min.error ? minEl : maxEl)?.focus();
      return;
    }
    if (errorEl) { errorEl.textContent = ""; errorEl.hidden = true; }
    view.minPrice = min.value;
    view.maxPrice = max.value;
    view.page = 1;
    syncUrl(view);
    loadProducts({ focusKey: "price" });
  }

  function removeChip(key) {
    if (key === "q") {
      view.q = "";
      if (searchInput) searchInput.value = "";
      view.page = 1;
      syncUrl(view);
      loadProducts({ focusKey: "search-input" });
      return;
    }
    if (key === "category") {
      view.category = "";
      view.page = 1;
      syncUrl(view);
      loadProducts({ focusKey: "category:all" });
      return;
    }
    if (key === "kind") {
      view.kind = "";
      view.page = 1;
      syncUrl(view);
      loadProducts({ focusKey: "kind:all" });
      return;
    }
    if (key === "price") {
      view.minPrice = null;
      view.maxPrice = null;
      view.page = 1;
      syncUrl(view);
      loadProducts({ focusKey: "price" });
      return;
    }
    if (key === "rating") {
      view.minRating = 0;
      view.page = 1;
      syncUrl(view);
      loadProducts({ focusKey: "rating:0" });
    }
  }

  page.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.matches("[data-search-form]")) {
      event.preventDefault();
      view.q = asText(searchInput?.value).slice(0, 60);
      changeAndReload({ focusResult: true });
      return;
    }
    if (form.matches("[data-price-form]")) {
      event.preventDefault();
      applyPriceFilter();
    }
  });

  page.addEventListener("change", (event) => {
    const select = event.target.closest("[data-search-sort]");
    if (!select) return;
    const nextSort = VALID_SORTS.includes(select.value) ? select.value : "featured";
    if (nextSort === view.sort) return;
    view.sort = nextSort;
    view.page = 1;
    syncUrl(view);
    loadProducts({ focusKey: "sort" });
  });

  page.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;

    if (button.matches("[data-filter-toggle]")) {
      const isOpen = filterToggle?.getAttribute("aria-expanded") === "true";
      setFilterOpen(!isOpen);
      return;
    }
    if (button.matches("[data-filter-close]")) {
      setFilterOpen(false);
      return;
    }

    const categoryButton = button.closest("[data-category-filter]");
    if (categoryButton) {
      const next = asText(categoryButton.dataset.categoryFilter);
      view.category = view.category === next ? "" : next;
      const focusKey = view.category ? "category:" + view.category : "category:all";
      view.page = 1;
      syncUrl(view);
      loadProducts({ focusKey });
      return;
    }

    const kindButton = button.closest("[data-kind-filter]");
    if (kindButton) {
      const next = asText(kindButton.dataset.kindFilter);
      view.kind = view.kind === next ? "" : next;
      const focusKey = view.kind ? "kind:" + view.kind : "kind:all";
      view.page = 1;
      syncUrl(view);
      loadProducts({ focusKey });
      return;
    }

    const ratingButton = button.closest("[data-rating-filter]");
    if (ratingButton) {
      const next = parsePositiveInt(ratingButton.dataset.ratingFilter, 0);
      const bounded = next >= 1 && next <= 5 ? next : 0;
      view.minRating = view.minRating === bounded ? 0 : bounded;
      view.page = 1;
      syncUrl(view);
      loadProducts({ focusKey: "rating:" + (view.minRating || 0) });
      return;
    }

    if (button.matches("[data-clear-filters]")) {
      clearFilters({ focusKey: "category:all" });
      return;
    }
    if (button.matches("[data-reset-search]")) {
      resetSearch();
      return;
    }
    if (button.matches("[data-retry-results]")) {
      loadProducts({ focusResult: true });
      return;
    }
    if (button.matches("[data-remove-filter]")) {
      removeChip(asText(button.dataset.removeFilter));
      return;
    }
    if (button.matches("[data-page]")) {
      const raw = asText(button.dataset.page);
      let nextPage = view.page;
      if (raw === "prev") nextPage = Math.max(1, view.page - 1);
      else if (raw === "next") nextPage = Math.min(lastPages, view.page + 1);
      else nextPage = parsePositiveInt(raw, view.page);
      if (nextPage === view.page) return;
      view.page = nextPage;
      syncUrl(view);
      loadProducts({ focusResult: true });
    }
  });

  filterPanel?.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!filterPanel.classList.contains("is-open")) return;
    event.preventDefault();
    setFilterOpen(false);
  });

  await loadProducts();
}

export { render };
