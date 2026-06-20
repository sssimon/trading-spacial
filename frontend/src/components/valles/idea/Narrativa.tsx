// ============================================================
// Narrativa.tsx — SP3 rewrite: VidaBlock · ParedesBlock · JugadaBlock
// Port 1:1 de sp3-ideaview.jsx líneas 139-248.
//
// Props: { vida, levels, plan } — cualquiera puede ser null.
// Empty-state honesto: nunca fabrica datos.
// Copy en tuteo venezolano. Costura AC7 VERBATIM.
// Decisión #3a: rama no-candidata-viva SIN número de posición.
// ============================================================

import React from 'react';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';
import { formatPrice } from '../../../utils';
import styles from './idea.module.css';

// ── helpers ──────────────────────────────────────────────────
const pr = (n: number | null | undefined): string =>
  n == null ? '—' : formatPrice(n);

const pct1 = (frac: number): string => {
  const sign = frac >= 0 ? '+' : '−';
  return `${sign}${Math.abs(frac).toFixed(1)}%`;
};

// ── helper toquesDe (port de sp3-ideaview.jsx:139-142) ───────
function toquesDe(levels: SrLevels, centro: number): number | null {
  const z = (levels.zonas || []).find(
    (x) => Math.abs(x.centro - centro) < 1e-6
  );
  return z ? z.toques : null;
}

// ── mapa de razones de muerte (port de sp3-data.jsx:188-193) ─
const RAZONES_MUERTE: Record<string, string> = {
  rsi_alto:        'RSI alto — el precio ya no está deprimido',
  encima_sma20:    'Encima de SMA20 — ya recuperó ese nivel',
  encima_sma50:    'Encima de SMA50 — tendencia corta no cae',
  vol_bajo:        'Volumen bajo — sin interés del mercado',
  consol_insuf:    'Consolidación insuficiente — poca calma previa',
  rango_estrecho:  'Rango 30d estrecho — no hay compresión medible',
  pos_alta:        'Posición en rango alta — cotiza en la parte alta',
  drawdown_insuf:  'Caída previa insuficiente — no bajó lo suficiente',
};

// ── tipos ─────────────────────────────────────────────────────
export interface NarrativaProps {
  vida:   ValleyEval | null;
  levels: SrLevels   | null;
  plan:   PlanDerived | null;
}

