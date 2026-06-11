// ============================================================
// ValleysView — Vista Valles A: lista NEUTRAL de monedas vivas
// en consolidación. Sin badges de compra, sin colores de señal:
// presenta hechos, no juicios.
// ============================================================

import React from 'react';
import type { ValleySnapshot } from '../types';
import { formatPrice } from '../utils';
import styles from './ValleysView.module.css';

export const ValleysView: React.FC<{ snapshot: ValleySnapshot; loading: boolean }> = ({
  snapshot, loading,
}) => {
  if (loading) return <div className={styles.empty}>Cargando…</div>;
  const { generated_at, coverage, candidates } = snapshot;
  if (!generated_at || candidates.length === 0) {
    return (
      <div className={styles.empty}>
        Aún no hay foto del screener. Corré <code>python -m tools.run_valley_screener</code>.
      </div>
    );
  }
  return (
    <div className={styles.wrap}>
      <div className={`${styles.meta} prose`}>
        Foto del {new Date(generated_at).toLocaleString('es-ES')} · cobertura{' '}
        {coverage.evaluated} / {coverage.universe}
        {!coverage.complete && ' (incompleta)'}
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Símbolo</th><th>Precio</th><th>Rango</th><th>Semanas</th>
            <th>Vol. percentil</th><th>Volumen/día</th><th>Desde máx.</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => (
            <tr key={c.symbol}>
              <td className={styles.sym}>{c.symbol}</td>
              <td className="num">{formatPrice(c.price)}</td>
              <td className="num">{(c.pct_rango * 100).toFixed(1)}%</td>
              <td className="num">{c.semanas_consolidando}</td>
              <td className="num">{Math.round(c.vol_percentil * 100)}%</td>
              <td className="num">${Math.round(c.volumen_usd_dia).toLocaleString('en-US')}</td>
              <td className="num">−{(c.distancia_ath_pct * 100).toFixed(0)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
