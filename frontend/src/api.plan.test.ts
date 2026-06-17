import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import * as api from './api';

const originalFetch = globalThis.fetch;

const derivedPayload = {
  entry: 0.419,
  sl_plan: 0.385,
  sl_piso: null,
  rungs: [],
  runner_frac: 0.05,
  entry_zone: null,
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  globalThis.fetch = vi.fn(async () => jsonResponse(derivedPayload));
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe('plan fetchers', () => {
  it('getPlanDerive hits /api/plan/derive with entry_price', async () => {
    await api.getPlanDerive('ADAUSDT', 0.4205);
    const url = String((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]);
    expect(url).toContain('/plan/derive/ADAUSDT');
    expect(url).toContain('entry_price=0.4205');
  });

  it('getPlanLive hits /api/plan/:symbol', async () => {
    await api.getPlanLive('ADAUSDT');
    const url = String((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]);
    expect(url).toContain('/api/plan/ADAUSDT');
  });

  it('getPlanConducta hits /api/plan/:symbol/conducta', async () => {
    await api.getPlanConducta('ADAUSDT');
    const url = String((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]);
    expect(url).toContain('/api/plan/ADAUSDT/conducta');
  });

  it('confirmPlan POSTs with snake_case body', async () => {
    await api.confirmPlan('ADAUSDT', 0.42, 7);
    const spy = fetch as ReturnType<typeof vi.fn>;
    const opts = spy.mock.calls[0][1] as RequestInit;
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body as string)).toMatchObject({
      symbol: 'ADAUSDT',
      entry_price: 0.42,
      position_id: 7,
    });
  });

  it('confirmPlan with no positionId sends position_id: null', async () => {
    await api.confirmPlan('ADAUSDT', 0.42);
    const spy = fetch as ReturnType<typeof vi.fn>;
    const opts = spy.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(opts.body as string);
    expect(body.position_id).toBeNull();
  });
});
