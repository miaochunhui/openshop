// 首页：品牌主活动、品类索引、可信承诺与编辑货架。
// 所有商品、价格与统计数字都来自 /api/home；本文件不写死业务数字。
import { api, ApiError } from "../core/api.js";
import {
  emptyState,
  escapeHtml,
  icon,
  priceHtml,
  productCard,
  sectionHead,
  skeletonCards,
} from "../components/index.js";

const asArray = (value) => (Array.isArray(value) ? value : []);
const asNumber = (value, fallback = null) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};
const asText = (value) => (typeof value === "string" ? value.trim() : "");
const formatInt = (value) => {
  const parsed = asNumber(value);
  return parsed === null ? "" : parsed.toLocaleString("zh-CN");
};
const cardHref = (slug) => "/p/" + encodeURIComponent(String(slug || ""));
const internalHref = (value, fallback = "/search") => {
  const href = asText(value);
  return href.startsWith("/") && !href.startsWith("//") ? href : fallback;
};
const categoryHref = (slug) => "/search?category=" + encodeURIComponent(String(slug || ""));
const usableCards = (value) => asArray(value).filter((card) => card && asText(card.slug));
const isAborted = (signal) => Boolean(signal && signal.aborted);

function loadingMarkup() {
  return `<div class="discovery-page home-page" data-testid="home-root">
    <div class="container home-loading" role="status" aria-label="正在打开首页">
      <div class="home-skeleton-hero">
        <div class="home-skeleton-copy">
          <span class="skeleton" style="width:96px;height:12px"></span>
          <span class="skeleton" style="width:min(430px,88%);height:72px;margin-top:26px"></span>
          <span class="skeleton" style="width:min(330px,72%);height:17px;margin-top:22px"></span>
          <span class="skeleton" style="width:210px;height:48px;margin-top:34px"></span>
        </div>
        <div class="skeleton home-skeleton-image"></div>
      </div>
      <div class="home-skeleton-rail">
        ${Array.from({ length: 4 }, () => `<span class="skeleton"></span>`).join("")}
      </div>
      ${skeletonCards(4)}
      <p class="sr-only">正在从服务端取回今日首页内容。</p>
    </div>
  </div>`;
}

function errorMarkup(message, isApi) {
  return `<div class="discovery-page home-page" data-testid="home-root">
    <div class="container home-state">
      <section class="home-state__card" role="alert" aria-labelledby="home-error-title">
        <p class="eyebrow">${isApi ? "API" : "CONNECTION"}</p>
        <h1 id="home-error-title">这张桌子还没摆好</h1>
        <p class="lede">${escapeHtml(message || "首页暂时没有打开，请稍后再试。")}</p>
        <div class="cluster mt-3">
          <button class="btn btn--primary" type="button" data-home-retry>再试一次</button>
          <a class="btn btn--ghost" href="/search" data-link>先去搜索</a>
        </div>
        <p class="tiny muted mt-2">商品、价格与评价均为虚构演示数据。</p>
      </section>
    </div>
  </div>`;
}

function heroSideMarkup(cards) {
  return cards.map((card) => {
    const title = asText(card.title);
    const brand = asText(card.brand);
    const slug = asText(card.slug);
    const price = asNumber(card.price_cents);
    const cover = asText(card.cover);
    const alt = asText(card.cover_alt) || title;
    return `<a class="home-hero__mini" href="${cardHref(slug)}" data-link aria-label="查看 ${escapeHtml(title)}">
      <span class="home-hero__mini-art">
        ${cover ? `<img src="${escapeHtml(cover)}" alt="${escapeHtml(alt)}" loading="lazy" decoding="async" data-discovery-img data-fallback-label="${escapeHtml(alt)}">` : `<span class="home-hero__mini-placeholder" aria-hidden="true">${icon("box", 22)}</span>`}
      </span>
      <span class="home-hero__mini-copy">
        <span class="home-hero__mini-brand">${escapeHtml(brand || "开集选物")}</span>
        <b>${escapeHtml(title)}</b>
        ${price !== null ? priceHtml(price, { className: "home-hero__mini-price" }) : ""}
      </span>
      <span class="home-hero__mini-arrow" aria-hidden="true">${icon("arrowRight", 14)}</span>
    </a>`;
  }).join("");
}

function heroSearchMarkup() {
  return `<form class="home-hero__search" role="search" data-home-search data-testid="home-search">
    <span class="home-hero__search-icon" aria-hidden="true">${icon("search", 17)}</span>
    <label class="sr-only" for="home-search-input">搜索商品、品牌或分类</label>
    <input id="home-search-input" name="q" type="search" maxlength="60" autocomplete="off" placeholder="搜耳机、沙发、背包……">
    <button type="submit">去搜</button>
  </form>`;
}

