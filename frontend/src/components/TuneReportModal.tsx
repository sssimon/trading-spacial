import React, { useState } from 'react';
import type { TuneResult, TuneSymbolResult } from '../types';

interface TuneReportModalProps {
  tune: TuneResult;
  onApply: () => Promise<void>;
  onReject: () => Promise<void>;
  onClose: () => void;
}

const TuneReportModal: React.FC<TuneReportModalProps> = ({ tune, onApply, onReject, onClose }) => {
  const [applying, setApplying] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [done, setDone] = useState<'applied' | 'rejected' | null>(null);
  const [showKept, setShowKept] = useState(false);

  const results = tune.results || [];
  const changes = results.filter((r) => r.recommendation === 'CHANGE');
  const kept = results.filter((r) => r.recommendation !== 'CHANGE');
  const isPending = tune.status === 'pending';
  const isApplied = tune.status === 'applied' || done === 'applied';

  const handleApply = async () => {
    setApplying(true);
    try { await onApply(); setDone('applied'); }
    catch (err) { console.error('Apply failed:', err); }
    finally { setApplying(false); }
  };

  const handleReject = async () => {
    setRejecting(true);
    try { await onReject(); setDone('rejected'); setTimeout(onClose, 1500); }
    catch (err) { console.error('Reject failed:', err); }
    finally { setRejecting(false); }
  };

  const formatDate = (ts: string) =>
    new Date(ts).toLocaleDateString('es-ES', {
      day: 'numeric', month: 'long', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });

  return (
    <>
      <div className="tune-overlay" onClick={onClose} />
      <div className="tune-modal">
        <div className="tune-modal-header">
          <div>
            <h2 className="tune-modal-title">Optimizacion de Parametros</h2>
            <p className="tune-modal-subtitle">
              {formatDate(tune.ts)} &middot; {tune.changes_count} cambio{tune.changes_count !== 1 ? 's' : ''} propuesto{tune.changes_count !== 1 ? 's' : ''}
            </p>
          </div>
          <button className="tune-close-btn" onClick={onClose}>&#x2715;</button>
        </div>

        {isApplied && <div className="tune-banner tune-banner--applied">Cambios aplicados exitosamente</div>}
        {done === 'rejected' && <div className="tune-banner tune-banner--rejected">Propuesta rechazada</div>}

        <div className="tune-modal-body">
          {changes.length > 0 && (
            <div className="tune-section">
              <h3 className="tune-section-title">Cambios Recomendados ({changes.length})</h3>
              {changes.map((r) => <SymbolChangeCard key={r.symbol} result={r} />)}
            </div>
          )}
          {changes.length === 0 && (
            <div className="tune-empty">Todos los parametros actuales son optimos. Sin cambios recomendados.</div>
          )}
          {kept.length > 0 && (
            <div className="tune-section">
              <button className="tune-collapse-btn" onClick={() => setShowKept((v) => !v)}>
                {showKept ? '\u25BE' : '\u25B8'} {kept.length} symbol{kept.length !== 1 ? 's' : ''} sin cambios
              </button>
              {showKept && (
                <div className="tune-kept-list">
                  {kept.map((r) => (
                    <div key={r.symbol} className="tune-kept-item">
                      <span className="tune-kept-symbol">{r.symbol.replace('USDT', '')}</span>
                      <span className="tune-kept-params">
                        SL {r.current_params.atr_sl_mult}x &middot; TP {r.current_params.atr_tp_mult}x &middot; BE {r.current_params.atr_be_mult}x
                      </span>
                      <span className="tune-kept-status">optimo</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {isPending && !done && (
          <div className="tune-modal-footer">
            <button className="btn btn-secondary" onClick={handleReject} disabled={applying || rejecting}>
              {rejecting ? 'Rechazando...' : 'Rechazar'}
            </button>
            <button className="btn btn-primary tune-apply-btn" onClick={handleApply} disabled={applying || rejecting || changes.length === 0}>
              {applying ? (<><span className="btn-spinner" /> Aplicando...</>) : `Aplicar ${changes.length} cambio${changes.length !== 1 ? 's' : ''}`}
            </button>
          </div>
        )}
        {done === 'applied' && (
          <div className="tune-modal-footer">
            <button className="btn btn-primary" onClick={onClose}>Cerrar</button>
          </div>
        )}
      </div>
    </>
  );
};

const SymbolChangeCard: React.FC<{ result: TuneSymbolResult }> = ({ result }) => {
  const { current_params: cur, proposed_params: prop, proposal_detail: detail } = result;
  if (!prop || !detail) return null;
  return (
    <div className="tune-change-card">
      <div className="tune-change-header">
        <span className="tune-change-symbol">{result.symbol.replace('USDT', '')}</span>
        <span className="tune-change-improvement">+{detail.improvement_pct}%</span>
      </div>
      <table className="tune-change-table">
        <thead><tr><th></th><th>SL</th><th>TP</th><th>BE</th><th>P&amp;L Val</th></tr></thead>
        <tbody>
          <tr className="tune-row-current">
            <td>Actual</td><td>{cur.atr_sl_mult}x</td><td>{cur.atr_tp_mult}x</td><td>{cur.atr_be_mult}x</td>
            <td>${(result.current_val_pnl ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
          </tr>
          <tr className="tune-row-proposed">
            <td>Nuevo</td><td>{prop.atr_sl_mult}x</td><td>{prop.atr_tp_mult}x</td><td>{prop.atr_be_mult}x</td>
            <td className="tune-pnl-positive">${detail.val_pnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
          </tr>
        </tbody>
      </table>
      <div className="tune-change-meta">
        <span>PF: {detail.val_pf.toFixed(2)}</span>
        <span>{detail.total_trades} trades</span>
      </div>
    </div>
  );
};

export default TuneReportModal;
