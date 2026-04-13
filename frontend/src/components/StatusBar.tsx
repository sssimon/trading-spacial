// ============================================================
// StatusBar.tsx — 4 metric cards with scanner statistics
// ============================================================

import React from 'react';
import type { StatusResponse } from '../types';

interface StatusBarProps {
  status: StatusResponse | null;
}

interface MetricCardProps {
  label: string;
  value: string | number;
  icon: string;
  highlight?: boolean;
}

const MetricCard: React.FC<MetricCardProps> = ({ label, value, icon, highlight }) => (
  <div className={`metric-card${highlight ? ' metric-card--highlight' : ''}`}>
    <div className="metric-icon">{icon}</div>
    <div className="metric-body">
      <div className="metric-value">{value}</div>
      <div className="metric-label">{label}</div>
    </div>
  </div>
);

const StatusBar: React.FC<StatusBarProps> = ({ status }) => {
  const state = status?.scanner_state;

  const scansTotal = state?.scans_total ?? '—';
  const signalsTotal = state?.signals_total ?? '—';
  const errors = state?.errors ?? '—';
  const lastSymbol = state?.last_symbol ?? '—';

  return (
    <div className="status-bar">
      <MetricCard
        icon="📊"
        label="Escaneos totales"
        value={typeof scansTotal === 'number' ? scansTotal.toLocaleString('es-ES') : scansTotal}
      />
      <MetricCard
        icon="🎯"
        label="Señales detectadas"
        value={typeof signalsTotal === 'number' ? signalsTotal.toLocaleString('es-ES') : signalsTotal}
        highlight={typeof signalsTotal === 'number' && signalsTotal > 0}
      />
      <MetricCard
        icon="⚠️"
        label="Errores"
        value={typeof errors === 'number' ? errors.toLocaleString('es-ES') : errors}
      />
      <MetricCard
        icon="🔍"
        label="Último par escaneado"
        value={lastSymbol}
      />
    </div>
  );
};

export default StatusBar;
