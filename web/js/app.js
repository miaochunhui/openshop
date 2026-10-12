// App shell, navigation and page router. No framework, no build step.
import { api, ApiError } from "./core/api.js";
import { loadCategories, refreshSession, state, subscribe } from "./core/store.js";
import { escapeHtml, icon, openAuth, toast } from "./components/index.js";

const app = document.getElementById("app");
const ROUTES = [
  { name: "home", pattern: /^\/$/, module: "home", title: "OpenShop 开集 · 逛得开，买得值" },
  { name: "search", pattern: /^\/search\/?$/, module: "catalog", title: "搜索与发现 · OpenShop 开集" },
  { name: "product", pattern: /^\/p\/([^/]+)\/?$/, module: "product", title: "商品详情 · OpenShop 开集", keys: ["slug"] },
  { name: "cart", pattern: /^\/cart\/?$/, module: "cart", title: "购物车 · OpenShop 开集" },
  { name: "checkout", pattern: /^\/checkout\/?$/, module: "checkout", title: "结算 · OpenShop 开集" },
  { name: "account", pattern: /^\/account\/?$/, module: "account", title: "我的开集 · OpenShop 开集" },
  { name: "order", pattern: /^\/orders\/([^/]+)\/?$/, module: "order", title: "订单详情 · OpenShop 开集", keys: ["orderNo"] },
  { name: "group", pattern: /^\/g\/([^/]+)\/?$/, module: "group", title: "一起拼 · OpenShop 开集", keys: ["code"] },
  { name: "seller", pattern: /^\/seller\/?$/, module: "seller", title: "商家工作台 · OpenShop 开集" },
  { name: "about", pattern: /^\/about\/?$/, module: "about", title: "关于开集 · 开源电商" },
  { name: "help", pattern: /^\/help\/?$/, module: "help", title: "帮助与规则 · OpenShop 开集" },
];

let currentController = null;
let currentPath = location.pathname + location.search;
let activeRoute = null;

const loadingLine = document.createElement("div");
loadingLine.className = "loading-line";
document.body.appendChild(loadingLine);

function matchRoute(pathname) {
  for (const route of ROUTES) {
    const match = pathname.match(route.pattern);
    if (!match) continue;
    const params = {};
    (route.keys || []).forEach((key, index) => { params[key] = decodeURIComponent(match[index + 1] || ""); });
    return { route, params };
  }
  return { route: null, params: {} };
}

export function navigate(path, { replace = false, scroll = true } = {}) {
  if (!path || path.startsWith("http://") || path.startsWith("https://")) {
    location.href = path;
    return;
  }
  const url = new URL(path, location.origin);
  if (url.origin !== location.origin) { location.href = url.href; return; }
  const next = url.pathname + url.search + url.hash;
  if (next === currentPath) return;
  if (replace) history.replaceState({}, "", next);
  else history.pushState({}, "", next);
  currentPath = next;
  if (scroll) window.scrollTo({ top: 0, behavior: "instant" });
  renderRoute();
}

function updateActiveNav(routeName) {
  document.querySelectorAll(".mobile-tabbar [data-nav]").forEach((link) => {
    const nav = link.dataset.nav;
    const active = (routeName === "home" && nav === "home") ||
      (["search", "product"].includes(routeName) && nav === "search") ||
      (["cart", "checkout", "group"].includes(routeName) && nav === "cart") ||
      (["account", "order", "seller"].includes(routeName) && nav === "account");
    link.classList.toggle("is-active", active);
    if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  });
}

function bootError(message) {
  app.innerHTML = `<section class="container boot-error">
    <span class="eyebrow">CONNECTION</span>
    <h1>摊位还没支好</h1>
    <p>${escapeHtml(message)}</p>
    <button class="btn btn--primary" type="button" id="boot-retry">重新连接</button>
    <p class="tiny muted">如果问题持续，请确认本地 OpenShop 服务正在运行。</p>
  </section>`;
  app.querySelector("#boot-retry")?.addEventListener("click", () => location.reload());
}