function fallbackHeroMarkup() {
  return `<section class="home-hero" aria-label="OpenShop 开集品牌活动">
    <div class="container">
      <div class="home-hero__panel">
        <div class="home-hero__copy">
          <div class="home-hero__kicker"><span class="home-hero__brand">OPENSHOP <em>开集</em></span><span class="home-hero__eyebrow">${icon("spark", 13)} 本期开场</span></div>
          <h1 class="home-hero__title">逛得开，<br>买得值。</h1>
          <p class="home-hero__body">一间把自营、品牌与源头拼团放在同一张桌子上的集市。慢慢看，按自己的节奏买。</p>
          ${heroSearchMarkup()}
          <div class="home-hero__actions">
            <a class="btn btn--primary btn--large" href="/search" data-link data-testid="hero-cta">去集市逛逛 ${icon("arrowRight", 16)}</a>
            <a class="btn btn--ghost btn--large home-hero__ghost" href="/about" data-link>开集怎么选物</a>
          </div>
          <p class="home-hero__disclaimer">商品、价格与评价来自当前在售数据；结算金额以服务端重算为准。支付为模拟流程。</p>
        </div>
        <div class="home-hero__media home-hero__media--fallback" aria-hidden="true">
          <div class="home-hero__empty-art">
            <span class="home-hero__empty-roof"></span>
            <span class="home-hero__empty-word">OPENSHOP</span>
            <span class="home-hero__empty-sub">逛得开 · 买得值</span>
          </div>
        </div>
      </div>
    </div>
  </section>`;
}

function heroMarkup(data) {
  const hero = data && typeof data.hero === "object" && data.hero ? data.hero : null;
  if (!hero) return fallbackHeroMarkup();
  const sideCards = usableCards(data.hero_side).slice(0, 2);
  const title = asText(hero.title) || "逛得开，买得值。";
  const body = asText(hero.body);
  const eyebrow = asText(hero.eyebrow);
  const badge = asText(hero.badge);
  const image = asText(hero.image);
  const slug = asText(hero.product_slug);
  const price = asNumber(hero.price_cents);
  const href = slug ? cardHref(slug) : "/search";
  const imageAlt = title + " 主活动视觉";
  return `<section class="home-hero" aria-label="本周主活动">
    <div class="container">
      <div class="home-hero__panel">
        <div class="home-hero__copy">
          <div class="home-hero__kicker">
            <span class="home-hero__brand">OPENSHOP <em>开集</em></span>
            ${eyebrow ? `<span class="home-hero__eyebrow">${icon("spark", 13)} ${escapeHtml(eyebrow)}</span>` : ""}
          </div>
          <h1 class="home-hero__title">${escapeHtml(title)}</h1>
          ${body ? `<p class="home-hero__body">${escapeHtml(body)}</p>` : ""}
          ${heroSearchMarkup()}
          <div class="home-hero__offerline">
            ${price !== null ? `<span class="home-hero__offer-label">本期活动价</span>${priceHtml(price, { className: "home-hero__price price--lg" })}` : ""}
            ${badge ? `<span class="home-hero__badge">${escapeHtml(badge)}</span>` : ""}
          </div>
          <div class="home-hero__actions">
            <a class="btn btn--primary btn--large" href="${href}" data-link data-testid="hero-cta">看看本期选物 ${icon("arrowRight", 16)}</a>
            <a class="btn btn--ghost btn--large home-hero__ghost" href="/search" data-link>逛全部商品</a>
          </div>
          <p class="home-hero__disclaimer">价格与供给以商品页实时数据为准，结算由服务端重算。演示环境，支付为模拟流程。</p>
        </div>
        <div class="home-hero__media">
          ${image ? `<div class="home-hero__figure">
            <img src="${escapeHtml(image)}" alt="${escapeHtml(imageAlt)}" fetchpriority="high" decoding="async" data-discovery-img data-fallback-label="${escapeHtml(imageAlt)}">
            <span class="home-hero__stamp" aria-hidden="true">OPENSHOP<br><b>开集选物</b></span>
            ${badge ? `<span class="home-hero__figure-tag">${escapeHtml(badge)}</span>` : ""}
          </div>` : ""}
          ${sideCards.length ? `<div class="home-hero__side" aria-label="主活动搭配选物">${heroSideMarkup(sideCards)}</div>` : ""}
        </div>
      </div>
    </div>
  </section>`;
}

