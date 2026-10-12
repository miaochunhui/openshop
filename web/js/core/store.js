// Tiny observable store shared by the app shell and page modules.
import { api, setCsrf } from "./api.js";

const listeners = new Set();
let loadCategoriesPromise = null;

export const state = {
  session: null,
  user: null,
  csrf: "",
  categories: [],
  cartCount: 0,
  favoriteCount: 0,
  bootError: null,
  route: null,
};

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit() {
  for (const listener of [...listeners]) {
    try { listener(state); } catch (err) { console.error("store listener failed", err); }
  }
}

export function patchState(patch) {
  Object.assign(state, patch);
  emit();
}

export function applySession(session) {
  if (!session || typeof session !== "object") return;
  if (typeof session.csrf === "string") setCsrf(session.csrf);
  state.session = session;
  state.user = session.user || null;
  state.csrf = session.csrf || "";
  state.cartCount = Number(session.cart_count || 0);
  state.favoriteCount = Number(session.favorite_count || 0);
  emit();
}

export async function refreshSession({ signal } = {}) {
  const session = await api.get("/api/session", { signal });
  applySession(session);
  return session;
}

export function updateCartCount(count) {
  state.cartCount = Math.max(0, Number(count) || 0);
  emit();
}

export function addToCartCount(delta) {
  updateCartCount(state.cartCount + Number(delta || 0));
}

export async function loadCategories({ signal } = {}) {
  if (loadCategoriesPromise) return loadCategoriesPromise;
  loadCategoriesPromise = api.get("/api/categories", { signal }).then((data) => {
    state.categories = Array.isArray(data) ? data : (data && data.items) || [];
    emit();
    return state.categories;
  }).catch((err) => {
    if (err && err.name === "AbortError") throw err;
    state.categories = state.categories || [];
    emit();
    return state.categories;
  }).finally(() => {
    loadCategoriesPromise = null;
  });
  return loadCategoriesPromise;
}

export async function logout() {
  const session = await api.post("/api/auth/logout", {});
  applySession(session);
  return session;
}

export function isSeller() {
  return Boolean(state.user && state.user.role === "seller" && state.user.shop);
}

export function isLoggedIn() {
  return Boolean(state.user);
}
