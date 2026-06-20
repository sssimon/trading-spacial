import React from 'react';
import type { RegimeSnapshot, RegimeComponent, Frescura } from '../../../types';
import styles from './regime.module.css';

// ── helper: edad en texto legible ──────────────────────────
function edad(seg: number | null): string | null {
  if (seg == null) return null;
  if (seg < 3600) return 'hace ' + Math.max(1, Math.round(seg / 60)) + ' min';
  if (seg < 172800) return 'hace ' + Math.round(seg / 3600) + ' h';
  return 'hace ' + Math.round(seg / 86400) + ' dias';
}

// ── átomo de frescura (fresco / rancio / muerto se ven distinto) ─
export function Fresh({ frescura, noun }: { frescura: Frescura | null | undefined; noun?: string }) {
  if (!frescura) return null;
  const e = frescura.estado;
  const cls =
    e === 'muerto' ? styles['fr--muerto'] :
    e === 'rancio' ? styles['fr--rancio'] :
    styles['fr--fresco'];
  let txt: string;
  if (e === 'muerto') txt = `sin ${noun || 'foto'} — el screener aún no ha completado un ciclo`;
  else if (e === 'rancio') txt = `${noun || 'foto'} ${edad(frescura.edad_seg)} · rancia`;
  else txt = `${noun || 'foto'} ${edad(frescura.edad_seg)}`;
  return (
    <span className={`${styles.fr} ${cls}`}>
      <span className={styles['fr__dot']} />
      {txt}
    </span>
  );
}

// ── frescura del régimen (variante con etiqueta "foto del régimen") ─
function RegimeFresh({ frescura }: { frescura: Frescura | null | undefined }) {
  if (!frescura) return null;
  const e = frescura.estado;
  const cls =
    e === 'muerto' ? styles['fr--muerto'] :
    e === 'rancio' ? styles['fr--rancio'] :
    styles['fr--fresco'];
  const tail =
    e === 'muerto' ? 'muerto' :
    e === 'rancio' ? `rancio · ${edad(frescura.edad_seg)}` :
    `fresco · ${edad(frescura.edad_seg)}`;
  return (
    <span className={`${styles.fr} ${cls}`}>
      <span className={styles['fr__dot']} />
      foto del régimen: {tail}
    </span>
  );
}

// ── textos de inclinación ─────────────────────────────────
const LEAN_TXT: Record<string, React.ReactNode> = {
  alts:  <>Inclinación del mercado: <b>hacia las alts</b></>,
  mixto: <>Inclinación del mercado: <b>mixta</b></>,
  btc:   <>Inclinación del mercado: <b>hacia BTC</b></>,
};
const LEAN_SHORT: Record<string, string> = {
  alts: 'hacia alts',
  mixto: 'mixta',
  btc: 'hacia BTC',
};
const LEAN_BARE: Record<string, string> = {
  alts: 'alts',
  mixto: 'lo mixto',
  btc: 'BTC',
};

