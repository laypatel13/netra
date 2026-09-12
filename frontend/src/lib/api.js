/**
 * Thin fetch wrapper over the backend (proxied at /api by vite.config.js).
 *
 * Every page previously hand-rolled its own fetch + error string, which is why
 * failure messages were inconsistent ("HTTP 500" in one place, silence in
 * another). One helper means one honest error message everywhere: a backend
 * that isn't running reads differently from a request the backend rejected.
 */
export class ApiError extends Error {
  constructor(message, { status = null, offline = false } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.offline = offline;
  }
}

// In dev this stays "/api" and rides the Vite proxy (vite.config.js). In a
// production static build there's no proxy, so VITE_API_BASE_URL must point
// straight at the deployed backend's public URL.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, options);
  } catch {
    throw new ApiError("Could not reach the backend - is it running?", { offline: true });
  }

  if (!res.ok) {
    let detail = null;
    try {
      const body = await res.json();
      detail = body?.detail;
      if (Array.isArray(detail)) detail = detail[0]?.msg ?? null;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail || `Request failed (HTTP ${res.status})`, { status: res.status });
  }

  if (res.status === 204) return null;
  return res.json();
}

/** Resolve a backend-relative asset path (thumbnails, HLS playlists) to the proxy. */
export const asset = (path) =>
  !path ? null : path.startsWith("/") ? `${API_BASE}${path}` : path;
