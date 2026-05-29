import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import OpenPositionModal from './OpenPositionModal';
import type { SymbolStatus } from '../types';
import * as api from '../api';

// Regression for the production 422 "qty Field required": the backend
// OpenPositionRequest requires `qty` (and forbids extra fields). The modal
// computes qty = size/entry for display but must also SEND it.
vi.mock('../api', () => ({
  openPosition: vi.fn().mockResolvedValue({ ok: true, position: { id: 1 } }),
}));

function symbols(): SymbolStatus[] {
  return [{
    symbol: 'BTCUSDT', estado: 'ok', price: 50_000, lrc_pct: 20, score: 6,
    señal: false, gatillo: true, ts: '2026-04-22T12:00:00Z',
  } as SymbolStatus];
}

describe('OpenPositionModal', () => {
  beforeEach(() => vi.clearAllMocks());

  it('sends qty (= size/entry) in the open-position payload', async () => {
    render(
      <OpenPositionModal
        symbols={symbols()}
        prefill={{ symbol: 'BTCUSDT', price: 50_000, sizeUsd: 1000 }}
        onClose={() => {}}
        onCreated={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Abrir/ }));
    await waitFor(() => expect(api.openPosition).toHaveBeenCalledTimes(1));

    const payload = (api.openPosition as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.qty).toBeCloseTo(1000 / 50_000, 10);   // 0.02
    expect(payload.qty).toBeGreaterThan(0);
    expect(payload.entry_price).toBe(50_000);
    expect(payload.size_usd).toBe(1000);
    // qty * entry_price ≈ size_usd — the backend cross-field invariant.
    expect(payload.qty * payload.entry_price).toBeCloseTo(payload.size_usd, 6);
  });

  it('blocks submit and does not call the API when capital is empty (qty would be 0)', async () => {
    render(
      <OpenPositionModal
        symbols={symbols()}
        prefill={{ symbol: 'BTCUSDT', price: 50_000 }}  // no sizeUsd → capital empty
        onClose={() => {}}
        onCreated={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Abrir/ }));
    await waitFor(() =>
      expect(screen.getByText(/capital.*mayor a 0/i)).toBeInTheDocument());
    expect(api.openPosition).not.toHaveBeenCalled();
  });
});
