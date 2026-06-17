import { describe, it, expect } from 'vitest';
import { buildOverlays } from './overlays';
import type { PlanDerived } from '../../../types';

const plan: PlanDerived = {
  entry: 0.419, sl_plan: 0.385, sl_piso: { centro: 0.392, precio_bajo: 0.388, precio_alto: 0.398, toques: 5 },
  entry_zone: { centro: 0.419, precio_bajo: 0.412, precio_alto: 0.426, toques: 5 },
  rungs: [
    { tp_price: 0.448, size_frac: 0.50, zona: { centro: 0.448, precio_bajo: 0.445, precio_alto: 0.451, toques: 2 } },
    { tp_price: 0.474, size_frac: 0.20, zona: { centro: 0.474, precio_bajo: 0.470, precio_alto: 0.478, toques: 4 } },
  ],
  runner_frac: 0.05,
};

describe('buildOverlays', () => {
  it('zona de entrada es banda (no linea): low != high', () => {
    const o = buildOverlays({ plan, live: 0.4205, state: null });
    expect(o.zone).toBeTruthy();
    expect(o.zone!.priceLow).toBe(0.412);
    expect(o.zone!.priceHigh).toBe(0.426);
  });
  it('precio dentro de la zona -> live.fuera false; arriba -> arriba', () => {
    expect(buildOverlays({ plan, live: 0.4205, state: null }).live.fuera).toBe(false);
    expect(buildOverlays({ plan, live: 0.4400, state: null }).live.fuera).toBe('arriba');
  });
  it('rung lleno cuando state.rungs_llenos lo incluye; BE mueve el stop', () => {
    const o = buildOverlays({ plan, live: 0.45, state: { rungs_llenos: [0], be_movido: true, sl_actual: 0.419 } });
    expect(o.rungs[0].filled).toBe(true);
    expect(o.rungs[1].filled).toBe(false);
    expect(o.stop.be).toBe(true);
    expect(o.stop.price).toBe(0.419);
  });
  it('escalera corta (1 rung) marca gap honesto', () => {
    const corto = { ...plan, rungs: [plan.rungs[0]] };
    expect(buildOverlays({ plan: corto, live: 0.4205, state: null }).gap).toBe(true);
  });
});
