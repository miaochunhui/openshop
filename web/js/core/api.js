// Same-origin JSON client. No third-party dependencies.
let csrfToken = "";
let lastMeta = null;

export class ApiError extends Error {
  constructor(status, code, message, fields = {}) {
    super(message || "请求失败");
    this.name = "ApiError";
    this.status = status;
    this.code = code || "UNKNOWN";
    this.fields = fields || {};
  }
}

export function setCsrf(token) {
  csrfToken = typeof token === "string" ? token : "";
}

export function getCsrf() {
  return csrfToken;
}

export function meta() {
  return lastMeta;
}

async function request(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (method !== "GET" && method !== "HEAD") {
    headers["Content-Type"] = "application/json";
    if (csrfToken) headers["X-OpenShop-CSRF"] = csrfToken;
  }
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      cache: "no-store",
    });
  } catch (err) {
    if (err && err.name === "AbortError") throw err;
    throw new ApiError(0, "NETWORK", "暂时连不上开集，请检查网络后重试");
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch (_) {
    payload = null;
  }
  lastMeta = payload && payload.meta ? payload.meta : null;
  if (!response.ok || (payload && payload.error)) {
    const error = payload && payload.error ? payload.error : {};
    throw new ApiError(response.status, error.code || "HTTP_ERROR", error.message || `请求失败（${response.status}）`, error.fields || {});
  }
  return payload && Object.prototype.hasOwnProperty.call(payload, "data") ? payload.data : payload;
}

export const api = {
  request,
  get: (path, options = {}) => request(path, { ...options, method: "GET" }),
  post: (path, body, options = {}) => request(path, { ...options, method: "POST", body }),
  patch: (path, body, options = {}) => request(path, { ...options, method: "PATCH", body }),
  del: (path, options = {}) => request(path, { ...options, method: "DELETE" }),
};
