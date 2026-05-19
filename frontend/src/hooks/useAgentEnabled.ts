// ============================================================
// useAgentEnabled.ts
//
// Server-driven feature flag for the copilot UI. Polls GET /agent/status
// on mount and every POLL_INTERVAL_MS afterwards, returning the latest
// enabled-or-not boolean.
//
// Phase 0 of the production-grade copilot rewrite (epic #400). Replaces
// the previous compile-time `VITE_AGENT_ENABLED` env-var-only gate so the
// dashboard can react to operator-side disables without redeploying the
// frontend.
//
// Compile-time override is preserved as a dev convenience: setting
// VITE_AGENT_ENABLED=0/false/off in `frontend/.env.local` forces the
// agent OFF regardless of what the backend says (useful for local
// development without an ANTHROPIC_API_KEY).
//
// While the first poll is in flight the hook reports the server-driven
// state as `null`, but the public boolean is optimistic (`true`) so the
// UI does not flash "offline" on every refresh. The first poll resolves
// in under a second on a healthy backend.
// ============================================================

import { useEffect, useState } from 'react';
import { getAgentStatus } from '../api';

// Compile-time dev override. Anything other than '0' / 'false' / 'off'
// is treated as "respect the server".
const COMPILE_TIME_OFF: boolean = (() => {
  const flag = (import.meta.env.VITE_AGENT_ENABLED ?? '').toString().trim().toLowerCase();
  return flag === '0' || flag === 'false' || flag === 'off';
})();

const POLL_INTERVAL_MS = 60_000;

export function useAgentEnabled(): boolean {
  const [serverEnabled, setServerEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    if (COMPILE_TIME_OFF) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const status = await getAgentStatus();
        if (!cancelled) setServerEnabled(status.enabled);
      } catch {
        // Network error or backend down → treat as disabled. Better to
        // hide the dock than to show "ONLINE" while every turn 5xx's.
        if (!cancelled) setServerEnabled(false);
      }
    };

    void tick();
    const id = window.setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  if (COMPILE_TIME_OFF) return false;
  // Optimistic-on-load: while the first poll is in flight (`null`), we
  // report enabled. The first poll typically resolves in <1s; the dock
  // hides smoothly if the server reports off.
  return serverEnabled !== false;
}