// ════════════════════════════════════════════════════════════
// VidaBlock — port de sp3-ideaview.jsx:144-184
// ════════════════════════════════════════════════════════════
const VidaBlock: React.FC<{ vida: ValleyEval | null }> = ({ vida }) => {
  if (!vida || vida.estado === 'no_disponible') {
    return (
      <div className={styles['nb']} id="idea-vida">
        <h3 className={styles['nb__h']}>¿Está viva?</h3>
        <p className={styles['nb__p']}>
          No se pudo revisar el estado de la moneda ahora. Puede ser un problema
          de la herramienta — intenta de nuevo en un momento.
        </p>
      </div>
    );
  }

  if (vida.candidata === false) {
    // Rama viva pero no candidata — decisión #3a: SIN número de posición
    if (vida.vivo) {
      return (
        <div className={styles['nb']} id="idea-vida">
          <h3 className={styles['nb__h']}>¿Está viva?</h3>
          <p className={styles['nb__p']}>
            No está en la parte baja de su rango ahora. Está viva, pero hoy
            cotiza en la parte alta de su rango de 30d, así que no entra en
            este filtro.
          </p>
        </div>
      );
    }
    // Rama no viva — lista razones de muerte
    return (
      <div className={styles['nb']} id="idea-vida">
        <h3 className={styles['nb__h']}>¿Está viva?</h3>
        <p className={styles['nb__p']}>
          No está viva mecánicamente ahora. Esto fue lo que se vio:
        </p>
        <ul className={styles['nb__list']}>
          {(vida.razones_muerte || []).map((r) => (
            <li className={styles['nb__li']} key={r}>
              <span className={styles['nb__li-b']}>—</span>
              {RAZONES_MUERTE[r] || r}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  // Candidata = true → viva y en la parte baja de su rango
  return (
    <div className={styles['nb']} id="idea-vida">
      <h3 className={styles['nb__h']}>¿Está viva?</h3>
      <p className={styles['nb__p']}>
        Está viva y en la <b>parte baja de su rango de 30d</b> (posición{' '}
        <b>{vida.pos_in_30d_range != null ? `${Math.round(vida.pos_in_30d_range * 100)}%` : '—'}</b>
        ), por debajo de su SMA20 (
        <b>{vida.pct_vs_sma20 != null ? pct1(vida.pct_vs_sma20) : '—'}</b>
        ), RSI <b>{vida.rsi14 != null ? vida.rsi14.toFixed(1) : '—'}</b>.
      </p>
      {/*VERBATIM AC7*/}
      <div className={`${styles['seam']} ${styles['seam--ac7']}`}>
        <p className={styles['seam__txt']}>
          Esto es la réplica del filtro que usaba el canal de 2019. Medido, no le ganó al azar de alts ni en su mejor régimen (alt-bull 2019: 14d 9.92% vs 12.54%). Lo que movió el retorno fue el régimen, no esta selección. <b>La decisión es tuya.</b>
        </p>
        <div className={styles['seam__evi']}>
          <div className={styles['seam__evi-item']}>
            <div className={styles['seam__evi-v']}>9.92%</div>
            <div className={styles['seam__evi-k']}>el filtro · 14 días</div>
          </div>
          <div className={styles['seam__evi-vs']}>vs</div>
          <div className={styles['seam__evi-item']}>
            <div className={`${styles['seam__evi-v']} ${styles['seam__evi-v--azar']}`}>12.54%</div>
            <div className={styles['seam__evi-k']}>azar de alts · 14 días</div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ════════════════════════════════════════════════════════════
// ParedesBlock — port de sp3-ideaview.jsx:186-221
// ════════════════════════════════════════════════════════════
const ParedesBlock: React.FC<{ levels: SrLevels | null }> = ({ levels }) => {
  if (!levels || levels.estado === 'no_disponible') {
    return (
      <div className={styles['nb']} id="idea-paredes">
        <h3 className={styles['nb__h']}>¿Dónde está entre sus paredes?</h3>
        <p className={styles['nb__p']}>
          No se pudieron calcular los niveles en este momento. Prueba de nuevo
          en un rato.
        </p>
      </div>
    );
  }

  const price = levels.price_live;

  if (!levels.zonas || levels.zonas.length === 0) {
    return (
      <div className={styles['nb']} id="idea-paredes">
        <h3 className={styles['nb__h']}>¿Dónde está entre sus paredes?</h3>
        <p className={styles['nb__p']}>
          Todavía no hay paredes claras: el precio no giró suficientes veces en
          ningún lugar como para marcar una pared.
          {price != null && <> Hoy vale <b>${pr(price)}</b>.</>}
        </p>
      </div>
    );
  }

  const u = levels.ubicacion || {};
  let lead: React.ReactNode;
  if (u.dentro_de && u.dentro_de.tipo === 'soporte') {
    lead = (
      <>
        El precio está sobre un piso que ya giró{' '}
        <b>{u.dentro_de.toques} veces</b> — zona histórica de compradores.
      </>
    );
  } else if (u.dentro_de && u.dentro_de.tipo === 'resistencia') {
    lead = (
      <>
        El precio está contra un techo que ya giró{' '}
        <b>{u.dentro_de.toques} veces</b> — zona donde el precio suele
        frenarse.
      </>
    );
  } else {
    lead = <>El precio está en el medio — no pegado a ninguna pared todavía.</>;
  }

  const techoT = u.techo ? toquesDe(levels, u.techo.centro) : null;
  const pisoT  = u.piso  ? toquesDe(levels, u.piso.centro)  : null;

  return (
    <div className={styles['nb']} id="idea-paredes">
      <h3 className={styles['nb__h']}>¿Dónde está entre sus paredes?</h3>
      <p className={styles['nb__p']}>
        {lead}
        {price != null && <> Precio actual: <b>${pr(price)}</b>.</>}
      </p>
      <ul className={styles['nb__list']}>
        {u.techo && (
          <li className={styles['nb__li']}>
            <span className={styles['nb__li-b']}>▲</span>
            Techo más cercano: <b>${pr(u.techo.centro)}</b>, queda{' '}
            {Math.abs(u.techo.dist_pct).toFixed(1)}% más arriba.
            {techoT != null && <> Ya rebotó {techoT} veces ahí.</>}
          </li>
        )}
        {u.piso && (
          <li className={styles['nb__li']}>
            <span className={styles['nb__li-b']}>▼</span>
            Piso más cercano: <b>${pr(u.piso.centro)}</b>, queda{' '}
            {Math.abs(u.piso.dist_pct).toFixed(1)}% más abajo.
            {pisoT != null && <> Ya rebotó {pisoT} veces ahí.</>}
          </li>
        )}
      </ul>
    </div>
  );
};

// ════════════════════════════════════════════════════════════
// JugadaBlock — port de sp3-ideaview.jsx:223-248
// ════════════════════════════════════════════════════════════
const JugadaBlock: React.FC<{ plan: PlanDerived | null }> = ({ plan }) => {
  const hasPlan = plan != null;

  return (
    <div className={styles['nb']} id="idea-jugada">
      <h3 className={styles['nb__h']}>Si decides entrar, la jugada</h3>
      {!hasPlan ? (
        <p className={styles['nb__p']}>
          Todavía no hay un plan calculado para esta moneda. Puede que falten
          niveles o que la moneda no esté en condición de entrada ahora.
        </p>
      ) : (() => {
          const pl = plan;
          const rungs = pl.rungs;
          const ladderTxt: React.ReactNode =
            rungs.length === 1 ? (
              <>
                Solo hay una pared clara arriba — una salida (
                {Math.round(rungs[0].size_frac * 100)}%) en{' '}
                <b>${pr(rungs[0].tp_price)}</b>.
              </>
            ) : (
              <>
                {rungs.length} peldaños (
                {rungs
                  .map((r) => `${Math.round(r.size_frac * 100)}% a $${pr(r.tp_price)}`)
                  .join(' / ')}
                ). La primera salida es la más grande — sales más donde la pared
                está más cerca.
              </>
            );
          return (
            <ul className={styles['nb__list']}>
              <li className={styles['nb__li']}>
                <span className={styles['nb__li-b']}>●</span>
                {pl.entry_zone ? (
                  <>
                    Zona de entrada: zona{' '}
                    <b>
                      ${pr(pl.entry_zone.precio_bajo)}–${pr(pl.entry_zone.precio_alto)}
                    </b>
                    , donde el precio ya rebotó {pl.entry_zone.toques} veces.
                  </>
                ) : (
                  <>No se identificó una zona de soporte nítida.</>
                )}
              </li>
              <li className={styles['nb__li']}>
                <span className={styles['nb__li-b']}>●</span>
                Stop: <b>${pr(pl.sl_plan)}</b>
                {pl.sl_piso != null && (
                  <>, justo debajo del piso de ${pr(pl.sl_piso.centro)}</>
                )}
                . Es lo máximo que estás dispuesto a perder en esta jugada.
              </li>
              <li className={styles['nb__li']}>
                <span className={styles['nb__li-b']}>●</span>
                Escalera de salidas: {ladderTxt}
              </li>
              {pl.runner_frac > 0 && (
                <li className={styles['nb__li']}>
                  <span className={styles['nb__li-b']}>●</span>
                  Runner: un <b>{Math.round(pl.runner_frac * 100)}%</b> queda
                  abierto sin objetivo. Cuando se llena la primera salida, su
                  stop sube a break-even — a partir de ahí esa parte ya no
                  puede perder.
                </li>
              )}
            </ul>
          );
        })()}
      {/*VERBATIM*/}
      <div className={styles['seam']}>
        Esto sale de tus niveles · <b>la decisión es tuya.</b>
      </div>
    </div>
  );
};

// ── componente principal ──────────────────────────────────────
export const Narrativa: React.FC<NarrativaProps> = ({ vida, levels, plan }) => {
  return (
    <div className={styles['iv-narr']}>
      <VidaBlock vida={vida} />
      <ParedesBlock levels={levels} />
      <JugadaBlock plan={plan} />
    </div>
  );
};
