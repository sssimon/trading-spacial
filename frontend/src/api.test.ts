import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { getSymbols, getStatus } from './api';

const originalFetch = globalThis.fetch;

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  globalThis.fetch = vi.fn(impl as typeof fetch);
}

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

describe('api client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  describe('getSymbols', () => {
    it('returns typed response with symbols array on 200', async () => {
      const payload = {
        total: 2,
        symbols: [
          { symbol: 'BTCUSDT', estado: 'ok', price: 50_000, lrc_pct: 20, score: 6, señal: true, gatillo: true, ts: '2026-04-22T12:00:00Z' },
          { symbol: 'ETHUSDT', estado: 'ok', price: 3_000, lrc_pct: 30, score: 4, señal: false, gatillo: true, ts: '2026-04-22T12:00:00Z' },
        ],
      };
      mockFetch(async () => jsonResponse(payload));

      const resp = await getSymbols();

      expect(resp.total).toBe(2);
      expect(resp.symbols).toHaveLength(2);
      expect(resp.symbols[0].symbol).toBe('BTCUSDT');
      expect(resp.symbols[1].score).toBe(4);
    });

    it('hits /api/symbols', async () => {
      const spy = vi.fn<typeof fetch>(async () => jsonResponse({ total: 0, symbols: [] }));
      globalThis.fetch = spy;

      await getSymbols();

      expect(spy).toHaveBeenCalledTimes(1);
      expect(String(spy.mock.calls[0][0])).toBe('/api/symbols');
    });
  });

  describe('request error handling', () => {
    it('throws when fetch rejects (network error)', async () => {
      mockFetch(async () => { throw new TypeError('Failed to fetch'); });

      await expect(getStatus()).rejects.toThrow(/Failed to fetch/);
    });

    it('throws on non-2xx response with status and body text', async () => {
      mockFetch(async () => new Response('internal server error', {
        status: 500,
        statusText: 'Internal Server Error',
      }));

      await expect(getStatus()).rejects.toThrow(/API error 500: internal server error/);
    });

    it('throws on 404 with response body in message', async () => {
      mockFetch(async () => new Response('not found', { status: 404 }));

      await expect(getStatus()).rejects.toThrow(/API error 404: not found/);
    });
  });
});