async function renderRoute() {
  if (currentController) currentController.abort();
  currentController = new AbortController();
  const { signal } = currentController;
  const url = new URL(location.href);
  const { route, params } = matchRoute(url.pathname);
  activeRoute = route;
  updateActiveNav(route?.name);
  loadingLine.classList.add("active");
  document.body.dataset.route = route?.name || "notfound";
  document.querySelectorAll(".modal-scrim").forEach((el) => el.remove());
  document.body.classList.remove("has-modal");

  if (!route) {
    document.title = "没找到这个摊位 · OpenShop";
    app.innerHTML = `<section class="container notfound-page">
      <div class="notfound-code">404</div><p class="eyebrow">走错巷子了</p>
      <h1>这个摊位今天没出。</h1><p class="lede">你找的页面不存在，或者已经搬到别的巷口。</p>
      <div class="cluster mt-3"><a class="btn btn--primary" href="/" data-link>回首页逛逛</a><a class="btn btn--ghost" href="/search" data-link>去搜索</a></div>
    </section>`;
    loadingLine.classList.remove("active");
    return;
  }

  document.title = route.title;
  app.innerHTML = `<div class="container route-loading" role="status"><div class="skeleton" style="height:52vh;border-radius:26px"></div><p class="sr-only">正在打开页面</p></div>`;
  const ctx = {
    root: app,
    params,
    query: url.searchParams,
    path: url.pathname,
    navigate,
    signal,
    setTitle(title) { if (title) document.title = title; },
    meta: state,
  };
  try {
    const module = await import(`./pages/${route.module}.js`);
    if (signal.aborted) return;
    if (typeof module.render !== "function") throw new Error(`page module ${route.module} has no render()`);
    await module.render(ctx);
    if (!signal.aborted) {
      app.dataset.ready = "1";
      requestAnimationFrame(() => {
        const heading = app.querySelector("h1");
        if (heading) {
          heading.setAttribute("tabindex", "-1");
          heading.focus({ preventScroll: true });
        }
      });
    }
  } catch (err) {
    if (err && err.name === "AbortError") return;
    console.error("route render failed", err);
    document.title = "页面开小差了 · OpenShop";
    const isApi = err instanceof ApiError;
    app.innerHTML = `<section class="container error-page">
      <span class="eyebrow">${isApi ? escapeHtml(err.code) : "PAGE_ERROR"}</span>
      <h1>这张桌子先歇一会儿</h1>
      <p class="lede">${escapeHtml(isApi ? err.message : "页面暂时没有打开，请稍后再试。")}</p>
      <div class="cluster mt-3"><button class="btn btn--primary" type="button" id="page-retry">再试一次</button><a class="btn btn--ghost" href="/" data-link>回首页</a></div>
    </section>`;
    app.querySelector("#page-retry")?.addEventListener("click", () => renderRoute());
  } finally {
    if (!signal.aborted) loadingLine.classList.remove("active");
  }
}

function applyChrome() {
  const user = state.user;
  const accountLabel = document.getElementById("account-label");
  const avatar = document.getElementById("account-avatar");
  if (user) {
    accountLabel.textContent = user.role === "seller" ? `${user.display_name} · 商家` : `你好，${user.display_name}`;
    avatar.innerHTML = user.avatar ? `<img src="${escapeHtml(user.avatar)}" alt="">` : escapeHtml(user.display_name.slice(0, 1));
  } else {
    accountLabel.textContent = "登录 / 注册";
    avatar.textContent = "客";
  }
  const count = state.cartCount || 0;
  for (const id of ["cart-badge", "mobile-cart-badge"]) {
    const badge = document.getElementById(id);
    if (!badge) continue;
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count <= 0;
  }
  document.getElementById("cart-action")?.setAttribute("aria-label", `购物车，${count} 件商品`);
  document.getElementById("build-stamp").textContent = `OpenShop v0.1.2 · ${new Date().toLocaleDateString("zh-CN")}`;
}

function renderCategories() {
  const wrap = document.getElementById("category-links");
  const drawer = document.getElementById("drawer-categories");
  if (!state.categories.length) {
    if (wrap) wrap.innerHTML = '<a href="/search" data-link>全部商品</a>';
    if (drawer) drawer.innerHTML = '<a href="/search" data-link>全部商品 <span>ALL</span></a>';
    return;
  }
  if (wrap) wrap.innerHTML = state.categories.map((cat) => `<a href="/search?category=${encodeURIComponent(cat.slug)}" data-link>${escapeHtml(cat.name)}</a>`).join("");
  if (drawer) drawer.innerHTML = state.categories.map((cat) => `<a href="/search?category=${encodeURIComponent(cat.slug)}" data-link>${escapeHtml(cat.name)} <span>${String(cat.count || 0).padStart(2, "0")}</span></a>`).join("");
}

function setDrawer(open) {
  const drawer = document.getElementById("mobile-drawer");
  const scrim = document.getElementById("nav-scrim");
  const toggle = document.getElementById("menu-toggle");
  if (!drawer || !scrim || !toggle) return;
  drawer.hidden = !open;
  scrim.hidden = !open;
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
  document.body.classList.toggle("has-modal", open);
  if (open) drawer.querySelector("a,button")?.focus();
}

