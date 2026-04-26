import React from 'react';
import Sparkline from './Sparkline';
import MetricsBlock from './MetricsBlock';
import type { DashboardSymbolState, KillSwitchPerSymbolTier } from '../types';

interface KillSwitchSymbolCardProps {
  state: DashboardSymbolState;
}

const TIER_CLASS: Record<KillSwitchPerSymbolTier, string> = {
  NORMAL: 'ks-tier-normal',
  ALERT: 'ks-tier-alert',
  REDUCED: 'ks-tier-reduced',
  PAUSED: 'ks-tier-paused',
  PROBATION: 'ks-tier-probation',
};

function formatRelativeTime(iso: string): string {
  const ts = new Date(iso);
  const diffMs = Date.now() - ts.getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  const hours = Math.floor(diffMs / (1000 * 60 * 60));
  if (days > 0) return `hace ${days}d`;
  if (hours > 0) return `hace ${hours}h`;
  const mins = Math.floor(diffMs / (1000 * 60));
  return `hace ${Math.max(1, mins)}m`;
}

const KillSwitchSymbolCard: React.FC<KillSwitchSymbolCardProps> = ({ state }) => {
  const tierClass = TIER_CLASS[state.state] ?? 'ks-tier-normal';

  return (
    <article
      className="ks-symbol-card-v2"
      aria-labelledby={`ks-card-${state.symbol}`}
    >
      <header className="ks-card-header">
        <h3 id={`ks-card-${state.symbol}`} className="ks-card-symbol">
          {state.symbol}
        </h3>
        <span
          className={`ks-tier-badge ${tierClass}`}
          aria-label={`Tier: ${state.state}`}
        >
          {state.state}
        </span>
      </header>

      <Sparkline outcomes={state.sparkline_20} />

      <MetricsBlock metrics={state.metrics} />

      {state.last_transition && (
        <div className="ks-last-transition">
          <span className="ks-transition-arrow">←</span>
          {state.last_transition.from_state}
          <span className="ks-transition-reason">
            {' '}{state.last_transition.reason}
          </span>
          <span className="ks-transition-ts">
            {' · '}{formatRelativeTime(state.last_transition.ts)}
          </span>
        </div>
      )}

      <p className="ks-next-conditions">{state.next_conditions}</p>
    </article>
  );
};

export default KillSwitchSymbolCard;