function assuranceMarkup(data) {
  const trust = asArray(data.trust).filter((item) => item && asText(item.title));
  const stats = data.stats && typeof data.stats === "object" ? data.stats : {};
  const metrics = [
    { key: "products", label: "件选物" },
    { key: "shops", label: "家店铺" },
    { key: "reviews", label: "条评价" },
  ].map(({ key, label }) => {
    const value = formatInt(stats[key]);
    return value ? `<span class="home-assurance__metric"><b>${value}</b><em>${label}</em></span>` : "";
  }).filter(Boolean).join("");
  if (!trust.length && !metrics) return "";
  return `<section class="home-assurance" aria-label="开集的服务承诺">
    <div class="container">
      <div class="home-assurance__panel">
        ${trust.length ? `<div class="home-assurance__points">${trust.map((item) => `<div class="home-assurance__point">
          <span class="home-assurance__icon">${icon(asText(item.icon) || "shield", 21)}</span>
          <span><b>${escapeHtml(asText(item.title))}</b><em>${escapeHtml(asText(item.detail))}</em></span>
        </div>`).join("")}</div>` : ""}
        ${metrics ? `<div class="home-assurance__stats" aria-label="市场概况">${metrics}</div>` : ""}
      </div>
    </div>
  </section>`;
}

function categoriesMarkup(data) {
  const categories = asArray(data.categories).filter((cat) => cat && asText(cat.slug) && asText(cat.name));
  if (!categories.length) return "";
  const tiles = categories.map((cat, index) => {
    const count = formatInt(cat.count);
    return `<a class="home-category" href="${categoryHref(cat.slug)}" data-link>
      <span class="home-category__top"><em>${String(index + 1).padStart(2, "0")}</em>${icon("arrowRight", 14)}</span>
      <b class="home-category__name">${escapeHtml(asText(cat.name))}</b>
      ${asText(cat.subtitle) ? `<span class="home-category__sub">${escapeHtml(asText(cat.subtitle))}</span>` : ""}
      ${count ? `<span class="home-category__count">${count} 件</span>` : ""}
    </a>`;
  }).join("");
  return `<section class="home-categories" aria-labelledby="home-categories-title">
    <div class="container">
      <div class="home-section-heading">
        <div><p class="eyebrow">CATEGORIES · 品类</p><h2 id="home-categories-title">按品类，慢慢逛</h2><p class="home-section-heading__desc">从一盏灯、一副耳机，找到今天需要的那一件。</p></div>
        <a class="link-arrow home-section-link" href="/search" data-link>全部商品 ${icon("arrowRight", 15)}</a>
      </div>
      <div class="home-category-rail" aria-label="商品分类">${tiles}</div>
    </div>
  </section>`;
}

function makeProductGrid(cards, { className = "product-grid", compact = false, horizontal = false, testId = "" } = {}) {
  const grid = document.createElement("div");
  grid.className = className;
  grid.setAttribute("data-home-product-list", "");
  if (testId) grid.dataset.testid = testId;
  for (const card of usableCards(cards)) {
    const node = productCard(card, { compact, horizontal, showDelivery: !horizontal });
    if (!node || !node.querySelector) continue;
    const slug = asText(card.slug);
    if (slug) node.dataset.productSlug = slug;
    grid.appendChild(node);
  }
  return grid;
}

function shelfMarkup(shelf) {
  const cards = usableCards(shelf && shelf.items);
  if (!cards.length) return "";
  const href = internalHref(shelf.href, "/search");
  return `<section class="home-shelf" aria-label="${escapeHtml(asText(shelf.title) || "编辑货架")}">
    <div class="container">
      ${sectionHead({
        eyebrow: "SHELF · 编辑货架",
        title: asText(shelf.title) || "本期选物",
        description: asText(shelf.subtitle),
        href,
        linkLabel: "逛这一栏",
      })}
      <div data-shelf-grid></div>
    </div>
  </section>`;
}

function dealsMarkup(deals) {
  const cards = usableCards(deals).slice(0, 4);
  if (!cards.length) return "";
  return `<section class="home-deals" aria-labelledby="home-deals-title">
    <div class="container">
      <div class="home-deals__panel">
        <div class="home-deals__head">
          <div><p class="eyebrow eyebrow--jade">TODAY'S VALUE · 今日好价</p><h2 id="home-deals-title">今天，买得值一点</h2><p>从当前在售选物里挑出的价格友好款。</p></div>
          <div class="home-deals__aside"><span>${cards.length} 件在列</span><a class="link-arrow" href="/search?sort=price_asc" data-link>按价格逛 ${icon("arrowRight", 15)}</a></div>
        </div>
        <div data-deals-grid></div>
      </div>
    </div>
  </section>`;
}

