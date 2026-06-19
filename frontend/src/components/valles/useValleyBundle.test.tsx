import { renderHook, waitFor, act } from '@testing-library/react';
import { it, expect, vi, beforeEach } from 'vitest';
import { useValleyBundle } from './useValleyBundle';
import * as api from '../../api';

vi.mock('../../api');

const VIDA = { symbol: 'ADAUSDT', estado: 'ok', candidata: true, pos_in_30d_range: 0.12, rsi14: 38, vol_ratio: 0.7, price: 0.45, volumen_usd_dia: 1e7, razones_vida: [] };
const LVL  = { symbol: 'ADAUSDT', estado: 'ok', generated_at: null, price_live: 0.45, zonas: [], ubicacion: { dentro_de: null, techo: null, piso: null } };
const DOS  = { symbol: 'ADAUSDT', equipo: [], equipo_identificado: false, presencia: {}, actividad: {}, financiacion: [], hitos: [], estado_general: 'opaco', no_encontrado_en: [], generated_at: null };

beforeEach(() => {
  vi.mocked(api.getValleyEval).mockResolvedValue(VIDA as never);
  vi.mocked(api.getLevels).mockResolvedValue(LVL as never);
  vi.mocked(api.getDossier).mockResolvedValue(DOS as never);
});

it('arranca las 3 lentes en loading y resuelve cada una por separado', async () => {
  const { result } = renderHook(() => useValleyBundle('ADAUSDT'));
  expect(result.current.vida.loading).toBe(true);
  expect(result.current.niveles.loading).toBe(true);
  expect(result.current.dossier.loading).toBe(true);
  await waitFor(() => expect(result.current.vida.data).toEqual(VIDA));
  expect(result.current.niveles.data).toEqual(LVL);
  expect(result.current.dossier.data).toEqual(DOS);
});

it('marca error (no loading, no data) cuando un fetch falla', async () => {
  vi.mocked(api.getLevels).mockRejectedValue(new Error('429'));
  const { result } = renderHook(() => useValleyBundle('ADAUSDT'));
  await waitFor(() => expect(result.current.niveles.loading).toBe(false));
  expect(result.current.niveles.error).toBe(true);
  expect(result.current.niveles.data).toBeNull();
});

it('ignora la respuesta tardía del símbolo anterior (symbol-guard)', async () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let resolveA: (v: any) => void = () => {};
  vi.mocked(api.getValleyEval).mockImplementationOnce(() => new Promise((r) => { resolveA = r; }));
  const { result, rerender } = renderHook(({ s }) => useValleyBundle(s), { initialProps: { s: 'AAAUSDT' } });
  rerender({ s: 'BBBUSDT' });
  await waitFor(() => expect(result.current.vida.data).toMatchObject({ symbol: 'ADAUSDT' }));
  act(() => resolveA({ symbol: 'AAAUSDT', estado: 'ok', candidata: false }));
  expect(result.current.vida.data?.symbol).not.toBe('AAAUSDT');
});

it('refreshDossier vuelve a pedir el dossier con refresh=true, sin tocar vida/niveles', async () => {
  const { result } = renderHook(() => useValleyBundle('ADAUSDT'));
  await waitFor(() => expect(result.current.dossier.data).toEqual(DOS));
  vi.mocked(api.getValleyEval).mockClear();
  act(() => result.current.refreshDossier());
  await waitFor(() => expect(api.getDossier).toHaveBeenLastCalledWith('ADAUSDT', true));
  expect(api.getValleyEval).not.toHaveBeenCalled();
});
