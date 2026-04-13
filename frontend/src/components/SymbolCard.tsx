// ============================================================
// SymbolCard.tsx — Individual symbol card with price, LRC%, score
// ============================================================

import React from 'react';
import type { SymbolStatus } from '../types';

interface SymbolCardProps {
  symbol: SymbolStatus;
  onClick?: () => void;
}

function formatPrice(price: number): string {
  if (price >= 1000) {
    return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (price >= 1) {
    return price.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 4 });
  }
  return price.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 6 });
}

function splitSymbol(sym: string): { base: string; quote: string } {
  // Common quote currencies
  const quotes = ['USDT', 'USDC', 'BTC', 'ETH', 'BNB', 'BUSD'];
  for (const q of quotes) {
    if (sym.endsWith(q)) {
      return { base: sym.slice(0, -q.length), quote: q };
    }
  }
  // Fallback: split at 3 chars from end
  return { base: sym.slice(0, -4), quote: sym.slice(-4) };
}

function timeAgo(ts: string): string {
  const now = Date.now();
  const then = new Date(ts).getTime();
  const diffMs = now - then;

  if (isNaN(diffMs)) return '—';

  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `hace ${diffSec}s`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `hace ${diffMin}m`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `hace ${diffHour}h`;
  const diffDay = Math.floor(diffHour / 24);
  return `hace ${diffDay}d`;
}

function getScoreColor(score: number): string {
  if (score >= 8) return '#22c55e';
  if (score >= 6) return '#86efac';
  if (score >= 4) return '#f59e0b';
  if (score >= 2) return '#fb923c';
  return '#ef4444';
}

function getLrcColor(lrcPct: number): string {
  // lrc_pct: distance from LRC — low means price is near bottom (bullish)
  return lrcPct <= 25 ? '#22c55e' : '#ef4444';
}

const SymbolCard: React.FC<SymbolCardProps> = ({ symbol, onClick }) => {
  const { base, quote } = splitSymbol(symbol.symbol);
  const isSenal = symbol.señal === true;
  const isSetup = !isSenal && symbol.gatillo === true;

  const lrc    = symbol.lrc_pct ?? 0;
  const score  = symbol.score   ?? 0;
  const price  = symbol.price   ?? 0;

  // Clamp lrc_pct to 0–100 for bar display
  const lrcBarPct   = Math.min(100, Math.max(0, lrc));
  // Score 0–10 for bar
  const scoreBarPct = Math.min(100, Math.max(0, (score / 10) * 100));

  let cardClass = 'symbol-card';
  if (isSenal) cardClass += ' symbol-card--signal';
  else if (isSetup) cardClass += ' symbol-card--setup';

  let badgeClass = 'card-badge';
  let badgeText = '—';
  if (isSenal) {
    badgeClass += ' card-badge--signal';
    badgeText = 'SEÑAL';
  } else if (isSetup) {
    badgeClass += ' card-badge--setup';
    badgeText = 'SETUP';
  }

  const tsFormatted = symbol.ts
    ? new Date(symbol.ts).toLocaleString('es-ES')
    : '';

  return (
    <div className={cardClass} onClick={onClick} title="Ver gráfico" style={{ cursor: 'pointer' }}>
      {/* Top row: symbol + badge */}
      <div className="card-header">
        <div className="card-symbol">
          <span className="card-symbol-base">{base}</span>
          <span className="card-symbol-quote">/{quote}</span>
        </div>
        <div className="card-header-right">
          <span className="card-chart-icon">↗</span>
          <span className={badgeClass}>
            {badgeText}
            {isSenal && <span className="badge-dot badge-dot--green" />}
            {isSetup && <span className="badge-dot badge-dot--amber" />}
          </span>
        </div>
      </div>

      {/* Price */}
      <div className="card-price">
        <span className="price-currency">$</span>
        <span className="price-value">
          {symbol.price != null ? formatPrice(price) : '—'}
        </span>
      </div>

      {/* LRC% bar */}
      <div className="card-metric">
        <div className="metric-row">
          <span className="metric-name">LRC%</span>
          <span className="metric-val" style={{ color: getLrcColor(lrc) }}>
            {symbol.lrc_pct != null ? `${lrc.toFixed(1)}%` : '—'}
          </span>
        </div>
        <div className="bar-track">
          <div
            className="bar-fill"
            style={{ width: `${lrcBarPct}%`, backgroundColor: getLrcColor(lrc) }}
          />
        </div>
      </div>

      {/* Score bar */}
      <div className="card-metric">
        <div className="metric-row">
          <span className="metric-name">Score</span>
          <span className="metric-val" style={{ color: getScoreColor(score) }}>
            {symbol.score != null ? score.toFixed(1) : '—'}
          </span>
        </div>
        <div className="bar-track">
          <div
            className="bar-fill"
            style={{ width: `${scoreBarPct}%`, backgroundColor: getScoreColor(score) }}
          />
        </div>
      </div>

      {/* Footer: macro + time */}
      <div className="card-footer">
        <span className={`macro-badge ${symbol.gatillo ? 'macro-badge--ok' : 'macro-badge--ko'}`}>
          Macro {symbol.gatillo ? '✓' : '✗'}
        </span>
        <span className="card-time" title={tsFormatted}>
          {symbol.ts ? timeAgo(symbol.ts) : '—'}
        </span>
      </div>
    </div>
  );
};

export default SymbolCard;
