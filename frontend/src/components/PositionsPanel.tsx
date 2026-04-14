// ============================================================
// PositionsPanel.tsx — Gestión de posiciones con P&L en vivo
// ============================================================

import React, { useState, useCallback, useEffect } from 'react';
import type { Position, SymbolStatus, Signal } from '../types';
import { getPositions, closePosition, cancelPosition } from '../api';
import { timeAgo, formatPrice } from '../utils';
import OpenPositionModal from './OpenPositionModal';

interface PositionsPanelProps {
  symbols:    SymbolStatus[];    // para precio en tiempo real
  onOpenFromSignal?: Signal | null;
  onSignalConsumed?: () => void;
}

// ── Helpers ───────────────────────────────────────────────────

function priceMap(symbols: SymbolStatus[]): Record<string, number> {
  const map: Record<string, number> = {};
  for (const s of symbols) {
    if (s.price != null) map[s.symbol] = s.price;
  }
  return map;
}

function calcUnrealizedPnl(pos: Position, currentPrice: number | null) {
  if (!currentPrice || !pos.qty) return { pnl_usd: null, pnl_pct: null };
  const pnl_usd = pos.direction === 'LONG'
    ? (currentPrice - pos.entry_price) * pos.qty
    : (pos.entry_price - currentPrice) * pos.qty;
  const pnl_pct = pos.direction === 'LONG'
    ? ((currentPrice - pos.entry_price) / pos.entry_price) * 100
    : ((pos.entry_price - currentPrice) / pos.entry_price) * 100;
  return { pnl_usd, pnl_pct };
}

function tpSlProgress(pos: Position, currentPrice: number | null) {
  if (!currentPrice || !pos.sl_price || !pos.tp_price) return null;
  const range = pos.direction === 'LONG'
    ? pos.tp_price - pos.sl_price
    : pos.sl_price - pos.tp_price;
  if (range <= 0) return null;
  const progress = pos.direction === 'LONG'
    ? (currentPrice - pos.sl_price) / range
    : (pos.sl_price - currentPrice) / range;
  return Math.max(0, Math.min(1, progress));
}

/** Format price with $ prefix, or '—' for null. */
function fmtPrice(p: number | null): string {
  if (p == null) return '—';
  return `$${formatPrice(p)}`;
}

function fmtPnl(usd: number | null, pct: number | null) {
  if (usd == null || pct == null) return { text: '—', cls: '' };
  const sign = usd >= 0 ? '+' : '';
  return {
    text: `${sign}$${usd.toFixed(2)} (${sign}${pct.toFixed(2)}%)`,
    cls:  usd >= 0 ? 'pnl--pos' : 'pnl--neg',
  };
}

const EXIT_REASON_LABEL: Record<string, string> = {
  TP_HIT:  'Take Profit',
  SL_HIT:  'Stop Loss',
  MANUAL:  'Manual',
  EXPIRED: 'Expirada',
};

// ── Close confirmation inline ──────────────────────────────────

interface CloseRowProps {
  pos:        Position;
  currentPrice: number | null;
  onConfirm:  (exitPrice: number) => void;
  onCancel:   () => void;
}

const CloseRow: React.FC<CloseRowProps> = ({ pos, currentPrice, onConfirm, onCancel }) => {
  const [exitPrice, setExitPrice] = useState(String(currentPrice ?? pos.entry_price));
  return (
    <div className="pos-close-inline">
      <span className="pos-close-label">Cerrar a:</span>
      <div className="pos-input-wrap pos-input-wrap--sm">
        <span className="pos-input-prefix">$</span>
        <input
          type="number" step="any"
          className="pos-input pos-input--sm"
          value={exitPrice}
          onChange={e => setExitPrice(e.target.value)}
        />
      </div>
      <button
        className="btn btn-sm btn-danger"
        onClick={() => {
          const price = parseFloat(exitPrice);
          if (isNaN(price) || price <= 0) { alert('Ingresa un precio de salida válido'); return; }
          onConfirm(price);
        }}
      >Confirmar</button>
      <button className="btn btn-sm btn-secondary" onClick={onCancel}>Cancelar</button>
    </div>
  );
};

// ── Main component ─────────────────────────────────────────────

