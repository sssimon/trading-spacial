import React from 'react';
import type { DashboardPortfolioState, KillSwitchPortfolioTier } from '../types';

interface PortfolioPanelProps {
  portfolio: DashboardPortfolioState;
}

const TIER_CLASS: Record<KillSwitchPortfolioTier, string> = {
  NORMAL: 'ks-tier-normal',
  WARNED: 'ks-tier-alert',
  REDUCED: 'ks-tier-reduced',
  FROZEN: 'ks-tier-paused',
};

const PortfolioPanel: React.FC<PortfolioPanelProps> = ({ portfolio }) => {
  const tierClass = TIER_CLASS[portfolio.tier] ?? 'ks-tier-normal';
  const ddPctText = (portfolio.dd_pct * 100).toFixed(1) + '%';

  return (
    <section className="ks-portfolio-panel" aria-label="Portfolio aggregate">
      <div className="ks-portfolio-tier-card">
        <span className={`ks-tier-badge ks-tier-large ${tierClass}`}>
          {portfolio.tier}
        </span>
        <dl className="ks-portfolio-metrics">
          <div className="ks-metric">
            <dt>DD</dt><dd>{ddPctText}</dd>
          </div>
          <div className="ks-metric">
            <dt>peak</dt><dd>${portfolio.peak_equity.toFixed(0)}</dd>
          </div>
          <div className="ks-metric">
            <dt>failures</dt><dd>{portfolio.concurrent_failures}</dd>
          </div>
        </dl>
      </div>

      <div className="ks-portfolio-transitions">
        <h4 className="ks-transitions-title">Transiciones recientes</h4>
        {portfolio.recent_transitions.length === 0 ? (
          <p className="ks-empty-text">— sin transiciones —</p>
        ) : (
          <ul className="ks-transitions-list">
            {portfolio.recent_transitions.map((t, i) => (
              <li key={i} className="ks-transition-row">
                <span className="ks-transition-flow">
                  {t.to_tier} ← {t.from_tier}
                </span>
                <span className="ks-transition-reason"> · {t.reason}</span>
                <span className="ks-transition-ts">
                  {' · '}{new Date(t.ts).toLocaleString('es-ES')}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
};

export default PortfolioPanel;
