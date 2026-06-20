import { describe, it, expect } from 'vitest';
import { buildLayers, LAYER_KEYS } from './chartLayers';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';

const vida = { pos_in_30d_range: 0.18, rsi14: 38, pct_vs_sma20: -6, pct_vs_sma50: -9, consol_30d: 40, vol_ratio: 0.7, drawdown_from_90h: -35, volumen_usd_dia: 4_000_000, vivo: true } as ValleyEval;
const levels = { estado: 'ok', price_live: 0.42, ubicacion: {}, zonas: [
  { tipo: 'soporte', precio_bajo: 0.388, precio_alto: 0.398, centro: 0.392, toques: 5 },
  { tipo: 'resistencia', precio_bajo: 0.445, precio_alto: 0.451, centro: 0.448, toques: 2 },
] } as unknown as SrLevels;

// 40 candles: the last 30 carry the range; the older 10 carry extreme values
// that MUST be excluded (range30 = min low / max high of the *last 30* only).
const mkCandle = (lo: number, hi: number, t: number) => ({ time: t, open: lo, high: hi, low: lo, close: hi });
const candlesLevels = (over: Partial<SrLevels> = {}): SrLevels => ({
  ...(levels as unknown as Record<string, unknown>),
  candles: [
    // 10 OLD candles with absurd extremes that should NOT enter range30
    ...Array.from({ length: 10 }, (_, i) => mkCandle(0.01, 9.99, i)),
    // 30 RECENT candles: lows in [0.30..], highs up to 0.50
    ...Array.from({ length: 30 }, (_, i) => mkCandle(0.30 + i * 0.001, 0.40 + i * 0.0033, 100 + i)),
  ],
  ...over,
} as unknown as SrLevels);
const plan = { entry: 0.419, sl_plan: 0.385, sl_piso: null, entry_zone: { centro:0.419, precio_bajo:0.412, precio_alto:0.426, toques:5 }, rungs: [{ tp_price:0.448, size_frac:0.5, zona:{centro:0.448,precio_bajo:0.445,precio_alto:0.451,toques:2} }], runner_frac: 0.05 } as PlanDerived;

describe('buildLayers', () => {
  it('expone las 3 capas con claves estables', () => {
    expect(LAYER_KEYS).toEqual(['vida', 'paredes', 'jugada']);
  });
  it('paredes incluye TODAS las zonas de levels', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.paredes.walls).toHaveLength(2);
    expect(m.paredes.walls.find(w => w.tipo === 'resistencia')!.toques).toBe(2);
  });
  it('vida expone el sello de posición en rango', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.vida.pos).toBeCloseTo(0.18);
    expect(m.vida.vivoStamp).toMatch(/pos/i);
  });
  it('vida.range30 se computa del min/max de las ÚLTIMAS 30 candles (no del backend)', () => {
    const m = buildLayers({ vida, levels: candlesLevels(), plan, live: 0.42, state: null });
    expect(m.vida.range30).not.toBeNull();
    // min low de las últimas 30 = 0.30 (la candle vieja con 0.01 queda fuera)
    expect(m.vida.range30!.lo).toBeCloseTo(0.30, 5);
    // max high de las últimas 30 = 0.40 + 29*0.0033 = 0.4957 (la vieja 9.99 queda fuera)
    expect(m.vida.range30!.hi).toBeCloseTo(0.4957, 4);
  });
  it('vida.pos refleja pos_in_30d_range', () => {
    const m = buildLayers({ vida, levels: candlesLevels(), plan, live: 0.42, state: null });
    expect(m.vida.pos).toBeCloseTo(0.18);
  });
  it('range30 es null cuando no hay candles', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.vida.range30).toBeNull();
  });
  it('range30 usa TODAS las candles cuando hay menos de 30', () => {
    const few = candlesLevels({ candles: [
      { time: 1, open: 0.4, high: 0.5, low: 0.35, close: 0.45 },
      { time: 2, open: 0.45, high: 0.55, low: 0.40, close: 0.50 },
    ] } as unknown as SrLevels);
    const m = buildLayers({ vida, levels: few, plan, live: 0.42, state: null });
    expect(m.vida.range30!.lo).toBeCloseTo(0.35, 5);
    expect(m.vida.range30!.hi).toBeCloseTo(0.55, 5);
  });
  it('jugada reusa buildOverlays', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.jugada.zone).not.toBeNull();
    expect(m.jugada.rungs).toHaveLength(1);
  });
  it('tolera data nula (pos null, walls vacio)', () => {
    const m = buildLayers({ vida: {} as ValleyEval, levels: { zonas: [] } as unknown as SrLevels, plan, live: 0, state: null });
    expect(m.vida.pos).toBeNull();
    expect(m.paredes.walls).toEqual([]);
  });
});
