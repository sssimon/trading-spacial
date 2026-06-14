// ValleysFlow.tsx
import React, { useCallback, useEffect, useState } from 'react';
import type { ValleySnapshot } from '../../types';
import { useValleyBundle } from './useValleyBundle';
import { PickScreen } from './PickScreen';
import { VidaScreen } from './VidaScreen';
import { NivelesScreen } from './NivelesScreen';
import { FundScreen } from './FundScreen';
import { ClosingScreen } from './ClosingScreen';
import { Copilot } from './Copilot';
import styles from './valles.module.css';

const STEPS = ['pick', 'vida', 'niveles', 'fund', 'cierre'] as const;
type Step = typeof STEPS[number];
const LENS: { key: Step; label: string }[] = [
  { key: 'vida', label: 'Vida' }, { key: 'niveles', label: 'Niveles' }, { key: 'fund', label: 'Quién' },
];

export const ValleysFlow: React.FC<{ snapshot: ValleySnapshot; loading: boolean }> = ({ snapshot, loading }) => {
  const [sym, setSym] = useState<string>(() => localStorage.getItem('vw_sym') ?? '');
  const [step, setStep] = useState<number>(() => Number(localStorage.getItem('vw_step') ?? 0));
  const [dock, setDock] = useState(false);

  useEffect(() => { localStorage.setItem('vw_step', String(step)); }, [step]);
  useEffect(() => { if (sym) localStorage.setItem('vw_sym', sym); }, [sym]);

  const bundle = useValleyBundle(sym);
  const go = useCallback((n: number | ((s: number) => number)) =>
    setStep((s) => Math.max(0, Math.min(STEPS.length - 1, typeof n === 'function' ? n(s) : n))), []);

  // keydown SOLO mientras este componente está montado (se limpia al desmontar la tab)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (dock) return;
      if (e.key === 'ArrowRight' && step > 0 && step < STEPS.length - 1) go((s) => s + 1);
      if (e.key === 'ArrowLeft' && step > 1) go((s) => s - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step, dock, go]);

  const pick = (s: string) => { setSym(s); go(1); };
  const restart = () => { setSym(''); go(0); localStorage.removeItem('vw_sym'); };

  const cur: Step = STEPS[step];
  let screen: React.ReactNode;
  if (cur === 'pick' || !sym) screen = <PickScreen snapshot={snapshot} onPick={pick} />;
  else if (cur === 'vida') screen = <VidaScreen symbol={sym} state={bundle.vida} frescura={snapshot.frescura} />;
  else if (cur === 'niveles') screen = <NivelesScreen symbol={sym} state={bundle.niveles} />;
  else if (cur === 'fund') screen = <FundScreen symbol={sym} state={bundle.dossier} onRefresh={bundle.refreshDossier} />;
  else screen = <ClosingScreen symbol={sym} bundle={bundle} onAsk={() => setDock(true)} onRestart={restart} />;

  const lensIdx = cur === 'cierre' ? 3 : ({ vida: 0, niveles: 1, fund: 2 } as Record<string, number>)[cur];

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
          {step > 0 && sym && (
            <div className={styles.vwSteps} role="group" aria-label={`Paso ${lensIdx + 1} de ${LENS.length}`}>
              <span className={styles.vwStepCount}>Paso {Math.min(lensIdx + 1, LENS.length)} de {LENS.length}</span>
              {LENS.map((l, i) => (
                <React.Fragment key={l.key}>
                  {i > 0 && <span className={styles.vwStepSep} aria-hidden="true" />}
                  <button
                    className={`${styles.vwStep} ${lensIdx === i ? styles.vwStepActive : ''} ${lensIdx > i ? styles.vwStepDone : ''}`}
                    onClick={() => sym && go(i + 1)} disabled={!sym}
                    aria-current={lensIdx === i ? 'step' : undefined}
                  >
                    <span className={styles.vwStepN} aria-hidden="true">{lensIdx > i ? '✓' : i + 1}</span>
                    <span className={styles.vwStepLabel}>{l.label}</span>
                  </button>
                </React.Fragment>
              ))}
            </div>
          )}
        </div>

        <div className={styles.vwStage}>
          {loading && cur === 'pick' ? <div className={styles.vwScreen}><p className={styles.vwPickLead}>Cargando la foto…</p></div>
            : React.isValidElement(screen) ? React.cloneElement(screen, { key: cur + sym } as never) : screen}
        </div>

        {step > 0 && cur !== 'pick' && sym && (
          <div className={styles.vwNav}>
            <div className={styles.vwNavInner}>
              <button className={`${styles.vwBtn} ${styles.vwBtnGhost}`} onClick={() => go((s) => s - 1)} disabled={step <= 1}>← Atrás</button>
              {cur !== 'cierre'
                ? <button className={`${styles.vwBtn} ${styles.vwBtnPrimary}`} onClick={() => go((s) => s + 1)}>{cur === 'fund' ? 'Cerrar el recorrido' : 'Siguiente'} →</button>
                : <button className={`${styles.vwBtn} ${styles.vwBtnPrimary}`} onClick={restart}>Mirar otra moneda →</button>}
            </div>
          </div>
        )}

        {!dock && step >= 1 && sym && <button className={styles.vwFab} onClick={() => setDock(true)} aria-label="Preguntar al copiloto">◈</button>}
        {dock && <Copilot onClose={() => setDock(false)} />}
      </div>
    </div>
  );
};
