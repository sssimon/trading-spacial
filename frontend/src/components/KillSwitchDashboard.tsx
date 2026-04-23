// ============================================================
// KillSwitchDashboard.tsx — Phase 1 MVP of kill switch v2 (#187)
// Shows per-symbol tier grid + portfolio aggregate state.
// Polls /kill_switch/current_state every 30s.
// ============================================================

import React, { useEffect, useState } from 'react';
import { getKillSwitchCurrentState } from '../api';
import type {
  KillSwitchCurrentStateResponse,
  KillSwitchPerSymbolTier,
  KillSwitchPortfolioTier,
} from '../types';

const POLL_INTERVAL_MS = 30_000;

const TIER_COLORS_PER_SYMBOL: Record<KillSwitchPerSymbolTier, string> = {
  NORMAL: '#22c55e',
  ALERT: '#f59e0b',
  REDUCED: '#fb923c',
  PAUSED: '#ef4444',
  PROBATION: '#a78bfa',
};

const TIER_COLORS_PORTFOLIO: Record<KillSwitchPortfolioTier, string> = {
  NORMAL: '#22c55e',
  WARNED: '#f59e0b',
  REDUCED: '#fb923c',
  FROZEN: '#ef4444',
};

const KillSwitchDashboard: React.FC = () => {
  const [state, setState] = useState<KillSwitchCurrentStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const fetchState = async () => {
      try {
        const resp = await getKillSwitchCurrentState('v1');
        if (!alive) return;
        setState(resp);
        setError(null);
      } catch (err) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : 'Error');
      }
    };
    fetchState();
    const id = setInterval(fetchState, POLL_INTERVAL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const symbols = state ? Object.values(state.symbols) : [];
  const portfolio = state?.portfolio ?? { tier: 'NORMAL' as const, concurrent_failures: 0 };

  return (
    <div className="ks-dashboard">
      {error && (
        <div className="ks-error">Error cargando kill switch: {error}</div>
      )}

      <div className="ks-portfolio-card">
        <div className="ks-portfolio-label">Portfolio</div>
        <div
          className="ks-portfolio-tier"
          style={{ color: TIER_COLORS_PORTFOLIO[portfolio.tier] }}
        >
          {portfolio.tier}
        </div>
        <div className="ks-portfolio-meta">
          {portfolio.concurrent_failures} símbolo(s) en ALERT/REDUCED/PAUSED
        </div>
      </div>

      <div className="ks-symbol-grid">
        {symbols.map((s) => (
          <div key={s.symbol} className="ks-symbol-card">
            <div className="ks-symbol-name">{s.symbol}</div>
            <div
              className="ks-symbol-tier"
              style={{ color: TIER_COLORS_PER_SYMBOL[s.per_symbol_tier] }}
            >
              {s.per_symbol_tier}
            </div>
            <div className="ks-symbol-meta">
              size × {s.size_factor.toFixed(2)} · {s.skip ? 'skip' : 'operating'}
            </div>
            <div className="ks-symbol-ts">
              {new Date(s.ts).toLocaleString('es-ES')}
            </div>
          </div>
        ))}
        {symbols.length === 0 && (
          <div className="ks-empty">Sin datos aún — esperando scans.</div>
        )}
      </div>
    </div>
  );
};

export default KillSwitchDashboard;
