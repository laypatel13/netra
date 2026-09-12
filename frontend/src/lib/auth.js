/**
 * Frontend-only demo login. Gates netra's own routes for the deployed demo -
 * it does not add auth to API requests, which still rely on the backend's
 * X-Role header model (see Registry.jsx). One seeded account, no session
 * refresh or expiry: this exists so a public deployment isn't wide open, not
 * as a real auth system.
 */
import { api, ApiError } from "./api.js";

const STORAGE_KEY = "netra-demo-token";

export function isAuthenticated() {
  try {
    return Boolean(localStorage.getItem(STORAGE_KEY));
  } catch {
    return false;
  }
}

export async function login(email, password) {
  const { token } = await api("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  try {
    localStorage.setItem(STORAGE_KEY, token);
  } catch {
    throw new ApiError("Could not store the session - is storage blocked (private window)?");
  }
}

export function logout() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage blocked - nothing to clear */
  }
}
