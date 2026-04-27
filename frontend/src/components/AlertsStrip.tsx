import React from 'react';
import type { DashboardAlertSummary } from '../types';

interface AlertsStripProps {
  alerts: DashboardAlertSummary;
}

const AlertsStrip: React.FC<AlertsStripProps> = ({ alerts }) => {
  if (alerts.items.length === 0) return null;

  return (
    <div className="ks-alerts-strip" role="region" aria-label="Alertas recientes">
      {alerts.items.map((item, i) => (
        <span
          key={i}
          className={`ks-alert ks-alert-${item.severity}`}
        >
          {item.text}
        </span>
      ))}
    </div>
  );
};

export default AlertsStrip;
