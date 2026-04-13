// ============================================================
// OpenPositionModal.tsx — Formulario para abrir una posición
// ============================================================

import React, { useState, useEffect } from 'react';
import type { SymbolStatus, PositionCreatePayload } from '../types';
import { openPosition } from '../api';

interface OpenPositionModalProps {
  symbols:   SymbolStatus[];
  prefill?:  { symbol: string; price?: number | null; sl?: number | null; tp?: number | null; scan_id?: number | null };
  onClose:   () => void;
  onCreated: () => void;
}

const OpenPositionModal: React.FC<OpenPositionModalProps> = ({
  symbols, prefill, onClose, onCreated,
}) => {
  const [symbol,     setSymbol]     = useState(prefill?.symbol   ?? 'BTCUSDT');
  const [direction,  setDirection]  = useState<'LONG' | 'SHORT'>('LONG');
  const [entryPrice, setEntryPrice] = useState(String(prefill?.price ?? ''));
  const [slPrice,    setSlPrice]    = useState(String(prefill?.sl    ?? ''));
  const [tpPrice,    setTpPrice]    = useState(String(prefill?.tp    ?? ''));
  const [sizeUsd,    setSizeUsd]    = useState('');
  const [notes,      setNotes]      = useState('');
  const [saving,     setSaving]     = useState(false);
  const [error,      setError]      = useState<string | null>(null);

  // When symbol changes, fill entry price from current price
  useEffect(() => {
    const sym = symbols.find(s => s.symbol === symbol);
    if (sym?.price && !prefill?.price) setEntryPrice(String(sym.price));
  }, [symbol, symbols, prefill]);

  // Derived calculations
  const entry = parseFloat(entryPrice) || 0;
  const sl    = parseFloat(slPrice)    || 0;
  const tp    = parseFloat(tpPrice)    || 0;
  const size  = parseFloat(sizeUsd)    || 0;
  const qty   = entry > 0 && size > 0 ? size / entry : 0;

  const slPct = entry > 0 && sl > 0
    ? direction === 'LONG'
      ? ((sl - entry) / entry) * 100
      : ((entry - sl) / entry) * 100
    : null;
  const tpPct = entry > 0 && tp > 0
    ? direction === 'LONG'
      ? ((tp - entry) / entry) * 100
      : ((entry - tp) / entry) * 100
    : null;
  const rr = slPct && tpPct && Math.abs(slPct) > 0
    ? Math.abs(tpPct) / Math.abs(slPct)
    : null;
  const maxRisk = size > 0 && slPct != null ? Math.abs(slPct / 100) * size : null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!entry) { setError('El precio de entrada es requerido'); return; }
    setSaving(true);
    setError(null);
    try {
      const payload: PositionCreatePayload = {
        symbol,
        direction,
        entry_price: entry,
        sl_price:   sl || null,
        tp_price:   tp || null,
        size_usd:   size || null,
        scan_id:    prefill?.scan_id ?? null,
        notes,
      };
      await openPosition(payload);
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error al abrir posición');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="chart-overlay" onClick={onClose} aria-hidden="true" />
      <div className="pos-modal" role="dialog" aria-modal="true">
        <div className="pos-modal-header">
          <span className="pos-modal-title">Abrir Posición</span>
          <button className="chart-close-btn" onClick={onClose}>✕</button>
        </div>

        <form className="pos-modal-body" onSubmit={handleSubmit}>

          {/* Symbol + Direction */}
          <div className="pos-form-row">
            <div className="pos-form-group pos-form-group--grow">
              <label className="pos-label">Par</label>
              <select
                className="pos-select"
                value={symbol}
                onChange={e => setSymbol(e.target.value)}
              >
                {symbols.map(s => (
                  <option key={s.symbol} value={s.symbol}>{s.symbol}</option>
                ))}
              </select>
            </div>
            <div className="pos-form-group">
              <label className="pos-label">Dirección</label>
              <div className="pos-dir-toggle">
                <button
                  type="button"
                  className={`pos-dir-btn ${direction === 'LONG' ? 'pos-dir-btn--long' : ''}`}
                  onClick={() => setDirection('LONG')}
                >LONG</button>
                <button
                  type="button"
                  className={`pos-dir-btn ${direction === 'SHORT' ? 'pos-dir-btn--short' : ''}`}
                  onClick={() => setDirection('SHORT')}
                >SHORT</button>
              </div>
            </div>
          </div>

          {/* Entry price + Size */}
          <div className="pos-form-row">
            <div className="pos-form-group pos-form-group--grow">
              <label className="pos-label">Precio entrada</label>
              <div className="pos-input-wrap">
                <span className="pos-input-prefix">$</span>
                <input
                  type="number" step="any" required
                  className="pos-input"
                  value={entryPrice}
                  onChange={e => setEntryPrice(e.target.value)}
                  placeholder="0.00"
                />
              </div>
            </div>
            <div className="pos-form-group pos-form-group--grow">
              <label className="pos-label">Capital (USD)</label>
              <div className="pos-input-wrap">
                <span className="pos-input-prefix">$</span>
                <input
                  type="number" step="any" min="0"
                  className="pos-input"
                  value={sizeUsd}
                  onChange={e => setSizeUsd(e.target.value)}
                  placeholder="1000"
                />
              </div>
            </div>
          </div>

          {/* SL + TP */}
          <div className="pos-form-row">
            <div className="pos-form-group pos-form-group--grow">
              <label className="pos-label">Stop Loss</label>
              <div className="pos-input-wrap">
                <span className="pos-input-prefix pos-input-prefix--red">$</span>
                <input
                  type="number" step="any" min="0"
                  className="pos-input"
                  value={slPrice}
                  onChange={e => setSlPrice(e.target.value)}
                  placeholder="0.00"
                />
              </div>
              {slPct != null && (
                <span className="pos-field-hint pos-field-hint--red">
                  {slPct.toFixed(2)}%
                </span>
              )}
            </div>
            <div className="pos-form-group pos-form-group--grow">
              <label className="pos-label">Take Profit</label>
              <div className="pos-input-wrap">
                <span className="pos-input-prefix pos-input-prefix--green">$</span>
                <input
                  type="number" step="any" min="0"
                  className="pos-input"
                  value={tpPrice}
                  onChange={e => setTpPrice(e.target.value)}
                  placeholder="0.00"
                />
              </div>
              {tpPct != null && (
                <span className="pos-field-hint pos-field-hint--green">
                  +{tpPct.toFixed(2)}%
                </span>
              )}
            </div>
          </div>

          {/* Notes */}
          <div className="pos-form-group">
            <label className="pos-label">Notas</label>
            <input
              type="text"
              className="pos-input"
              value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder="Opcional..."
            />
          </div>

          {/* Calculated stats */}
          <div className="pos-calc-row">
            {qty > 0 && (
              <div className="pos-calc-chip">
                <span className="pos-calc-label">Cantidad</span>
                <span className="pos-calc-val">{qty.toFixed(6)}</span>
              </div>
            )}
            {rr != null && (
              <div className={`pos-calc-chip ${rr >= 2 ? 'pos-calc-chip--good' : 'pos-calc-chip--warn'}`}>
                <span className="pos-calc-label">R:R</span>
                <span className="pos-calc-val">{rr.toFixed(2)}:1</span>
              </div>
            )}
            {maxRisk != null && (
              <div className="pos-calc-chip pos-calc-chip--red">
                <span className="pos-calc-label">Riesgo máx.</span>
                <span className="pos-calc-val">${maxRisk.toFixed(2)}</span>
              </div>
            )}
          </div>

          {error && <div className="pos-error">{error}</div>}

          <div className="pos-modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancelar
            </button>
            <button
              type="submit"
              className={`btn ${direction === 'LONG' ? 'btn-long' : 'btn-short'}`}
              disabled={saving}
            >
              {saving ? 'Guardando…' : `Abrir ${direction}`}
            </button>
          </div>
        </form>
      </div>
    </>
  );
};

export default OpenPositionModal;
