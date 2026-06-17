import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';
import { buildOverlays, type LiveState, type OverlayModel } from '../jugada/overlays';

export const LAYER_KEYS = ['vida', 'paredes', 'jugada'] as const;
export type LayerKey = typeof LAYER_KEYS[number];
export type LayerVisibility = Record<LayerKey, boolean>;
export const DEFAULT_LAYERS: LayerVisibility = { vida: true, paredes: true, jugada: true };

export interface Wall {
  tipo: 'soporte' | 'resistencia';
  centro: number;
  low: number;
  high: number;
  toques: number;
}

export interface LayersModel {
  vida: { band: { low: number; high: number } | null; semanas: number; vivoStamp: string };
  paredes: { walls: Wall[]; price: number | null };
  jugada: OverlayModel;
}

export function buildLayers(args: {
  vida: ValleyEval;
  levels: SrLevels;
  plan: PlanDerived | null;
  live: number;
  state: LiveState | null;
}): LayersModel {
  const { vida, levels, plan, live, state } = args;

  // vida layer: consolidation band centred on the live price using pct_rango
  const band =
    vida?.pct_rango != null && live
      ? { low: live * (1 - vida.pct_rango / 2), high: live * (1 + vida.pct_rango / 2) }
      : null;

  // paredes layer: all S/R zones mapped to a flat Wall shape
  const walls: Wall[] = (levels?.zonas ?? []).map((z) => ({
    tipo: z.tipo,
    centro: z.centro,
    low: z.precio_bajo,
    high: z.precio_alto,
    toques: z.toques,
  }));

  // jugada layer: delegate to the canonical buildOverlays; use a safe empty
  // OverlayModel when no plan has been derived yet
  const jugada: OverlayModel = plan
    ? buildOverlays({ plan, live, state })
    : {
        zone: null,
        stop: { price: 0, be: false, piso: null },
        rungs: [],
        runner: null,
        live: { price: live, fuera: false },
        gap: false,
      };

  return {
    vida: {
      band,
      semanas: vida?.semanas_consolidando ?? 0,
      vivoStamp:
        vida?.vivo
          ? `viva · vol $${Math.round((vida.volumen_usd_dia ?? 0) / 1000)}k/día`
          : 'sin actividad',
    },
    paredes: { walls, price: levels?.price_live ?? null },
    jugada,
  };
}
