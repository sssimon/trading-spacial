// ============================================================
// Narrativa.tsx — tres bloques anclados descriptivos:
//   #idea-vida      ¿Está viva?
//   #idea-paredes   ¿Dónde está entre sus paredes?
//   #idea-jugada    Si decides entrar, la jugada
//
// Props: { vida, levels, plan } — cualquiera puede ser null
// (empty-state honesto, nunca fabrica datos).
// Copy en tuteo venezolano. Costura obligatoria en bloque jugada.
// ============================================================

import React from 'react';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';
import { formatPrice } from '../../../utils';
import styles from './idea.module.css';

// ── helper ──────────────────────────────────────────────────
const pr = (n: number | null | undefined): string =>
  n == null ? '—' : formatPrice(n);

const pct = (frac: number): string => `${Math.round(frac * 100)}%`;

// ── types ───────────────────────────────────────────────────
export interface NarrativaProps {
  vida:   ValleyEval | null;
  levels: SrLevels   | null;
  plan:   PlanDerived | null;
}

// ── bloque: ¿Está viva? ─────────────────────────────────────
const BloqueVida: React.FC<{ vida: ValleyEval | null }> = ({ vida }) => {
  if (vida == null || vida.estado === 'no_disponible') {
    return (
      <p className={styles['na-empty']}>
        No se pudo revisar el estado de la moneda ahora. Puede ser un problema
        de la herramienta — intenta de nuevo en un momento.
      </p>
    );
  }

  if (vida.candidata === false) {
    return (
      <p className={styles['na-empty']}>
        No está en la parte baja de su rango ahora.
      </p>
    );
  }

  // Candidata = true → viva y en la parte baja de su rango
  const pos = vida.pos_in_30d_range != null ? `${Math.round(vida.pos_in_30d_range * 100)}%` : '—';
  const vsSma = vida.pct_vs_sma20 != null ? `${vida.pct_vs_sma20.toFixed(1)}%` : '—';
  const rsi = vida.rsi14 != null ? vida.rsi14.toFixed(0) : '—';

  return (
    <>
      <p className={styles['na-body']}>
        Está viva y en la <b>parte baja de su rango de 30d</b> (posición <b>{pos}</b>),
        por debajo de su SMA20 (<b>{vsSma}</b>), RSI <b>{rsi}</b>.
      </p>
      <p className={styles['na-costura']}>
        Esto es la réplica del filtro que usaba el canal de 2019. Medido, no le ganó al
        azar de alts ni en su mejor régimen (alt-bull 2019: 14d 9.92% vs 12.54%). Lo que
        movió el retorno fue el régimen, no esta selección. La decisión es tuya.
      </p>
    </>
  );
};

// ── bloque: ¿Dónde está entre sus paredes? ──────────────────
const BloqueParedes: React.FC<{ levels: SrLevels | null }> = ({ levels }) => {
  if (levels == null || levels.estado === 'no_disponible') {
    return (
      <p className={styles['na-empty']}>
        No se pudieron calcular los niveles en este momento.
        Prueba de nuevo en un rato.
      </p>
    );
  }

  if (levels.zonas.length === 0) {
    return (
      <p className={styles['na-empty']}>
        Todavía no hay paredes claras: el precio no giró suficientes veces
        en ningún lugar como para marcar una pared.
        {levels.price_live != null && (
          <> Hoy vale <b>${pr(levels.price_live)}</b>.</>
        )}
      </p>
    );
  }

  const u = levels.ubicacion;
  const { techo, piso, dentro_de: dentro } = u;

  // Descripción de la ubicación
  let ubicacionTexto: React.ReactNode;
  if (dentro && dentro.tipo === 'soporte') {
    ubicacionTexto = (
      <>
        El precio está <b>sobre un piso</b> que ya giró{' '}
        <b>{dentro.toques} veces</b> — zona histórica de compradores.
      </>
    );
  } else if (dentro && dentro.tipo === 'resistencia') {
    ubicacionTexto = (
      <>
        El precio está <b>contra un techo</b> que ya giró{' '}
        <b>{dentro.toques} veces</b> — zona donde el precio suele frenarse.
      </>
    );
  } else {
    ubicacionTexto = (
      <>
        El precio está <b>en el medio</b> — no pegado a ninguna pared todavía.
      </>
    );
  }

  return (
    <div className={styles['na-body']}>
      <p>
        {ubicacionTexto}
        {levels.price_live != null && (
          <> Precio actual: <b>${pr(levels.price_live)}</b>.</>
        )}
      </p>
      {(techo || piso) && (
        <ul className={styles['na-list']}>
          {techo && (
            <li>
              <b>Techo más cercano</b>: ${pr(techo.centro)},{' '}
              queda {techo.dist_pct.toFixed(1)}% más arriba.{' '}
              {(() => {
                const z = levels.zonas.find(z => z.tipo === 'resistencia' && Math.abs(z.centro - techo.centro) < 0.001);
                return z ? <>Ya rebotó <b>{z.toques} veces</b> ahí.</> : null;
              })()}
            </li>
          )}
          {piso && (
            <li>
              <b>Piso más cercano</b>: ${pr(piso.centro)},{' '}
              queda {Math.abs(piso.dist_pct).toFixed(1)}% más abajo.{' '}
              {(() => {
                const z = levels.zonas.find(z => z.tipo === 'soporte' && Math.abs(z.centro - piso.centro) < 0.001);
                return z ? <>Ya rebotó <b>{z.toques} veces</b> ahí.</> : null;
              })()}
            </li>
          )}
        </ul>
      )}
    </div>
  );
};

