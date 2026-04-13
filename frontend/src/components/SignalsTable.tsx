// ============================================================
// SignalsTable.tsx — Recent signals table (max 20 rows)
// ============================================================

import React from 'react';
import type { Signal } from '../types';

interface SignalsTableProps {
  signals: Signal[];
  loading: boolean;
  onOpenPosition?: (signal: Signal) => void;
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

function formatDatetime(ts: string): string {
  if (!ts) return '';
  return new Date(ts).toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
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

function getScoreClass(score: number): string {
  if (score >= 8) return 'score-high';
  if (score >= 6) return 'score-mid';
  if (score >= 4) return 'score-low';
  return 'score-vlow';
}

const SignalsTable: React.FC<SignalsTableProps> = ({ signals, loading, onOpenPosition }) => {
  const rows = signals.slice(0, 20);

  return (
    <section className="signals-section">
      <div className="section-header">
        <h2 className="section-title">
          Señales recientes
          <span className="section-badge">{signals.length}</span>
        </h2>
      </div>

      {loading ? (
        <div className="table-loading">
          <div className="spinner-large" />
          <span>Cargando señales…</span>
        </div>
      ) : rows.length === 0 ? (
        <div className="empty-state empty-state--table">
          <div className="empty-icon">📡</div>
          <div className="empty-title">Sin señales recientes</div>
          <div className="empty-subtitle">
            El scanner está monitoreando 20 pares
          </div>
        </div>
      ) : (
        <div className="table-wrapper">
          <table className="signals-table">
            <thead>
              <tr>
                <th className="col-num">#</th>
                <th className="col-time">Hora</th>
                <th className="col-pair">Par</th>
                <th className="col-price">Precio</th>
                <th className="col-lrc">LRC%</th>
                <th className="col-score">Score</th>
                <th className="col-estado">Estado</th>
                <th className="col-gatillo">Gatillo</th>
                {onOpenPosition && <th className="col-action"></th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((sig, idx) => (
                <tr
                  key={sig.id}
                  className={`table-row${sig.señal ? ' row-signal' : sig.setup ? ' row-setup' : ''}`}
                >
                  <td className="col-num">{idx + 1}</td>
                  <td className="col-time">
                    <span title={formatDatetime(sig.ts)}>
                      {timeAgo(sig.ts)}
                    </span>
                  </td>
                  <td className="col-pair">
                    <span className="pair-text">{sig.symbol}</span>
                  </td>
                  <td className="col-price">
                    <span className="price-text">
                      {sig.price != null ? `$${formatPrice(sig.price)}` : '—'}
                    </span>
                  </td>
                  <td className="col-lrc">
                    <span className={`lrc-text ${(sig.lrc_pct ?? 100) <= 25 ? 'lrc-good' : 'lrc-bad'}`}>
                      {sig.lrc_pct != null ? `${sig.lrc_pct.toFixed(1)}%` : '—'}
                    </span>
                  </td>
                  <td className="col-score">
                    <span className={`score-pill ${getScoreClass(sig.score ?? 0)}`}>
                      {sig.score != null ? sig.score.toFixed(1) : '—'}
                    </span>
                  </td>
                  <td className="col-estado">
                    <span className="estado-text">{sig.estado}</span>
                  </td>
                  <td className="col-gatillo">
                    <span className={`gatillo-dot ${sig.gatillo ? 'gatillo-ok' : 'gatillo-no'}`}>
                      {sig.gatillo ? '✓' : '✗'}
                    </span>
                  </td>
                  {onOpenPosition && (
                    <td className="col-action">
                      {sig.señal && (
                        <button
                          className="btn btn-sm btn-signal-open"
                          onClick={() => onOpenPosition(sig)}
                          title="Abrir posición desde esta señal"
                        >
                          + Posición
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
};

export default SignalsTable;
