// VidaScreen.tsx
import React from 'react';
import type { Frescura, ValleyEval } from '../../types';
import type { AsyncState } from './useValleyBundle';
import { Eyebrow, Callout, Loading, VerNumeros } from './atoms';
import styles from './valles.module.css';

const RAZONES_MUERTE: Record<string, string> = {
  volumen_bajo_piso: 'El volumen está por debajo del piso que se pide.',
  volumen_agonizante: 'El volumen viene cayendo hasta casi apagarse.',
  velas_planas: 'Las velas están casi planas — apenas se mueve.',
  historia_insuficiente: 'No hay suficiente historia para evaluarla.',
};

export const VidaScreen: React.FC<{ symbol: string; state: AsyncState<ValleyEval>; frescura?: Frescura }> = ({ symbol, state, frescura }) => {
  const { data, loading, error } = state;
  let answer: React.ReactNode;

  if (loading) {
    answer = <Loading label="Revisando si está viva…" />;
  } else if (error || !data || data.estado === 'no_disponible') {
    answer = <Callout tone="mute" icon="?" title="No se pudo revisar ahora" sub="Es un problema de la herramienta, no de la moneda." />;
  } else if (data.candidata === false) {
    answer = (
      <div className={styles.vwAnswer}>
        <div className={styles.vwAnswerRow}>
          <div className={styles.vwAnswerIcon} aria-hidden="true">▱</div>
          <div>
            <div className={styles.vwAnswerLead}>Está muy quieta</div>
            <div className={styles.vwAnswerSay}>Casi no se mueve, así que no entra en este tipo de análisis. Esto fue lo que se vio:</div>
          </div>
        </div>
        <ul className={styles.vwFacts}>
          {(data.razones_muerte ?? []).map((r) => (
            <li className={styles.vwFact} key={r}><span className={styles.vwFactB} aria-hidden="true">—</span>{RAZONES_MUERTE[r] ?? r}</li>
          ))}
        </ul>
      </div>
    );
  } else {
    const quietud = 100 - Math.round((data.vol_percentil ?? 0) * 100);
    answer = (
      <div className={styles.vwAnswer}>
        <div className={styles.vwAnswerRow}>
          <div className={styles.vwAnswerIcon} aria-hidden="true">∿</div>
          <div>
            <div className={styles.vwAnswerLead}>Está viva y tranquila</div>
            <div className={styles.vwAnswerSay}>Se mueve dentro de una franja angosta, sin pegar saltos. A eso se le dice estar <b>"en valle"</b>.</div>
          </div>
        </div>
        <ul className={styles.vwFacts}>
          <li className={styles.vwFact}><span className={styles.vwFactB} aria-hidden="true">●</span>Se mueve poco: su franja es de un <b>{((data.pct_rango ?? 0) * 100).toFixed(1)}%</b> de su precio.</li>
          <li className={styles.vwFact}><span className={styles.vwFactB} aria-hidden="true">●</span>Lleva <b>{data.semanas_consolidando} semanas</b> sin salirse de esa franja.</li>
          <li className={styles.vwFact}><span className={styles.vwFactB} aria-hidden="true">●</span>Hoy está más quieta que el <b>{quietud}%</b> de su último año.</li>
        </ul>
        <VerNumeros items={[
          { k: 'Ancho de la franja', v: `${((data.pct_rango ?? 0) * 100).toFixed(1)}%`, note: 'de su precio' },
          { k: 'Semanas consolidando', v: `${data.semanas_consolidando} sem`, note: 'sin salir de la banda' },
          { k: 'Volatilidad (percentil)', v: `p${Math.round((data.vol_percentil ?? 0) * 100)}`, note: 'en su propio año' },
        ]} />
      </div>
    );
  }

  return (
    <div className={styles.vwScreen}>
      <Eyebrow symbol={symbol} frescura={frescura} />
      <h2 className={styles.vwQuestion}>¿Está viva la moneda?</h2>
      {answer}
    </div>
  );
};