function wireChrome() {
  subscribe(() => { applyChrome(); renderCategories(); });
  applyChrome();
  renderCategories();

  document.getElementById("account-action")?.addEventListener("click", () => {
    if (state.user) navigate("/account");
    else openAuth({ onSuccess: () => { applyChrome(); renderRoute(); } });
  });
  document.getElementById("menu-toggle")?.addEventListener("click", () => setDrawer(true));
  document.getElementById("drawer-close")?.addEventListener("click", () => setDrawer(false));
  document.getElementById("nav-scrim")?.addEventListener("click", () => setDrawer(false));

  document.getElementById("header-search")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.getElementById("global-search");
    const q = (input?.value || "").trim();
    hideSuggest();
    navigate(q ? `/search?q=${encodeURIComponent(q)}` : "/search");
  });

  const searchInput = document.getElementById("global-search");
  const suggestBox = document.getElementById("search-suggest");
  let suggestSeq = 0;
  const hideSuggest = () => { if (suggestBox) suggestBox.hidden = true; };
  const showSuggest = () => { if (suggestBox) suggestBox.hidden = false; };
  const doSuggest = async () => {
    if (!searchInput || !suggestBox) return;
    const q = searchInput.value.trim();
    if (q.length < 2) { hideSuggest(); return; }
    const seq = ++suggestSeq;
    try {
      const data = await api.get(`/api/search/suggest?q=${encodeURIComponent(q)}`);
      if (seq !== suggestSeq) return;
      const suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
      const products = Array.isArray(data.products) ? data.products : [];
      const hot = Array.isArray(data.hot) ? data.hot : [];
      if (!suggestions.length && !products.length && !hot.length) { hideSuggest(); return; }
      suggestBox.innerHTML = `
        ${suggestions.length ? `<div class="suggest-label">搜索建议</div>${suggestions.slice(0, 5).map((item) => `<button type="button" data-suggest-q="${escapeHtml(typeof item === "string" ? item : item.text)}">${icon("search", 15)} ${escapeHtml(typeof item === "string" ? item : item.text)}</button>`).join("")}` : ""}
        ${products.length ? `<div class="suggest-label">商品</div>${products.slice(0, 4).map((item) => `<a href="/p/${encodeURIComponent(item.slug)}" data-link data-suggest-link><img src="${escapeHtml(item.cover)}" alt="" width="34" height="34" style="object-fit:cover;border-radius:7px"> ${escapeHtml(item.title)}</a>`).join("")}` : ""}
        ${!suggestions.length && !products.length && hot.length ? `<div class="suggest-label">大家在搜</div>${hot.slice(0, 5).map((word) => `<button type="button" data-suggest-q="${escapeHtml(word)}">${icon("spark", 15)} ${escapeHtml(word)}</button>`).join("")}` : ""}
      `;
      suggestBox.querySelectorAll("[data-suggest-q]").forEach((button) => button.addEventListener("click", () => {
        if (searchInput) searchInput.value = button.dataset.suggestQ || "";
        hideSuggest();
        navigate(`/search?q=${encodeURIComponent(button.dataset.suggestQ || "")}`);
      }));
      suggestBox.querySelectorAll("[data-suggest-link]").forEach((link) => link.addEventListener("click", hideSuggest));
      showSuggest();
    } catch (err) {
      if (!(err instanceof ApiError)) console.error(err);
      hideSuggest();
    }
  };
  let timer = null;
  searchInput?.addEventListener("input", () => { window.clearTimeout(timer); timer = window.setTimeout(doSuggest, 220); });
  suggestBox?.addEventListener("click", (event) => {
    if (event.target.closest("a")) hideSuggest();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { hideSuggest(); setDrawer(false); }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); searchInput?.focus(); searchInput?.select(); }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".header-search")) hideSuggest();
  });

  document.addEventListener("click", (event) => {
    const anchor = event.target.closest("a");
    if (!anchor) return;
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (anchor.target && anchor.target !== "_self") return;
    if (anchor.hasAttribute("download")) return;
    const url = new URL(anchor.href, location.href);
    if (url.origin !== location.origin) return;
    if (anchor.dataset.native === "true") return;
    if (url.pathname + url.search + url.hash === currentPath) { event.preventDefault(); return; }
    if (anchor.getAttribute("href")?.startsWith("#")) return;
    event.preventDefault();
    setDrawer(false);
    navigate(url.pathname + url.search + url.hash);
  });
  window.addEventListener("popstate", () => {
    currentPath = location.pathname + location.search + location.hash;
    renderRoute();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setDrawer(false);
  });
}

async function boot() {
  try {
    wireChrome();
    await refreshSession();
  } catch (err) {
    console.error("session boot failed", err);
    bootError(err instanceof ApiError ? "会话服务暂时不可用。" : "暂时无法连接 OpenShop 服务。");
    return;
  }
  loadCategories().catch(() => {});
  renderRoute();
}

boot();