function groupMarkup(groupPicks) {
  const cards = usableCards(groupPicks).slice(0, 4);
  if (!cards.length) return "";
  const sizes = [...new Set(cards.map((card) => asNumber(card.group && card.group.size)).filter((size) => size !== null && size > 0))];
  const sizeText = sizes.length ? `${sizes.sort((a, b) => a - b).join(" / ")} 人成团` : "多人拼团";
  return `<section class="home-group" aria-labelledby="home-group-title">
    <div class="container">
      <div class="home-group__panel">
        <div class="home-group__intro">
          <p class="eyebrow">GROUP BUY · 一起拼</p>
          <h2 id="home-group-title">叫上一个人，<br>把价格拼松一点。</h2>
          <p>本期待选的拼团供给开放 ${escapeHtml(sizeText)}。价格、库存与成团进度都来自实时服务，不写死、不制造倒计时。</p>
          <div class="home-group__facts">
            <span><b>${cards.length}</b> 个拼团供给</span>
            <span><b>${sizes.length || "实时"}</b>${sizes.length ? " 种成团人数" : " 成团信息"}</span>
          </div>
          <a class="btn btn--primary" href="/search?sort=price_asc" data-link>看看拼团选物 ${icon("arrowRight", 16)}</a>
          <p class="home-group__note">发起拼团后生成真实邀请码；是否成团以拼团页的服务端进度为准。</p>
        </div>
        <div data-group-grid></div>
      </div>
    </div>
  </section>`;
}

function arrivalsMarkup() {
  return `<section class="home-arrivals" aria-labelledby="home-arrivals-title">
    <div class="container">
      ${sectionHead({
        eyebrow: "JUST IN · 刚刚入集",
        title: "新来的，也值得看看",
        description: "最近上架的选物，按服务端在售状态更新。",
        href: "/search?sort=newest",
        linkLabel: "看最新上架",
      })}
      <div data-arrivals-grid></div>
    </div>
  </section>`;
}

function personalMarkup(recent, recs) {
  return `<section class="home-personal" aria-labelledby="home-personal-title">
    <div class="container">
      <div class="home-section-heading home-personal__heading">
        <div><p class="eyebrow">YOUR MARKET · 为你留着</p><h2 id="home-personal-title">逛过的，和还没逛到的</h2></div>
        <p class="home-personal__note">浏览与推荐来自当前会话与在售数据。</p>
      </div>
      <div class="home-personal__grid">
        <div class="home-personal__recent">
          <div class="home-personal__subhead"><h3>最近浏览</h3><span>${recent.length ? "继续看看" : "等待你的足迹"}</span></div>
          <div class="home-recent-list" data-recent-list>${recent.length ? "" : emptyState({ title: "还没有浏览足迹", message: "打开感兴趣的商品，下一次回来就能在这里找到它。", actionLabel: "去找点灵感", actionHref: "/search", iconName: "search" })}</div>
        </div>
        <div class="home-personal__recommended">
          <div class="home-personal__subhead"><h3>为你推荐</h3><span>在有货的选物里挑的</span></div>
          ${recs.length ? `<div data-recommend-grid></div>` : emptyState({ title: "推荐还在准备", message: "先逛逛全部商品，服务端会根据你的浏览更新这里。", actionLabel: "去逛逛", actionHref: "/search", iconName: "spark" })}
        </div>
      </div>
    </div>
  </section>`;
}

function wireImageFallbacks(root) {
  root.querySelectorAll("img[data-discovery-img]").forEach((img) => {
    img.addEventListener("error", () => {
      const label = img.dataset.fallbackLabel || img.alt || "开集选物";
      const fallback = document.createElement("span");
      fallback.className = "home-img-fallback";
      fallback.setAttribute("role", "img");
      fallback.setAttribute("aria-label", label);
      fallback.textContent = "开集选物";
      img.replaceWith(fallback);
    }, { once: true });
  });
}

function wireHomeSearch(ctx, root) {
  root.querySelectorAll("[data-home-search]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = form.querySelector("input[name=q]");
      const q = (input && input.value ? input.value : "").trim();
      ctx.navigate(q ? "/search?q=" + encodeURIComponent(q) : "/search");
    });
  });
}

function finishFirstGrid(root) {
  const firstGrid = root.querySelector("[data-home-product-list]");
  if (firstGrid) firstGrid.dataset.testid = "home-product-grid";
}

