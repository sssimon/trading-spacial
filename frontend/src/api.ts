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
} from './types';

const BASE_URL = '/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    ...options,
  });

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
