// ============================================================
// hierarchy.ts — smart bucketing + focus computation
// ============================================================
// The "intelligence" that decides what matters now. Drives both
// the FocusPanel ("qué mirar ahora") and the bucketed watchlist
// (featured / watching / quiet).
// ============================================================

import type { SymbolStatus, Position, StatusResponse } from '../types';

// Scanner timestamps carry a ' UTC' suffix ('YYYY-MM-DD HH:MM:SS UTC') that
// `new Date()` can't parse. Normalize to a Z-suffixed string so recency math
// (freshness window, bucket tiebreak) doesn't silently NaN — which would mark
// every signal "fresh". Mirrors MercadoView's minsSince parse.
const tsMs = (ts: string): number => new Date(ts.replace(' UTC', 'Z')).getTime();

// ────────────────────────────────────────────────────────────
// Focus items — surfaced at the top of Mercado as cards.
// ────────────────────────────────────────────────────────────

export type FocusKind =
  | 'risk-position'   // open position close to SL
  | 'fresh-signal'    // newly-fired high-score symbol
  | 'near-tp'         // position close to take profit
  | 'kill-switch'     // pairs auto-paused
  | 'error';          // scanner errors in last cycle

export interface FocusItem {
  kind: FocusKind;
  priority: number;
  title: string;
  body: string;
  action: string;
  pair?: string;
  // arbitrary handle for downstream wiring (open chart for pair X, etc.)
  refId?: string | number | null;
}

/**
 * Compute the prioritised attention queue. The FocusPanel renders the
 * top 4 items.
 *
 * Heuristics (cheap, can run on every render — does NOT call the network):
 *   1. Open positions near SL (proximity > 0.55 of TP-SL range, or pnl < -0.8%)
 *   2. Fresh, high-score firing signals (score >= 5, trigger true,
 *      ts within `freshWithinMin` minutes of `now`)
 *   3. Open positions near TP (proximity > 0.7)
 *   4. Macro alerts (kill-switch active, scanner errors)
 */
export function computeFocus(
  symbols: SymbolStatus[],
  positions: Position[],
  status: StatusResponse | null,
  now: number = Date.now(),
  freshWithinMin: number = 8,
): FocusItem[] {
  const items: FocusItem[] = [];
  const scanner = status?.scanner_state;

  // 1. Positions at risk
  for (const p of positions) {
    if (p.status !== 'open') continue;
    if (p.sl_price == null || p.tp_price == null) continue;
    const range = Math.abs(p.tp_price - p.sl_price);
    if (range === 0) continue;
    // approximate "current" via entry + pnl; if no current price available,
    // fall back to entry price.
    const current = p.entry_price * (1 + (p.pnl_pct ?? 0) / 100);
    const distToSl = Math.abs(current - p.sl_price);
    const slProx = 1 - (distToSl / range);
    const pnlPct = p.pnl_pct ?? 0;
    if (slProx > 0.55 || pnlPct < -0.8) {
      items.push({
        kind: 'risk-position',
        priority: 100 - pnlPct * 10,
        pair: p.symbol,
        title: `${p.symbol} se acerca al stop`,
        body: `Posición ${p.direction === 'LONG' ? 'larga' : 'corta'} a ${pnlPct.toFixed(2)}% · falta ${((distToSl / current) * 100).toFixed(2)}% al stop`,
        action: 'Revisar posición',
        refId: p.id,
      });
    }
  }

  // 2. Fresh, high-score firing signals
  for (const s of symbols) {
    if (!s.señal) continue;
    const score = s.score ?? 0;
    if (score < 5) continue;
    if (!s.ts) continue;
    const ageMin = (now - tsMs(s.ts)) / 60_000;
    if (ageMin > freshWithinMin) continue;
    items.push({
      kind: 'fresh-signal',
      priority: 80 + score,
      pair: s.symbol,
      title: `${s.symbol} disparó setup`,
      body: `Score ${score}/9 · LRC ${(s.lrc_pct ?? 0).toFixed(1)}% · hace ${Math.round(ageMin)} min`,
      action: 'Abrir posición',
      refId: s.symbol,
    });
  }

  // 3. Position close to TP (positive risk)
  for (const p of positions) {
    if (p.status !== 'open') continue;
    if (p.sl_price == null || p.tp_price == null) continue;
    const range = Math.abs(p.tp_price - p.sl_price);
    if (range === 0) continue;
    const current = p.entry_price * (1 + (p.pnl_pct ?? 0) / 100);
    const distToTp = Math.abs(p.tp_price - current);
    const tpProx = 1 - (distToTp / range);
    if (tpProx > 0.7) {
      items.push({
        kind: 'near-tp',
        priority: 70,
        pair: p.symbol,
        title: `${p.symbol} cerca del objetivo`,
        body: `+${(p.pnl_pct ?? 0).toFixed(2)}% · falta ${((distToTp / current) * 100).toFixed(2)}% al TP`,
        action: 'Asegurar ganancia',
        refId: p.id,
      });
    }
  }

  // 4. System alerts
  if (scanner && scanner.errors > 0) {
    items.push({
      kind: 'error',
      priority: 90,
      title: `${scanner.errors} errores en el último ciclo`,
      body: 'El escáner reportó fallos. Posible problema de conexión o de API.',
      action: 'Ver detalles',
    });
  }

  return items.sort((a, b) => b.priority - a.priority).slice(0, 4);
}

// ────────────────────────────────────────────────────────────
// Watchlist bucketing
// ────────────────────────────────────────────────────────────

export interface SymbolBuckets {
  featured: SymbolStatus[]; // big cards, firing
  watching: SymbolStatus[]; // standard cards, score 2-4 (or score>=5 without trigger)
  quiet:    SymbolStatus[]; // compressed rows
}

/**
 * Three-tier bucketing of the watchlist by signal score + trigger state.
 * Sorted within each bucket by score desc, then by recency.
 */
export function bucketSymbols(symbols: SymbolStatus[]): SymbolBuckets {
  const sortByScoreRecency = (a: SymbolStatus, b: SymbolStatus): number => {
    const sa = a.score ?? 0;
    const sb = b.score ?? 0;
    if (sa !== sb) return sb - sa;
    const ta = a.ts ? tsMs(a.ts) : 0;
    const tb = b.ts ? tsMs(b.ts) : 0;
    return tb - ta;
  };

  const featured: SymbolStatus[] = [];
  const watching: SymbolStatus[] = [];
  const quiet:    SymbolStatus[] = [];

  for (const s of symbols) {
    const score = s.score ?? 0;
    if (score >= 5 && s.señal) featured.push(s);
    else if (score >= 2 || (score >= 5 && !s.señal)) watching.push(s);
    else quiet.push(s);
  }

  return {
    featured: featured.sort(sortByScoreRecency),
    watching: watching.sort(sortByScoreRecency),
    quiet:    quiet.sort(sortByScoreRecency),
  };
}

// ────────────────────────────────────────────────────────────
// Score component breakdown — used by ScoreGrid
// ────────────────────────────────────────────────────────────

/**
 * The scanner reports a numeric score (0-9 from the v6 backend) but doesn't
 * (yet) break down which of the constituent factors contributed. Until the
 * backend exposes that breakdown, we render the score as a filled-cells grid
 * proportional to the value. Each cell = one factor.
 *
 * When the backend starts returning a `score_components: number[]` per
 * symbol, swap this for the real array.
 */
export function fakeScoreComponents(score: number, max: number = 9): number[] {
  const clamped = Math.max(0, Math.min(max, Math.round(score)));
  return Array.from({ length: max }, (_, i) => (i < clamped ? 1 : 0));
}
