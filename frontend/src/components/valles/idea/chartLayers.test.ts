import { describe, it, expect } from 'vitest';
import { buildLayers, LAYER_KEYS } from './chartLayers';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';

const vida = { pct_rango: 0.18, semanas_consolidando: 12, volumen_usd_dia: 4_000_000, vivo: true } as ValleyEval;
const levels = { estado: 'ok', price_live: 0.42, ubicacion: {}, zonas: [
  { tipo: 'soporte', precio_bajo: 0.388, precio_alto: 0.398, centro: 0.392, toques: 5 },
  { tipo: 'resistencia', precio_bajo: 0.445, precio_alto: 0.451, centro: 0.448, toques: 2 },
] } as unknown as SrLevels;
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
  it('vida expone la banda de consolidacion como rango', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.vida.band).not.toBeNull();
    expect(m.vida.band!.low).toBeLessThan(m.vida.band!.high);
    expect(m.vida.semanas).toBe(12);
  });
  it('jugada reusa buildOverlays', () => {
    const m = buildLayers({ vida, levels, plan, live: 0.42, state: null });
    expect(m.jugada.zone).not.toBeNull();
    expect(m.jugada.rungs).toHaveLength(1);
  });
  it('tolera data nula (band null, walls vacio)', () => {
    const m = buildLayers({ vida: {} as ValleyEval, levels: { zonas: [] } as unknown as SrLevels, plan, live: 0, state: null });
    expect(m.vida.band).toBeNull();
    expect(m.paredes.walls).toEqual([]);
  });
});
