// ClosingScreen.tsx
import React from 'react';
import type { ValleyBundle } from './useValleyBundle';
import { humanName } from './atoms';
import { vidaRecap, nivelesRecap, dossierRecap } from './recap';
import styles from './valles.module.css';

export const ClosingScreen: React.FC<{ symbol: string; bundle: ValleyBundle; onAsk: () => void; onRestart: () => void }> = ({ symbol, bundle, onAsk, onRestart }) => (
  <div className={styles.vwScreen}>
    <div className={styles.vwCloseIcon} aria-hidden="true">⌖</div>
    <h2 className={styles.vwCloseTitle}>Estas tres cosas son hechos. La decisión es tuya.</h2>
    <p className={styles.vwCloseBody}>
      Valles te mostró, por separado, si <b>{humanName(symbol)}</b> está viva, dónde está su precio
      y quién está detrás. No suma las tres en una nota ni te dice qué hacer — eso lo decides tú,
      con tu propio criterio.
    </p>
    <div className={styles.vwCloseRecap}>
      <div className={styles.vwRecap}><span className={styles.vwRecapN}>1</span><span className={styles.vwRecapQ}>¿Está viva?</span><span className={styles.vwRecapA}>{vidaRecap(bundle.vida.data)}</span></div>
      <div className={styles.vwRecap}><span className={styles.vwRecapN}>2</span><span className={styles.vwRecapQ}>¿Dónde está el precio?</span><span className={styles.vwRecapA}>{nivelesRecap(bundle.niveles.data)}</span></div>
      <div className={styles.vwRecap}><span className={styles.vwRecapN}>3</span><span className={styles.vwRecapQ}>¿Quién está detrás?</span><span className={styles.vwRecapA}>{dossierRecap(bundle.dossier.data)}</span></div>
    </div>
    <div className={styles.vwCloseActions}>
      <button className={`${styles.vwBtn} ${styles.vwBtnOutline}`} onClick={onAsk}>◈ Preguntarle al copiloto</button>
      <button className={`${styles.vwBtn} ${styles.vwBtnGhost}`} onClick={onRestart}>Mirar otra moneda</button>
    </div>
  </div>
);
