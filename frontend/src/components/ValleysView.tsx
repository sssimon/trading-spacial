// ============================================================
// ValleysView — Vista Valles A: lista NEUTRAL de monedas vivas
// en consolidación. Sin badges de compra, sin colores de señal:
// presenta hechos, no juicios.
// ============================================================

import React, { useState } from 'react';
import type { ValleySnapshot, Dossier, SrLevels } from '../types';
import { getDossier, getLevels } from '../api';
import { formatPrice } from '../utils';
import { ProjectDossier } from './ProjectDossier';
import { LevelsPanel } from './LevelsPanel';
import { CoinCard } from './CoinCard';
import { FreshnessTag } from './FreshnessTag';
import styles from './ValleysView.module.css';

export const ValleysView: React.FC<{ snapshot: ValleySnapshot; loading: boolean }> = ({
  snapshot, loading,
}) => {
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [dossierLoading, setDossierLoading] = useState(false);
  const [levels, setLevels] = useState<SrLevels | null>(null);
  const [levelsLoading, setLevelsLoading] = useState(false);
  const [cardInput, setCardInput] = useState('');
  const [cardSymbol, setCardSymbol] = useState('');

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
      <form
        onSubmit={(e) => { e.preventDefault(); setCardSymbol(cardInput.trim().toUpperCase()); }}
      >
        <input
          value={cardInput}
          onChange={(e) => setCardInput(e.target.value)}
          placeholder="Símbolo (ej. SOLUSDT)"
        />
        <button type="submit">Ver tarjeta</button>
      </form>
      {cardSymbol && <CoinCard symbol={cardSymbol} />}
      <div className={`${styles.meta} prose`}>
        Foto del {new Date(generated_at).toLocaleString('es-ES')} · cobertura{' '}
        {coverage.evaluated} / {coverage.universe}
        {!coverage.complete && ' (incompleta)'}
        {snapshot.frescura && <>{' '}<FreshnessTag frescura={snapshot.frescura} /></>}
      </div>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Símbolo</th><th>Precio</th><th>Rango</th><th>Semanas</th>
            <th>Vol. percentil</th><th>Volumen/día</th><th>Desde máx.</th><th></th>
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
              <td>
                <button
                  className={styles.dossierBtn}
                  onClick={() => {
                    setDossier(null);
                    setDossierLoading(true);
                    getDossier(c.symbol)
                      .then(setDossier)
                      .finally(() => setDossierLoading(false));
                  }}
                >Dossier</button>
                <button
                  className={styles.dossierBtn}
                  onClick={() => {
                    setLevels(null);
                    setLevelsLoading(true);
                    getLevels(c.symbol)
                      .then(setLevels)
                      .finally(() => setLevelsLoading(false));
                  }}
                >Niveles</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(dossier || dossierLoading) && (
        <ProjectDossier dossier={dossier!} loading={dossierLoading} />
      )}
      {(levels || levelsLoading) && (
        <LevelsPanel levels={levels!} loading={levelsLoading} />
      )}
    </div>
  );
};
