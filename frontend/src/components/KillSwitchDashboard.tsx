// ============================================================
// KillSwitchDashboard.tsx — B6 dashboard observability (#187 #200)
// Polls /health/dashboard every 30s, renders alerts + portfolio + symbols.
// ============================================================

import React, { useEffect, useRef, useState } from 'react';
import { getHealthDashboard } from '../api';
import AlertsStrip from './AlertsStrip';
import PortfolioPanel from './PortfolioPanel';
import KillSwitchSymbolCard from './KillSwitchSymbolCard';
import type { DashboardResponse } from '../types';

const POLL_INTERVAL_MS = 30_000;

const KillSwitchDashboard: React.FC = () => {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const liveRegionRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    let lastAnnouncement = 0;

    const fetch = async () => {
      try {
        const resp = await getHealthDashboard();
        if (!alive) return;
        setData(resp);  // KEEP previous on top until new arrives
        setError(null);
        setLoading(false);
        // Polite announcement (debounce to 1×/5s)
        const now = Date.now();
        if (liveRegionRef.current && now - lastAnnouncement > 5000) {
          liveRegionRef.current.textContent = 'Datos actualizados';
          lastAnnouncement = now;
        }
      } catch (err) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : 'Error');
        setLoading(false);
      }
    };

    fetch();
    const id = setInterval(fetch, POLL_INTERVAL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (loading && !data) {
    return (
      <div className="ks-dashboard" aria-busy="true">
        <div className="ks-skeleton-strip" />
        <div className="ks-skeleton-portfolio" />
        <div className="ks-skeleton-grid">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="ks-skeleton-card" />
          ))}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="ks-dashboard">
        {error && (
          <div className="ks-error">Error cargando kill switch: {error}</div>
        )}
      </div>
    );
  }

  return (
    <div className="ks-dashboard">
      {error && (
        <div className="ks-error">Error refrescando: {error} (mostrando última data)</div>
      )}

      <div ref={liveRegionRef} role="status" aria-live="polite" className="ks-sr-only" />

      <AlertsStrip alerts={data.alerts} />
      <PortfolioPanel portfolio={data.portfolio} />

      <div className="ks-symbol-grid-v2">
        {data.symbols.map((s) => (
          <KillSwitchSymbolCard key={s.symbol} state={s} />
        ))}
        {data.symbols.length === 0 && (
          <div className="ks-empty">Sin datos aún — esperando scans.</div>
        )}
      </div>
    </div>
  );
};

export default KillSwitchDashboard;
