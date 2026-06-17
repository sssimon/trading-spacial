// ============================================================
// JugadaDerivada.tsx — body de la pantalla "La Jugada Derivada"
//
// Muestra la salida ordenada derivada de los niveles, calculada
// al precio vivo de ahora. SIN el chrome/stepper/nav (eso lo
// inyecta ValleysFlow). Equivale al body de JuDerivada del handoff.
// ============================================================

import React from 'react';
import type { PlanDerived } from '../../../types';
import { formatPrice } from '../../../utils';
import { Eyebrow } from '../atoms';
import { LaJugadaChart } from './LaJugadaChart';
import styles from './jugada.module.css';

// ── helpers ────────────────────────────────────────────────
const pr = (n: number | null | undefined): string =>
  n == null ? '—' : formatPrice(n);

/** Formatea una fracción como porcentaje entero: 0.55 → "55%". */
const pct = (frac: number): string => `${Math.round(frac * 100)}%`;

// ── props ──────────────────────────────────────────────────
export interface JugadaDerivadaProps {
  symbol: string;
  plan: PlanDerived;
  live: number;
}

// ── componente ─────────────────────────────────────────────
export const JugadaDerivada: React.FC<JugadaDerivadaProps> = ({
  symbol,
  plan,
  live,
}) => {
  const escaleraCortaPath = plan.rungs.length === 1;
  const zona = plan.entry_zone;

  // ¿el precio vivo cae dentro de la zona de entrada?
  const fueraDeLaZona =
    zona != null && live > zona.precio_alto;

  return (
    <div className={styles['ju-screen']}>
      {/* eyebrow ─────────────────────────────────────────── */}
      <Eyebrow symbol={symbol} />
      <span className={styles['ju-calc']}>
        <i />calculado al precio de ahora
      </span>

      {/* titular ─────────────────────────────────────────── */}
      <h2 className={styles['ju-question']}>
        Si decides entrar, así se sale por partes.
      </h2>

      {/* lede ─────────────────────────────────────────────── */}
      <p className={styles['ju-lede']}>
        Es la salida ordenada que se deriva de <b>tus niveles</b>: dónde
        entrarías, hasta dónde aguantas, y cómo vas saliendo poco a poco
        contra cada techo. Describe una salida,{' '}
        <b>no una orden de comprar</b>.
      </p>

      {/* gráfico ─────────────────────────────────────────── */}
      <LaJugadaChart
        symbol={symbol}
        plan={plan}
        live={live}
        phaseLabel="derivada · al precio de ahora"
      />

      {/* hechos ───────────────────────────────────────────── */}
      <ul className={styles['ju-facts']}>

        {/* 1 — zona de entrada */}
        <li className={styles['ju-fact']}>
          <span className={styles['ju-fact__icon']}>▭</span>
          <div>
            <div className={styles['ju-fact__k']}>
              Dónde entrarías — una zona, no un punto
            </div>
            <div className={styles['ju-fact__v']}>
              {zona != null ? (
                <>
                  La franja de soporte{' '}
                  <b>
                    ${pr(zona.precio_bajo)}–${pr(zona.precio_alto)}
                  </b>
                  , donde el precio ya rebotó{' '}
                  <b>{zona.toques} veces</b>.{' '}
                  {fueraDeLaZona ? (
                    <>
                      El precio de ahora está{' '}
                      <b>por encima de tu zona</b> — no estás dentro.
                    </>
                  ) : (
                    <>El precio de ahora cae dentro de ella.</>
                  )}
                </>
              ) : (
                <>No se identificó una zona de soporte nítida.</>
              )}
            </div>
          </div>
        </li>

        {/* 2 — stop */}
        <li className={styles['ju-fact']}>
          <span
            className={`${styles['ju-fact__icon']} ${styles['ju-fact__icon--ochre']}`}
          >
            ↧
          </span>
          <div>
            <div className={styles['ju-fact__k']}>
              Hasta dónde aguantas — el stop
            </div>
            <div className={styles['ju-fact__v']}>
              <b>${pr(plan.sl_plan)}</b>
              {plan.sl_piso != null && (
                <>, justo debajo del piso de ${pr(plan.sl_piso.precio_bajo)}</>
              )}
              . Es lo que estás dispuesto a perder, no una garantía.
            </div>
          </div>
        </li>

        {/* 3 — escalera */}
        <li className={styles['ju-fact']}>
          <span className={styles['ju-fact__icon']}>⋮</span>
          <div>
            <div className={styles['ju-fact__k']}>
              Cómo sales por partes — la escalera
            </div>
            <div className={styles['ju-fact__v']}>
              {escaleraCortaPath ? (
                <>
                  Solo hay <b>una pared clara</b> arriba: la primera salida (
                  {pct(plan.rungs[0].size_frac)}) en{' '}
                  <b>${pr(plan.rungs[0].tp_price)}</b>. No hay más techos
                  para escalonar — la jugada lo dice honesto.
                </>
              ) : (
                <>
                  Cada peldaño se apoya sobre un techo nombrado. La{' '}
                  <b>primera salida es la más grande</b> (
                  {plan.rungs.map((r) => pct(r.size_frac)).join(' / ')}
                  ): sales más donde la pared está más cerca.
                </>
              )}
            </div>
          </div>
        </li>

        {/* 4 — runner */}
        <li className={styles['ju-fact']}>
          <span
            className={`${styles['ju-fact__icon']} ${styles['ju-fact__icon--sage']}`}
          >
            →
          </span>
          <div>
            <div className={styles['ju-fact__k']}>
              Lo que dejas corriendo — el runner
            </div>
            <div className={styles['ju-fact__v']}>
              Un <b>{pct(plan.runner_frac)}</b> queda abierto, sin objetivo.
              Cuando se llena la primera salida, su stop sube a break-even:{' '}
              <b>a partir de ahí esa parte ya no puede perder</b>.
            </div>
          </div>
        </li>

      </ul>

      {/* procedencia ──────────────────────────────────────── */}
      <div className={styles['ju-prov']}>
        <b>Esto se arma con tus niveles</b>, las paredes de D.1 — no es un
        consejo de comprar. Cada número trae su pared: la entrada sale del
        soporte, el stop del piso, cada salida de una resistencia. La
        decisión de entrar es tuya.
      </div>
    </div>
  );
};