function mountShelf(shelf) {
  const holder = document.createElement("div");
  holder.innerHTML = shelfMarkup(shelf);
  const section = holder.firstElementChild;
  if (!section) return null;
  const gridHost = section.querySelector("[data-shelf-grid]");
  const grid = makeProductGrid(shelf.items, { className: "product-grid" });
  if (!gridHost) return null;
  gridHost.replaceWith(grid);
  return section;
}

function renderHome(root, data, ctx) {
  const shelves = asArray(data.shelves).filter((shelf) => shelf && usableCards(shelf.items).length);
  const deals = usableCards(data.deals);
  const groupPicks = usableCards(data.group_picks);
  const newArrivals = usableCards(data.new_arrivals);
  const recent = usableCards(data.recently_viewed);
  const recommended = usableCards(data.recommended);

  root.innerHTML = `<div class="discovery-page home-page" data-testid="home-root">
    ${heroMarkup(data)}
    ${assuranceMarkup(data)}
    ${categoriesMarkup(data)}
    <div data-home-stack></div>
  </div>`;
  const stack = root.querySelector("[data-home-stack]");
  if (!stack) return;

  const shelfSections = shelves.map(mountShelf).filter(Boolean);
  if (shelfSections[0]) stack.appendChild(shelfSections[0]);

  if (deals.length) {
    const holder = document.createElement("div");
    holder.innerHTML = dealsMarkup(deals);
    const section = holder.firstElementChild;
    const host = section && section.querySelector("[data-deals-grid]");
    if (section && host) {
      host.replaceWith(makeProductGrid(deals.slice(0, 4), { className: "product-grid" }));
      stack.appendChild(section);
    }
  }

  shelfSections.slice(1).forEach((section) => stack.appendChild(section));

  if (groupPicks.length) {
    const holder = document.createElement("div");
    holder.innerHTML = groupMarkup(groupPicks);
    const section = holder.firstElementChild;
    const host = section && section.querySelector("[data-group-grid]");
    if (section && host) {
      host.replaceWith(makeProductGrid(groupPicks.slice(0, 4), { className: "product-grid home-group__grid" }));
      stack.appendChild(section);
    }
  }

  if (newArrivals.length) {
    const holder = document.createElement("div");
    holder.innerHTML = arrivalsMarkup();
    const section = holder.firstElementChild;
    const host = section && section.querySelector("[data-arrivals-grid]");
    if (section && host) {
      host.replaceWith(makeProductGrid(newArrivals.slice(0, 4), { className: "product-grid" }));
      stack.appendChild(section);
    }
  }

  if (recent.length || recommended.length) {
    const holder = document.createElement("div");
    holder.innerHTML = personalMarkup(recent, recommended);
    const section = holder.firstElementChild;
    if (section) {
      const recentHost = section.querySelector("[data-recent-list]");
      if (recentHost && recent.length) {
        recent.slice(0, 4).forEach((card) => {
          const node = productCard(card, { compact: true, horizontal: true, showDelivery: false });
          const slug = asText(card.slug);
          if (slug) node.dataset.productSlug = slug;
          recentHost.appendChild(node);
        });
      }
      const recHost = section.querySelector("[data-recommend-grid]");
      if (recHost) recHost.replaceWith(makeProductGrid(recommended.slice(0, 4), { className: "product-grid home-recommend-grid" }));
      stack.appendChild(section);
    }
  }

  if (!shelves.length && !deals.length && !groupPicks.length && !newArrivals.length && !recent.length && !recommended.length) {
    stack.innerHTML = `<div class="container home-empty">${emptyState({
      title: "今天集上还在摆货",
      message: "首页已经打开，但当前没有可展示的商品。稍后再来，或直接用搜索找找看。",
      actionLabel: "去搜索",
      actionHref: "/search",
      iconName: "box",
    })}</div>`;
  }

  finishFirstGrid(root);
  wireImageFallbacks(root);
  wireHomeSearch(ctx, root);
}

async function render(ctx) {
  ctx.setTitle("OpenShop 开集 · 逛得开，买得值");
  const host = ctx.root;

  const load = async () => {
    if (isAborted(ctx.signal)) return;
    host.innerHTML = loadingMarkup();
    try {
      const data = await api.get("/api/home", { signal: ctx.signal });
      if (isAborted(ctx.signal)) return;
      renderHome(host, data && typeof data === "object" ? data : {}, ctx);
    } catch (err) {
      if (isAborted(ctx.signal) || (err && err.name === "AbortError")) return;
      const isApi = err instanceof ApiError;
      host.innerHTML = errorMarkup(isApi ? err.message : "首页暂时连不上服务，请稍后再试。", isApi);
      host.querySelector("[data-home-retry]")?.addEventListener("click", load);
    }
  };

  await load();
}

export { render };
