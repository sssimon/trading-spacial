// ============================================================
// api.ts — API client for the Crypto Scanner backend
// Base URL: /api  (proxied by nginx → http://localhost:8000)
// ============================================================

import type {
  SymbolsResponse,
  StatusResponse,
  SignalsResponse,
  ScanResponse,
  WebhookTestResponse,
  SignalsParams,
  OhlcvResponse,
  AppConfig,
  ConfigUpdateResponse,
  SignalFilters,
  PositionsResponse,
  PositionCreatePayload,
  PositionUpdatePayload,
  PositionClosePayload,
  Position,
  TuneResult,
  NotificationsResponse,
  KillSwitchDecisionsResponse,
  KillSwitchCurrentStateResponse,
  KillSwitchEngine,
  DashboardResponse,
} from './types';

const BASE_URL = '/api';

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function readCsrfCookie(): string {
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

// Module-level guard against infinite refresh loops. Two requests racing on
// a 401 share a single refresh attempt.
let _refreshInflight: Promise<boolean> | null = null;

async function tryRefreshOnce(): Promise<boolean> {
  if (_refreshInflight) return _refreshInflight;
  _refreshInflight = (async () => {
    try {
      const r = await fetch(`${BASE_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      });
      return r.ok;
    } catch {
      return false;
    } finally {
      // Release the gate on next tick so back-to-back 401s can still share it.
      setTimeout(() => {
        _refreshInflight = null;
      }, 0);
    }
  })();
  return _refreshInflight;
}

async function rawRequest(path: string, options?: RequestInit): Promise<Response> {
  const url = `${BASE_URL}${path}`;
  const method = (options?.method || 'GET').toUpperCase();
  const headers = new Headers(options?.headers || {});
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCsrfCookie();
    if (csrf) headers.set('X-CSRF-Token', csrf);
  }
  return fetch(url, {
    ...options,
    method,
    headers,
    credentials: 'include',
  });
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response = await rawRequest(path, options);

  // 401 interceptor: try one silent refresh, then retry once. If refresh
  // also 401s, dispatch the user to /login.
  if (response.status === 401 && !path.startsWith('/auth/')) {
    const refreshed = await tryRefreshOnce();
    if (refreshed) {
      response = await rawRequest(path, options);
    } else {
      if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
        window.location.assign('/login');
      }
      throw new Error('API error 401: not authenticated');
    }
  }

  if (!response.ok) {
    const errorText = await response.text().catch(() => response.statusText);
    throw new Error(`API error ${response.status}: ${errorText}`);
  }

  return response.json() as Promise<T>;
}

// GET /symbols
export async function getSymbols(): Promise<SymbolsResponse> {
  return request<SymbolsResponse>('/symbols');
}

// GET /status
export async function getStatus(): Promise<StatusResponse> {
  return request<StatusResponse>('/status');
}

// GET /signals
export async function getSignals(params: SignalsParams = {}): Promise<SignalsResponse> {
  const query = new URLSearchParams();

  if (params.limit !== undefined) {
    query.set('limit', String(params.limit));
  }
  if (params.only_signals !== undefined) {
    query.set('only_signals', String(params.only_signals));
  }
  if (params.since_hours !== undefined) {
    query.set('since_hours', String(params.since_hours));
  }
  if (params.symbol !== undefined && params.symbol !== '') {
    query.set('symbol', params.symbol);
  }

  const qs = query.toString();
  const path = qs ? `/signals?${qs}` : '/signals';
  return request<SignalsResponse>(path);
}

// POST /scan?symbol=
export async function forceScan(symbol?: string): Promise<ScanResponse> {
  const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : '';
  return request<ScanResponse>(`/scan${query}`, { method: 'POST' });
}

// GET /webhook/test
export async function testWebhook(): Promise<WebhookTestResponse> {
  return request<WebhookTestResponse>('/webhook/test');
}

// GET /ohlcv
export async function getOhlcv(
  symbol: string,
  interval: string = '1h',
  limit: number = 300,
): Promise<OhlcvResponse> {
  const query = new URLSearchParams({
    symbol,
    interval,
    limit: String(limit),
  });
  return request<OhlcvResponse>(`/ohlcv?${query}`);
}

// GET /config
export async function getConfig(): Promise<AppConfig> {
  return request<AppConfig>('/config');
}

// POST /config  (partial update — only signal_filters)
export async function updateConfig(
  filters: SignalFilters
): Promise<ConfigUpdateResponse> {
  return request<ConfigUpdateResponse>('/config', {
    method: 'POST',
    body: JSON.stringify({ signal_filters: filters }),
  });
}

// ---- Positions ------------------------------------------------------------

// GET /positions?status=open|closed|all
export async function getPositions(status: 'open' | 'closed' | 'all' = 'all'): Promise<PositionsResponse> {
  return request<PositionsResponse>(`/positions?status=${status}`);
}

// POST /positions
export async function openPosition(payload: PositionCreatePayload): Promise<{ ok: boolean; position: Position }> {
  return request<{ ok: boolean; position: Position }>('/positions', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// PUT /positions/{id}
export async function updatePosition(id: number, payload: PositionUpdatePayload): Promise<{ ok: boolean; position: Position }> {
  return request<{ ok: boolean; position: Position }>(`/positions/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

// POST /positions/{id}/close
export async function closePosition(id: number, payload: PositionClosePayload): Promise<{ ok: boolean; position: Position }> {
  return request<{ ok: boolean; position: Position }>(`/positions/${id}/close`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// DELETE /positions/{id}
export async function cancelPosition(id: number): Promise<{ ok: boolean; message: string }> {
  return request<{ ok: boolean; message: string }>(`/positions/${id}`, {
    method: 'DELETE',
  });
}

// ---- Auto-Tune -------------------------------------------------------

// GET /tune/latest
export async function getTuneLatest(): Promise<TuneResult | null> {
  return request<TuneResult | null>('/tune/latest');
}

// POST /tune/apply
export async function applyTune(): Promise<{ ok: boolean; applied: number; backup: string }> {
  return request<{ ok: boolean; applied: number; backup: string }>('/tune/apply', {
    method: 'POST',
  });
}

// POST /tune/reject
export async function rejectTune(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/tune/reject', { method: 'POST' });
}

// POST /config — extended to support auto_approve_tune
export async function updateConfigFull(
  body: { signal_filters?: SignalFilters; auto_approve_tune?: boolean }
): Promise<ConfigUpdateResponse> {
  return request<ConfigUpdateResponse>('/config', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// ---- Notifications (#162 PR C) ---------------------------------------

// GET /notifications?unread=true&limit=50 — defaults to unread only
export async function getNotifications(opts: { unread?: boolean; limit?: number } = {}): Promise<NotificationsResponse> {
  const params = new URLSearchParams();
  if (opts.unread !== undefined) params.set('unread', String(opts.unread));
  if (opts.limit !== undefined) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return request<NotificationsResponse>(`/notifications${qs ? `?${qs}` : ''}`);
}

// POST /notifications/{id}/read
export async function markNotificationRead(id: number): Promise<{ ok: boolean; id: number }> {
  return request<{ ok: boolean; id: number }>(`/notifications/${id}/read`, {
    method: 'POST',
  });
}

// POST /notifications/read-all
export async function markAllNotificationsRead(): Promise<{ ok: boolean; marked: number }> {
  return request<{ ok: boolean; marked: number }>(`/notifications/read-all`, {
    method: 'POST',
  });
}

// ---- Kill switch observability (#187 phase 1) ---------------------------

export async function getKillSwitchDecisions(
  opts: {
    symbol?: string;
    engine?: KillSwitchEngine;
    since?: string;
    limit?: number;
  } = {},
): Promise<KillSwitchDecisionsResponse> {
  const params = new URLSearchParams();
  if (opts.symbol) params.set('symbol', opts.symbol);
  if (opts.engine) params.set('engine', opts.engine);
  if (opts.since) params.set('since', opts.since);
  if (opts.limit !== undefined) params.set('limit', String(opts.limit));
  const qs = params.toString();
  return request<KillSwitchDecisionsResponse>(
    `/kill_switch/decisions${qs ? `?${qs}` : ''}`,
  );
}

export async function getKillSwitchCurrentState(
  engine: KillSwitchEngine = 'v1',
): Promise<KillSwitchCurrentStateResponse> {
  return request<KillSwitchCurrentStateResponse>(
    `/kill_switch/current_state?engine=${engine}`,
  );
}

export async function getHealthDashboard(): Promise<DashboardResponse> {
  return request<DashboardResponse>('/health/dashboard');
}
