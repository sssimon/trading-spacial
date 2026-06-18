import React, { useEffect, useState } from 'react';
import { getAltSeason } from '../../api';
import type { RegimeSnapshot } from '../../types';
import styles from './AltSeasonHeader.module.css';

const ESTADO_LABEL: Record<string, string> = {
  alts: 'Inclinación del mercado: hacia alts',
  mixto: 'Inclinación del mercado: mixta',
  btc: 'Inclinación del mercado: hacia BTC',
};

function pct(v: number | null): string {
  return v === null ? '—' : `${(v * 100).toFixed(1)}%`;
}

export const AltSeasonHeader: React.FC = () => {
  const [snap, setSnap] = useState<RegimeSnapshot | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    getAltSeason().then(s => { if (alive) setSnap(s); }).catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, []);

  if (error) return <div className={styles.header}>El régimen de mercado no está disponible ahora.</div>;
  if (!snap) return <div className={styles.header}>Cargando el régimen del mercado…</div>;

  const { regime, frescura } = snap;
  const c = regime.componentes;
  const dom = c.dominancia_btc;
  const breadth = c.breadth50;
  const outperf = c.outperf_30d;

  return (
    <div className={styles.header}>
      <div className={styles.estado} data-testid="regime-estado">
        {ESTADO_LABEL[regime.estado] ?? regime.estado}
      </div>
      <div className={styles.componentes}>
        {breadth?.estado === 'muerto'
          ? <span className={`${styles.comp} ${styles.muerta}`} data-testid="breadth-muerta">
              breadth: sin dato ({breadth.razon ?? 'cobertura baja'})</span>
          : <span className={styles.comp}>breadth: {pct(breadth?.valor ?? null)}
              {breadth?.n != null ? ` (n=${breadth.n})` : ''}</span>}
        {outperf?.estado === 'muerto'
          ? <span className={`${styles.comp} ${styles.muerta}`} data-testid="outperf-muerta">
              outperf 30d: sin dato</span>
          : <span className={styles.comp}>outperf 30d: {pct(outperf?.valor ?? null)}</span>}
        {dom?.estado === 'muerto'
          ? <span className={`${styles.comp} ${styles.muerta}`} data-testid="dominancia-muerta">
              dominancia: sin dato (fuente caída)</span>
          : <span className={styles.comp}>dominancia BTC: {pct(dom?.valor ?? null)}</span>}
      </div>
      <div className={styles.frase}>
        Lo que más mueve el resultado es el régimen del mercado, no la moneda que elijas.
      </div>
      {frescura && (
        <div className={styles.frescura}>foto del régimen: {frescura.estado}</div>
      )}
    </div>
  );
};
