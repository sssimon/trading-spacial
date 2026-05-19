// ============================================================
// agent/surfaces.ts — single source of truth for the 5 copilot
// surface identifiers. Phase 4 of epic #400.
//
// Each mount of <useAgentStream/> picks one surface; the backend
// (api/agent/router.py + api/agent/models.py) uses that string to
// pick the right model + per-surface system-prompt block + tool
// subset. Adding a new surface here REQUIRES corresponding entries
// in:
//   - api/agent/models.py           (SURFACE_MODEL_DEFAULTS)
//   - api/agent/prompts/surfaces.py (SURFACE_PROMPTS)
//   - api/agent/tools/registry.py   (TOOL_CATALOG[*].surfaces)
//
// Keeping the surface strings as named constants (not stringly-
// typed literals scattered across the codebase) means grep finds
// every mount in one shot, and a typo in one mount fails at
// compile time instead of at runtime when the backend rejects
// the unknown surface.
// ============================================================

import type { AgentSurface } from './types';

// ── Named constants ────────────────────────────────────────────────

export const SURFACE_DOCK:          AgentSurface = 'dock';
export const SURFACE_SYMBOL_DETAIL: AgentSurface = 'symbol_detail';
export const SURFACE_KILL_SWITCH:   AgentSurface = 'kill_switch';
export const SURFACE_AUTOTUNE:      AgentSurface = 'autotune';
export const SURFACE_HISTORIAL:     AgentSurface = 'historial';

/**
 * Frozen enumeration of every surface. Use this when you need to iterate
 * (e.g. preloading per-surface assets, telemetry coverage report). The
 * order matters for snapshot stability — keep it the same as
 * api/agent/router.py's _AgentTurnRequest Literal.
 */
export const ALL_SURFACES: readonly AgentSurface[] = Object.freeze([
  SURFACE_DOCK,
  SURFACE_SYMBOL_DETAIL,
  SURFACE_KILL_SWITCH,
  SURFACE_AUTOTUNE,
  SURFACE_HISTORIAL,
]);

// ── Per-surface UI metadata ────────────────────────────────────────

/**
 * Human-facing label per surface, shown in debug panels and (eventually)
 * the conversation header so the user can see which copilot mode they're
 * talking to. Venezuelan-neutral Spanish.
 */
export const SURFACE_LABELS: Readonly<Record<AgentSurface, string>> = Object.freeze({
  dock:          'Copiloto general',
  symbol_detail: 'Copiloto del par',
  kill_switch:   'Copiloto del kill-switch',
  autotune:      'Copiloto del auto-tune',
  historial:     'Copiloto del historial',
});

/**
 * Which UI mount uses each surface. Informational only — keeps a single
 * place to consult when adding a new surface (or when removing one,
 * to know which mount to clean up).
 *
 * NOTE: as of Phase 4, only `dock` (AgentDock) and `symbol_detail`
 * (SymbolDetail) have live mounts. The other three are declared on the
 * backend (model + prompt + tools) but unmounted on the frontend — a
 * future epic will add KillSwitchView / AutoTuneView / HistorialView
 * copilot panels.
 */
export const SURFACE_MOUNTS: Readonly<Record<AgentSurface, string | null>> = Object.freeze({
  dock:          'components/AgentDock',
  symbol_detail: 'components/SymbolDetail',
  kill_switch:   null,
  autotune:      null,
  historial:     null,
});