// ── componente individual del régimen ────────────────────
function Component({
  label,
  comp,
  render: renderVal,
}: {
  label: string;
  comp: RegimeComponent | null | undefined;
  render: (v: number) => string;
}) {
  if (!comp || comp.estado === 'muerto') {
    return (
      <div className={`${styles['rf__comp']} ${styles['rf__comp--muerto']}`}>
        <div className={styles['rf__comp-k']}>{label}</div>
        <div className={styles['rf__comp-v']}>sin dato</div>
        <div className={styles['rf__comp-lean']}>fuente caída</div>
      </div>
    );
  }
  return (
    <div className={styles['rf__comp']}>
      <div className={styles['rf__comp-k']}>{label}</div>
      <div className={styles['rf__comp-v']}>{renderVal(comp.valor as number)}</div>
      <div className={styles['rf__comp-lean']}>
        se inclina a {comp.lean === 'neutral' ? 'ninguno (neutral)' : comp.lean}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════
// PIEZA 1 · MARCO DE RÉGIMEN — cabecera dominante
// ════════════════════════════════════════════════════════════
export function RegimeFrame({ regime }: { regime: RegimeSnapshot | null }) {
  const frescura = regime?.frescura ?? null;
  const isDead = !regime || !regime.regime || frescura?.estado === 'muerto';

  if (isDead) {
    return (
      <header className={styles.rf}>
        <div className={styles['rf__eyebrow']}>Clima del mercado</div>
        <div className={styles['rf__lean']}>El régimen de mercado no está disponible ahora.</div>
        <div className={`${styles['fr-dead']}`} style={{ marginTop: 18 }}>
          <span className={styles['fr-dead__icon']}>⧖</span>
          <div>
            <div className={styles['fr-dead__t']}>La foto del régimen está caída</div>
            <div className={styles['fr-dead__s']}>
              El productor del clima no respondió o aún no corrió un ciclo. No es un dato viejo disfrazado — es ausencia honesta.
            </div>
          </div>
        </div>
        {/* VERBATIM */}
        <div className={styles['rf__doctrine']}>
          Lo que más mueve el resultado es el régimen del mercado, no la moneda que elijas.
        </div>
      </header>
    );
  }

  const r = regime!.regime;
  const votos = r.votos;
  const leanCount = votos[r.estado as keyof typeof votos] as number || 0;
  const comps = r.componentes as Record<string, RegimeComponent>;

  return (
    <header className={styles.rf}>
      <div className={styles['rf__eyebrow']}>Clima del mercado · enmarca esta moneda</div>
      <div className={styles['rf__lean']}>{LEAN_TXT[r.estado]}</div>

      <div className={styles['rf__components']}>
        <Component
          label="amplitud (alts sobre su media 50d)"
          comp={comps.breadth50}
          render={(v) => (v * 100).toFixed(1) + '%'}
        />
        <Component
          label="alts vs BTC · 30 días"
          comp={comps.outperf_30d}
          render={(v) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'}
        />
        <Component
          label="dominancia BTC"
          comp={comps.dominancia_btc}
          render={(v) => (v * 100).toFixed(1) + '%'}
        />
      </div>

      <div style={{ marginTop: 12, fontSize: 14, color: 'var(--ink-3)' }}>
        Decidido por {votos.vivos} {votos.vivos === 1 ? 'juez' : 'jueces'} ·{' '}
        {leanCount} se {leanCount === 1 ? 'inclina' : 'inclinan'} a {LEAN_BARE[r.estado]}
        {votos.neutral ? `, ${votos.neutral} neutral` : ''}.
      </div>

      <p className={styles['rf__doctrine']}>
        {/* VERBATIM */}
        Lo que más mueve el resultado es <b>el régimen del mercado</b>, no la moneda que elijas.
      </p>
      <div className={styles['rf__foot']}>
        <RegimeFresh frescura={frescura} />
      </div>
    </header>
  );
}

// ════════════════════════════════════════════════════════════
// FRANJA PERSISTENTE (sticky) — repite el clima mientras se lee
// ════════════════════════════════════════════════════════════
export function RegimeStrip({ regime }: { regime: RegimeSnapshot | null }) {
  const frescura = regime?.frescura ?? null;
  const isDead = !regime || !regime.regime || frescura?.estado === 'muerto';

  if (isDead) {
    return (
      <div className={styles['rf-strip']}>
        <span className={styles['rf-strip__lean']}>
          <span className={styles['rf-strip__chip']}>clima</span> no disponible
        </span>
        <span className={styles['rf-strip__sep']} />
        <Fresh frescura={frescura} noun="foto" />
      </div>
    );
  }

  const r = regime!.regime;
  return (
    <div className={styles['rf-strip']}>
      <span className={styles['rf-strip__lean']}>
        <span className={styles['rf-strip__chip']}>clima</span> {LEAN_SHORT[r.estado]}
      </span>
      <span className={styles['rf-strip__note']}>el clima manda sobre la moneda — no la valida</span>
      <span className={styles['rf-strip__sep']} />
      <RegimeFresh frescura={frescura} />
    </div>
  );
}
