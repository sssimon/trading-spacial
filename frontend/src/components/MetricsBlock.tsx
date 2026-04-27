import React from 'react';
import type { DashboardSymbolMetrics } from '../types';

interface MetricsBlockProps {
  metrics: DashboardSymbolMetrics;
}

const fmtPct = (v: number): string => (v * 100).toFixed(0) + '%';
const fmtUsd = (v: number): string => {
  const sign = v >= 0 ? '+' : '−';
  return `${sign}$${Math.abs(v).toFixed(0)}`;
};

const MetricsBlock: React.FC<MetricsBlockProps> = ({ metrics }) => {
  return (
    <dl className="ks-metrics">
      <div className="ks-metric">
        <dt>WR</dt>
        <dd>{fmtPct(metrics.win_rate_20_trades)}</dd>
      </div>
      <div className="ks-metric">
        <dt>pnl 30d</dt>
        <dd className={metrics.pnl_30d >= 0 ? 'ks-pos' : 'ks-neg'}>
          {fmtUsd(metrics.pnl_30d)}
        </dd>
      </div>
      <div className="ks-metric">
        <dt>trades</dt>
        <dd>{metrics.trades_count_total}</dd>
      </div>
      {metrics.months_negative_consecutive > 0 && (
        <div className="ks-metric">
          <dt>meses neg</dt>
          <dd>{metrics.months_negative_consecutive}</dd>
        </div>
      )}
    </dl>
  );
};

export default MetricsBlock;
