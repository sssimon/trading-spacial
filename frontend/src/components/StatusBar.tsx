// ============================================================
// StatusBar.tsx — dense horizontal strip of macro + scanner state.
//
// Six cells from left to right:
//   REGIMEN BTC · F&G · FUNDING · ESCANEOS · KILL-SWITCH · ERRORES
//
// Macro cells (REGIMEN / F&G / FUNDING) come from /macro via useMacro.
// Scanner cells (ESCANEOS / KILL-SWITCH / ERRORES) come from /status.
// ============================================================

import React from 'react';
import styles from './StatusBar.module.css';
import type { StatusResponse } from '../types';
import type { MacroResponse } from '../api';

interface StatusBarProps {
  status: StatusResponse | null;
  macro:  MacroResponse | null;
  /** Count of symbols currently PAUSED or in PROBATION by the kill switch.
   *  Derived in App.tsx from /health/dashboard. Optional so existing call
   *  sites that don't have the dashboard yet keep compiling (the cell shows
   *  "todos activos" / 0 in that case). */
  killSwitchActive?: number;
}

type Tone = 'bull' | 'bear' | 'warn' | 'neutral' | 'dim';

interface ItemProps {
  label:  string;
  value:  string;
  suffix?: string;
  tone?:  Tone;
  hint?:  string;
}
const Item: React.FC<ItemProps> = ({ label, value, suffix, tone = 'neutral', hint }) => (
  <div className={styles.item} title={hint}>
    <div className={`${styles.label} label`}>{label}</div>
    <div className={`${styles.value} ${styles[`value--${tone}`]}`}>
      <span className="num">{value}</span>
      {suffix && <span className={styles.suffix}> · {suffix}</span>}
    </div>
  </div>
);

function regimeTone(r: string | null | undefined): Tone {
  if (r === 'BULL') return 'bull';
  if (r === 'BEAR') return 'bear';
  if (r === 'NEUTRAL') return 'warn';
  return 'dim';
}

function fgTone(idx: number | null | undefined): Tone {
  if (idx == null) return 'dim';
  if (idx >= 75) return 'bear';   // Extreme greed → contrarian bear
  if (idx >= 55) return 'warn';
  if (idx >= 45) return 'neutral';
  if (idx >= 25) return 'bull';   // Slight fear → contrarian bull (buy zone)
  return 'bull';                  // Extreme fear → contrarian bull
}

function fundingTone(v: number | null | undefined): Tone {
  if (v == null) return 'dim';
  if (v > 0) return 'bull';
  if (v < 0) return 'bear';
  return 'neutral';
}

const StatusBar: React.FC<StatusBarProps> = ({ status, macro, killSwitchActive }) => {
  const s = status?.scanner_state;
  const scansTotal   = s?.scans_total   ?? 0;
  const signalsTotal = s?.signals_total ?? 0;
  const errors       = s?.errors        ?? 0;

  const regime    = macro?.regime ?? null;
  const rgScore   = macro?.regime_score;
  const fg        = macro?.fear_greed_index;
  const fgLabel   = macro?.fear_greed_label ?? '—';
  const funding   = macro?.funding_rate_pct;

  const ksPaused  = killSwitchActive ?? 0;

  return (
    <div className={styles.strip}>
      <Item
        label="Régimen BTC"
        value={regime ?? '—'}
        suffix={rgScore != null ? rgScore.toFixed(0) : undefined}
        tone={regimeTone(regime)}
        hint="Régimen compuesto (precio + sentimiento + funding)"
      />
      <Item
        label="F&G"
        value={fg != null ? String(fg) : '—'}
        suffix={fgLabel}
        tone={fgTone(fg)}
        hint="Fear & Greed index — 0 extremo miedo, 100 extrema codicia"
      />
      <Item
        label="Funding"
        value={
          funding != null
            ? `${funding >= 0 ? '+' : ''}${funding.toFixed(4)}%`
            : '—'
        }
        tone={fundingTone(funding)}
        hint="Tasa de financiación BTC perp /8h"
      />
      <Item
        label="Escaneos"
        value={scansTotal.toLocaleString('es-ES')}
        suffix={`${signalsTotal} señales`}
        tone="bull"
        hint="Ciclos completados · señales detectadas (gatillo 5M)"
      />
      <Item
        label="Kill-switch"
        value={String(ksPaused)}
        suffix={ksPaused === 0 ? 'todos activos' : 'pausados'}
        tone={ksPaused === 0 ? 'dim' : 'warn'}
        hint="Símbolos pausados por kill-switch"
      />
      <Item
        label="Errores"
        value={String(errors)}
        tone={errors > 0 ? 'bear' : 'dim'}
        hint="Fallos en el último ciclo"
      />
    </div>
  );
};

export default StatusBar;
