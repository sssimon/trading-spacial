// ============================================================
// Header.tsx — Top navigation bar with scanner status
// ============================================================

import React from 'react';
import NotificationBell from './NotificationBell';

interface HeaderProps {
  scannerRunning: boolean;
  lastRefresh: Date | null;
  scanning: boolean;
  onRefresh: () => void;
  onScan: () => void;
  onConfigOpen: () => void;
  hasPendingTune: boolean;
  onTuneOpen: () => void;
}

function formatTime(date: Date | null): string {
  if (!date) return '—';
  return date.toLocaleTimeString('es-ES', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

const Header: React.FC<HeaderProps> = ({
  scannerRunning,
  lastRefresh,
  scanning,
  onRefresh,
  onScan,
  onConfigOpen,
  hasPendingTune,
  onTuneOpen,
}) => {
  return (
    <header className="header">
      {/* Left: brand */}
      <div className="header-left">
        <div className="header-brand">
          <span className="header-logo">⬡</span>
          <div className="header-title-group">
            <span className="header-title">CRYPTO SCANNER</span>
            <span className="header-subtitle">V6 · Top 20</span>
          </div>
        </div>
      </div>

      {/* Center: live status */}
      <div className="header-center">
        <span
          className={`status-dot ${scannerRunning ? 'status-dot--live' : 'status-dot--offline'}`}
        />
        <span
          className={`status-label ${scannerRunning ? 'status-label--live' : 'status-label--offline'}`}
        >
          {scannerRunning ? 'LIVE' : 'OFFLINE'}
        </span>
        <span className="status-refresh">
          Actualizado: {formatTime(lastRefresh)}
        </span>
      </div>

      {/* Right: action buttons */}
      <div className="header-right">
        <button
          className="btn btn-secondary"
          onClick={onRefresh}
          disabled={scanning}
          title="Actualizar datos"
        >
          Actualizar
        </button>
        <button
          className="btn btn-primary"
          onClick={onScan}
          disabled={scanning}
          title="Forzar escaneo completo"
        >
          {scanning ? (
            <>
              <span className="btn-spinner" />
              Escaneando…
            </>
          ) : (
            'Escanear ahora'
          )}
        </button>
        {hasPendingTune && (
          <button
            className="btn btn-icon tune-badge-btn"
            onClick={onTuneOpen}
            title="Parametros optimizados pendientes de revision"
            aria-label="Revision de parametros"
          >
            <span className="tune-badge-icon">&#x2691;</span>
            <span className="tune-badge-dot" />
          </button>
        )}
        <NotificationBell />
        <button
          className="btn btn-icon"
          onClick={onConfigOpen}
          title="Configurar filtros de señales"
          aria-label="Configuracion"
        >
          ⚙
        </button>
      </div>
    </header>
  );
};

export default Header;
