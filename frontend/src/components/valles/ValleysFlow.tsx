// ValleysFlow.tsx
import React, { useState } from 'react';
import type { ValleySnapshot } from '../../types';
import { PickScreen } from './PickScreen';
import { IdeaView } from './idea/IdeaView';
import { Copilot } from './Copilot';
import { AltSeasonHeader } from './AltSeasonHeader';
import styles from './valles.module.css';

export const ValleysFlow: React.FC<{ snapshot: ValleySnapshot; loading: boolean }> = ({ snapshot, loading }) => {
  const [sym, setSym] = useState<string>(() => localStorage.getItem('vw_sym') ?? '');
  const [dock, setDock] = useState(false);

  const handlePick = (s: string) => {
    setSym(s);
    localStorage.setItem('vw_sym', s);
  };

  const handleRestart = () => {
    setSym('');
    localStorage.removeItem('vw_sym');
  };

  return (
    <div className={styles.vwRoot}>
      <div className={styles.vw}>
        <div className={styles.vwTop}>
          <div className={styles.vwBrand}>
            <span className={styles.vwBrandMark} aria-hidden="true">V</span>
            <span className={styles.vwBrandName}>Valles</span>
            {/* §4.1 — promesa de solo-hechos, no veredicto de operabilidad */}
            <span className={styles.vwBrandTag}>los hechos, lente por lente — la decisión es tuya</span>
          </div>
        </div>

        {/* AltSeasonHeader solo para la lista — IdeaView ya trae su RegimeFrame adentro */}
        {!sym && <AltSeasonHeader />}

        <div className={styles.vwStage}>
          {!sym
            ? (loading
                ? <div className={styles.vwScreen}><p className={styles.vwPickLead}>Cargando la foto…</p></div>
                : <PickScreen snapshot={snapshot} onPick={handlePick} />)
            : <IdeaView symbol={sym} onRestart={handleRestart} />}
        </div>

        {!dock && sym && <button className={styles.vwFab} onClick={() => setDock(true)} aria-label="Preguntar al copiloto">◈</button>}
        {dock && <Copilot onClose={() => setDock(false)} symbol={sym || undefined} />}
      </div>
    </div>
  );
};
