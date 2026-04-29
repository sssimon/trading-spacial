// Auth API client. Used by AuthContext only — the rest of the app talks
// through the regular src/api.ts (which automatically includes credentials
// and CSRF header thanks to the wrapper update).

const BASE = '/api';

export interface AuthUser {
  id: number;
  email: string;
  role: 'admin' | 'viewer';
  is_active: boolean;
  last_login_at: string | null;
}

export class AuthError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function readCsrfCookie(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

async function call(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers || {});
  headers.set('Content-Type', 'application/json');

  const method = (init.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
    const csrf = readCsrfCookie();
    if (csrf) headers.set('X-CSRF-Token', csrf);
  }

  return fetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });
}

async function jsonOr<T>(resp: Response): Promise<T> {
  const text = await resp.text();
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = JSON.parse(text)?.detail ?? text;
    } catch {
      detail = text || resp.statusText;
    }
    throw new AuthError(resp.status, String(detail));
  }
  return text ? (JSON.parse(text) as T) : ({} as T);
}

export async function login(email: string, password: string): Promise<{ user: AuthUser }> {
  const resp = await call('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
  return jsonOr<{ user: AuthUser }>(resp);
}

export async function logout(): Promise<{ ok: boolean }> {
  const resp = await call('/auth/logout', { method: 'POST' });
  // Even if logout fails server-side, the frontend treats this as logged-out
  // (the cookies are likely gone or invalid anyway).
  if (!resp.ok) return { ok: false };
  return jsonOr<{ ok: boolean }>(resp);
}

export async function refresh(): Promise<{ user: AuthUser } | null> {
  const resp = await call('/auth/refresh', { method: 'POST' });
  if (resp.status === 401) return null;
  if (!resp.ok) return null;
  return jsonOr<{ user: AuthUser }>(resp);
}

export async function me(): Promise<{ user: AuthUser } | null> {
  const resp = await call('/auth/me', { method: 'GET' });
  if (resp.status === 401) return null;
  return jsonOr<{ user: AuthUser }>(resp);
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ ok: boolean; message: string }> {
  const resp = await call('/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
  return jsonOr<{ ok: boolean; message: string }>(resp);
}