// ── bloque: Si decides entrar, la jugada ────────────────────
const BloqueJugada: React.FC<{ plan: PlanDerived | null }> = ({ plan }) => {
  if (plan == null) {
    return (
      <>
        <p className={styles['na-empty']}>
          Todavía no hay un plan calculado para esta moneda. Puede que falten
          niveles o que la moneda no esté en condición de entrada ahora.
        </p>
        <p className={styles['na-costura']}>
          Esto sale de tus niveles · la decisión es tuya.
        </p>
      </>
    );
  }

  const zona = plan.entry_zone;
  const escaleraCortaPath = plan.rungs.length === 1;

  return (
    <>
      <ul className={styles['na-list']}>
        {/* Zona de entrada */}
        <li>
          <b>Zona de entrada</b>:{' '}
          {zona != null ? (
            <>
              zona{' '}
              <b>${pr(zona.precio_bajo)}–${pr(zona.precio_alto)}</b>,
              donde el precio ya rebotó <b>{zona.toques} veces</b>.
            </>
          ) : (
            <>No se identificó una zona de soporte nítida.</>
          )}
        </li>

        {/* Stop */}
        <li>
          <b>Stop</b>: <b>${pr(plan.sl_plan)}</b>
          {plan.sl_piso != null && (
            <>, justo debajo del piso de ${pr(plan.sl_piso.precio_bajo)}</>
          )}
          . Es lo máximo que estás dispuesto a perder en esta jugada.
        </li>

        {/* Escalera */}
        <li>
          <b>Escalera de salidas</b>:{' '}
          {escaleraCortaPath ? (
            <>
              Solo hay una pared clara arriba — una salida (
              {pct(plan.rungs[0].size_frac)}) en{' '}
              <b>${pr(plan.rungs[0].tp_price)}</b>.
            </>
          ) : (
            <>
              {plan.rungs.length} peldaños (
              {plan.rungs.map((r, i) => (
                <React.Fragment key={i}>
                  {i > 0 && ' / '}
                  {pct(r.size_frac)} a <b>${pr(r.tp_price)}</b>
                </React.Fragment>
              ))}
              ). La primera salida es la más grande — sales más donde la
              pared está más cerca.
            </>
          )}
        </li>

        {/* Runner */}
        <li>
          <b>Runner</b>: un <b>{pct(plan.runner_frac)}</b> queda abierto sin
          objetivo. Cuando se llena la primera salida, su stop sube a
          break-even — a partir de ahí esa parte ya no puede perder.
        </li>
      </ul>

      {/* Costura obligatoria */}
      <p className={styles['na-costura']}>
        Esto sale de tus niveles · la decisión es tuya.
      </p>
    </>
  );
};

// ── componente principal ─────────────────────────────────────
export const Narrativa: React.FC<NarrativaProps> = ({ vida, levels, plan }) => {
  return (
    <div className={styles['na-root']}>

      {/* ── Bloque 1: ¿Está viva? ──────────────────────────── */}
      <section id="idea-vida" className={styles['na-block']}>
        <h3 className={styles['na-heading']}>¿Está viva?</h3>
        <BloqueVida vida={vida} />
      </section>

      {/* ── Bloque 2: ¿Dónde está entre sus paredes? ─────── */}
      <section id="idea-paredes" className={styles['na-block']}>
        <h3 className={styles['na-heading']}>¿Dónde está entre sus paredes?</h3>
        <BloqueParedes levels={levels} />
      </section>

      {/* ── Bloque 3: Si decides entrar, la jugada ─────────── */}
      <section id="idea-jugada" className={styles['na-block']}>
        <h3 className={styles['na-heading']}>Si decides entrar, la jugada</h3>
        <BloqueJugada plan={plan} />
      </section>

    </div>
  );
};
