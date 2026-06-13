// ============================================================
// CoinCard — la tarjeta de selección compuesta (F3b).
// Tres bloques YUXTAPUESTOS (A vida · D.1 niveles · C dossier),
// cada uno con su propio estado/frescura. La tarjeta EXHIBE, no
// firma: cero veredicto compuesto, cero score. Spec §3.
// ============================================================
import React, { useEffect, useState } from 'react';
import type { ValleyEval, SrLevels, Dossier } from '../types';
import { getValleyEval, getLevels, getDossier } from '../api';
import { LevelsPanel } from './LevelsPanel';
import { ProjectDossier } from './ProjectDossier';
import styles from './CoinCard.module.css';

const VidaBlock: React.FC<{ ev: ValleyEval | null; loading: boolean }> = ({ ev, loading }) => {
  if (loading) return <div className={styles.empty}>Evaluando…</div>;
  if (!ev || ev.estado === 'no_disponible') return <div className={styles.empty}>Sin datos</div>;
  if (ev.candidata) {
    return (
      <div className={styles.row}>
        viva · en rango {((ev.pct_rango ?? 0) * 100).toFixed(0)}% ·{' '}
        {ev.semanas_consolidando} semanas ·{' '}
        ${Math.round(ev.volumen_usd_dia ?? 0).toLocaleString('en-US')}/día
      </div>
    );
  }
  return (
    <div className={styles.row}>
      {ev.vivo ? 'viva, fuera de rango' : 'no candidata'}
      {ev.razones_muerte && ev.razones_muerte.length > 0
        ? ` — ${ev.razones_muerte.join(', ')}` : ''}
    </div>
  );
};

export const CoinCard: React.FC<{ symbol: string }> = ({ symbol }) => {
  const [vida, setVida] = useState<ValleyEval | null>(null);
  const [vidaLoading, setVidaLoading] = useState(true);
  const [levels, setLevels] = useState<SrLevels | null>(null);
  const [levelsLoading, setLevelsLoading] = useState(true);
  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [dossierLoading, setDossierLoading] = useState(true);

  useEffect(() => {
    setVida(null); setVidaLoading(true);
    setLevels(null); setLevelsLoading(true);
    setDossier(null); setDossierLoading(true);
    getValleyEval(symbol).then(setVida).finally(() => setVidaLoading(false));
    getLevels(symbol).then(setLevels).finally(() => setLevelsLoading(false));
    getDossier(symbol).then(setDossier).finally(() => setDossierLoading(false));
  }, [symbol]);

  return (
    <div className={styles.card}>
      <header className={styles.head}>{symbol.replace('USDT', '')}</header>

      <section className={styles.sec}>
        <h4>Vida</h4>
        <VidaBlock ev={vida} loading={vidaLoading} />
      </section>

      <section className={styles.sec}>
        <h4>Niveles</h4>
        {(levels || levelsLoading)
          ? <LevelsPanel levels={levels!} loading={levelsLoading} />
          : <div className={styles.empty}>Sin datos</div>}
      </section>

      <section className={styles.sec}>
        <h4>Fundamentales</h4>
        {(dossier || dossierLoading)
          ? <ProjectDossier dossier={dossier!} loading={dossierLoading} />
          : <div className={styles.empty}>Sin datos</div>}
      </section>
    </div>
  );
};