const PositionsPanel: React.FC<PositionsPanelProps> = ({
  symbols,
  onOpenFromSignal,
  onSignalConsumed,
}) => {
  const [positions,   setPositions]   = useState<Position[]>([]);
  const [loading,     setLoading]     = useState(true);
  const [tab,         setTab]         = useState<'open' | 'closed'>('open');
  const [openModal,   setOpenModal]   = useState(false);
  const [closingId,   setClosingId]   = useState<number | null>(null);
  type PrefillData = { symbol: string; price?: number | null; sl?: number | null; tp?: number | null; scan_id?: number | null };
  const [prefill,     setPrefill]     = useState<PrefillData | undefined>();

  const prices = priceMap(symbols);

  const load = useCallback(async () => {
    try {
      const res = await getPositions('all');
      setPositions(res.positions);
    } catch (e) {
      console.error('getPositions error:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 30s
  useEffect(() => {
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  // If a signal was passed from outside, pre-fill the modal
  useEffect(() => {
    if (onOpenFromSignal) {
      setPrefill({
        symbol:  onOpenFromSignal.symbol,
        price:   onOpenFromSignal.price,
        scan_id: onOpenFromSignal.id,
      });
      setOpenModal(true);
      onSignalConsumed?.();
    }
  }, [onOpenFromSignal, onSignalConsumed]);

  const handleClose = async (id: number, exitPrice: number) => {
    try {
      await closePosition(id, { exit_price: exitPrice, exit_reason: 'MANUAL' });
      setClosingId(null);
      await load();
    } catch (e) {
      console.error(e);
    }
  };

  const handleCancel = async (id: number) => {
    if (!confirm('¿Cancelar esta posición? Se marcará como cancelada.')) return;
    try {
      await cancelPosition(id);
      await load();
    } catch (e) { console.error(e); }
  };

  // ── Computed stats ──────────────────────────────────────────
  const openPos   = positions.filter(p => p.status === 'open');
  const closedPos = positions.filter(p => p.status === 'closed');

  const unrealizedTotal = openPos.reduce((acc, p) => {
    const { pnl_usd } = calcUnrealizedPnl(p, prices[p.symbol] ?? null);
    return acc + (pnl_usd ?? 0);
  }, 0);

  const realizedTotal = closedPos.reduce((acc, p) => acc + (p.pnl_usd ?? 0), 0);
  const wins     = closedPos.filter(p => (p.pnl_usd ?? 0) > 0).length;
  const winRate  = closedPos.length > 0 ? (wins / closedPos.length) * 100 : null;

  // ── Render ──────────────────────────────────────────────────
  return (
    <section className="positions-section">

      {/* ── Stats bar ──────────────────────────────────────── */}
      <div className="positions-stats">
        <div className="pos-stat">
          <span className="pos-stat-label">Abiertas</span>
          <span className="pos-stat-val">{openPos.length}</span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">P&L Abierto</span>
          <span className={`pos-stat-val ${unrealizedTotal >= 0 ? 'pnl--pos' : 'pnl--neg'}`}>
            {unrealizedTotal >= 0 ? '+' : ''}${unrealizedTotal.toFixed(2)}
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">P&L Realizado</span>
          <span className={`pos-stat-val ${realizedTotal >= 0 ? 'pnl--pos' : 'pnl--neg'}`}>
            {realizedTotal >= 0 ? '+' : ''}${realizedTotal.toFixed(2)}
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Win Rate</span>
          <span className={`pos-stat-val ${winRate != null && winRate >= 50 ? 'pnl--pos' : winRate != null ? 'pnl--neg' : ''}`}>
            {winRate != null ? `${winRate.toFixed(0)}%` : '—'}
          </span>
        </div>
        <div className="pos-stat">
          <span className="pos-stat-label">Cerradas</span>
          <span className="pos-stat-val">{closedPos.length}</span>
        </div>

        <button
          className="btn btn-primary pos-open-btn"
          onClick={() => { setPrefill(undefined); setOpenModal(true); }}
        >
          + Abrir posición
        </button>
      </div>

      {/* ── Tabs ───────────────────────────────────────────── */}
      <div className="section-header">
        <h2 className="section-title">Posiciones</h2>
        <div className="filter-tabs">
          <button
            className={`filter-tab${tab === 'open' ? ' filter-tab--active' : ''}`}
            onClick={() => setTab('open')}
          >
            Abiertas
            <span className={`tab-count${openPos.length > 0 ? ' tab-count--active' : ''}`}>
              {openPos.length}
            </span>
          </button>
          <button
            className={`filter-tab${tab === 'closed' ? ' filter-tab--active' : ''}`}
            onClick={() => setTab('closed')}
          >
            Historial
            <span className="tab-count">{closedPos.length}</span>
          </button>
        </div>
      </div>

      {/* ── Open positions table ────────────────────────────── */}
      {tab === 'open' && (
        <div className="pos-table-wrap">
          {loading ? (
            <div className="pos-empty">Cargando…</div>
          ) : openPos.length === 0 ? (
            <div className="pos-empty">
              <div className="empty-icon">📊</div>
              <div className="empty-title">Sin posiciones abiertas</div>
              <div className="empty-subtitle">Abre una posición cuando el scanner detecte una señal.</div>
            </div>
          ) : (
            <table className="pos-table">
              <thead>
                <tr>
                  <th>Par</th>
                  <th>Dir</th>
                  <th>Entrada</th>
                  <th>Actual</th>
                  <th>P&L</th>
                  <th>SL</th>
                  <th>TP</th>
                  <th>Progreso</th>
                  <th>Tiempo</th>
                  <th>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {openPos.map(pos => {
                  const cp = prices[pos.symbol] ?? null;
                  const { pnl_usd, pnl_pct } = calcUnrealizedPnl(pos, cp);
                  const pnl = fmtPnl(pnl_usd, pnl_pct);
                  const prog = tpSlProgress(pos, cp);
                  return (
                    <React.Fragment key={pos.id}>
                      <tr className="pos-row">
                        <td>
                          <span className="pos-symbol">{pos.symbol.replace('USDT', '')}</span>
                          <span className="pos-sym-quote">/USDT</span>
                        </td>
                        <td>
                          <span className={`pos-dir-badge pos-dir-badge--${pos.direction.toLowerCase()}`}>
                            {pos.direction}
                          </span>
                        </td>
                        <td className="pos-mono">{fmtPrice(pos.entry_price)}</td>
                        <td className="pos-mono pos-current">{fmtPrice(cp)}</td>
                        <td className={`pos-mono ${pnl.cls}`}>{pnl.text}</td>
                        <td className="pos-mono pos-sl">{fmtPrice(pos.sl_price)}</td>
                        <td className="pos-mono pos-tp">{fmtPrice(pos.tp_price)}</td>
                        <td>
                          {prog != null ? (
                            <div className="pos-progress-track" title={`${(prog * 100).toFixed(0)}% hacia TP`}>
                              <div
                                className="pos-progress-fill"
                                style={{
                                  width: `${prog * 100}%`,
                                  background: prog > 0.7 ? '#22c55e' : prog > 0.4 ? '#f59e0b' : '#ef4444',
                                }}
                              />
                            </div>
                          ) : <span className="pos-mono" style={{ color: 'var(--text-muted)' }}>—</span>}
                        </td>
                        <td className="pos-time">{timeAgo(pos.entry_ts)}</td>
                        <td>
                          <div className="pos-actions">
                            <button
                              className="btn btn-sm btn-danger"
                              onClick={() => setClosingId(closingId === pos.id ? null : pos.id)}
                            >
                              Cerrar
                            </button>
                            <button
                              className="btn btn-sm btn-ghost"
                              onClick={() => handleCancel(pos.id)}
                              title="Cancelar posición"
                            >
                              ✕
                            </button>
                          </div>
                        </td>
                      </tr>
                      {closingId === pos.id && (
                        <tr className="pos-close-row">
                          <td colSpan={10}>
                            <CloseRow
                              pos={pos}
                              currentPrice={cp}
                              onConfirm={price => handleClose(pos.id, price)}
                              onCancel={() => setClosingId(null)}
                            />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── Closed positions table ───────────────────────────── */}
      {tab === 'closed' && (
        <div className="pos-table-wrap">
          {loading ? (
            <div className="pos-empty">Cargando…</div>
          ) : closedPos.length === 0 ? (
            <div className="pos-empty">
              <div className="empty-icon">📋</div>
              <div className="empty-title">Sin historial</div>
              <div className="empty-subtitle">Las posiciones cerradas aparecerán aquí.</div>
            </div>
          ) : (
            <table className="pos-table">
              <thead>
                <tr>
                  <th>Par</th>
                  <th>Dir</th>
                  <th>Entrada</th>
                  <th>Salida</th>
                  <th>P&L</th>
                  <th>Razón</th>
                  <th>Capital</th>
                  <th>Cerrada</th>
                </tr>
              </thead>
              <tbody>
                {closedPos.map(pos => {
                  const pnl = fmtPnl(pos.pnl_usd, pos.pnl_pct);
                  const reason = pos.exit_reason ?? 'MANUAL';
                  return (
                    <tr key={pos.id} className={`pos-row pos-row--closed ${(pos.pnl_usd ?? 0) >= 0 ? 'pos-row--win' : 'pos-row--loss'}`}>
                      <td>
                        <span className="pos-symbol">{pos.symbol.replace('USDT', '')}</span>
                        <span className="pos-sym-quote">/USDT</span>
                      </td>
                      <td>
                        <span className={`pos-dir-badge pos-dir-badge--${pos.direction.toLowerCase()}`}>
                          {pos.direction}
                        </span>
                      </td>
                      <td className="pos-mono">{fmtPrice(pos.entry_price)}</td>
                      <td className="pos-mono">{fmtPrice(pos.exit_price)}</td>
                      <td className={`pos-mono ${pnl.cls}`}>{pnl.text}</td>
                      <td>
                        <span className={`pos-reason pos-reason--${reason.toLowerCase().replace('_', '-')}`}>
                          {EXIT_REASON_LABEL[reason] ?? reason}
                        </span>
                      </td>
                      <td className="pos-mono">
                        {pos.size_usd ? `$${pos.size_usd.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—'}
                      </td>
                      <td className="pos-time">{pos.exit_ts ? timeAgo(pos.exit_ts) : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── Open position modal ─────────────────────────────── */}
      {openModal && (
        <OpenPositionModal
          symbols={symbols}
          prefill={prefill}
          onClose={() => setOpenModal(false)}
          onCreated={load}
        />
      )}
    </section>
  );
};

export default PositionsPanel;
