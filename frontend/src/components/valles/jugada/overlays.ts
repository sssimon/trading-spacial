// overlays.ts
import type { PlanDerived } from '../../../types';

export interface LiveState { rungs_llenos: number[]; be_movido: boolean; sl_actual: number | null; }
export interface OverlayModel {
  zone: { priceLow: number; priceHigh: number; toques: number } | null;
  stop: { price: number; be: boolean; piso: number | null };
  rungs: { price: number; sizeFrac: number; toques: number | null; filled: boolean }[];
  runner: { frac: number; fromPrice: number } | null;
  live: { price: number; fuera: 'arriba' | 'abajo' | false };
  gap: boolean;  // escalera corta: no hay más techos
}

export function buildOverlays(args: { plan: PlanDerived; live: number; state: LiveState | null }): OverlayModel {
  const { plan, live, state } = args;
  const z = plan.entry_zone;
  let fuera: 'arriba' | 'abajo' | false = false;
  if (z) { if (live > z.precio_alto) fuera = 'arriba'; else if (live < z.precio_bajo) fuera = 'abajo'; }
  const llenos = state?.rungs_llenos ?? [];
  const topRung = plan.rungs.length ? plan.rungs[plan.rungs.length - 1].tp_price : plan.entry;
  return {
    zone: z ? { priceLow: z.precio_bajo, priceHigh: z.precio_alto, toques: z.toques } : null,
    stop: { price: state?.be_movido ? (state.sl_actual ?? plan.sl_plan) : plan.sl_plan,
            be: !!state?.be_movido, piso: plan.sl_piso?.centro ?? null },
    rungs: plan.rungs.map((r, i) => ({ price: r.tp_price, sizeFrac: r.size_frac,
            toques: r.zona?.toques ?? null, filled: llenos.includes(i) })),
    runner: plan.runner_frac > 0 ? { frac: plan.runner_frac, fromPrice: topRung } : null,
    live: { price: live, fuera },
    gap: plan.rungs.length === 1,
  };
}
