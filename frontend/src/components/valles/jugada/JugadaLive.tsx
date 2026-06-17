// ============================================================
// JugadaLive.tsx — body de JuLive (la jugada en curso / plano vivo).
//
// Props: { symbol: string; live: PlanLive }
// El wrapper garantiza que live.estado_vivo no es null y live.plan
// existe antes de renderizar esta pantalla. Aún así se guarda con
// el return null para el caso borde donde plan llegue indefinido.
//
// Render 1:1 con JuLive del handoff jugada-screens.jsx.
// Copy remapeado a tuteo venezolano (jamás voseo).
// ============================================================

import React from 'react';
import type { PlanLive } from '../../../types';
import { Eyebrow } from '../atoms';
import { LaJugadaChart } from './LaJugadaChart';
import type { LiveState } from './overlays';
import styles from './jugada.module.css';

// ── helpers de formato ─────────────────────────────────────

/** Formatea edad_seg en "hace N min" / "hace N h" / "hace N seg".
 *  Si edad_seg es null o 0 devuelve "ahora mismo". */
function formatEdad(seg: number | null): string {
  if (seg == null || seg <= 0) return 'ahora mismo';
  if (seg < 60) return `hace ${Math.round(seg)} seg`;
  if (seg < 3600) return `hace ${Math.round(seg / 60)} min`;
  return `hace ${Math.round(seg / 3600)} h`;
}

// ── tipos locales de props ─────────────────────────────────

interface JugadaLiveProps {
  symbol: string;
  live: PlanLive;
}

// ── componente ─────────────────────────────────────────────

export const JugadaLive: React.FC<JugadaLiveProps> = ({ symbol, live }) => {
  if (!live.plan) return null;

  const plan    = live.plan;
  const incierto = live.estado_vivo === 'incierto';
  const rancio  = live.frescura?.estado === 'rancio';

  // LiveState para el gráfico
  const liveState: LiveState = {
    rungs_llenos: live.realidad?.rungs_llenos ?? [],
    be_movido:    !!live.realidad?.be_movido,
    sl_actual:    live.realidad?.sl_actual ?? null,
  };

  return (
    <div className={styles['ju-screen']}>
      <Eyebrow symbol={symbol} />
      {/* ── cabecera del plano vivo ── */}
      <div className={styles['ju-livehead']}>
        {/* pill de estado */}
        <span
          className={[
            styles['ju-livestate'],
            styles[incierto ? 'ju-livestate--incierto' : 'ju-livestate--activo'],
          ].join(' ')}
        >
          <i />{incierto ? 'estado incierto' : 'en curso'}
        </span>

        {/* tag de frescura */}
        {live.frescura && (
          <span
            className={[
              styles['ju-livefresh'],
              styles[rancio ? 'ju-livefresh--rancio' : 'ju-livefresh--fresco'],
            ].join(' ')}
          >
            <i />actualizado {formatEdad(live.frescura.edad_seg)}
            {rancio && ' · puede haber cambiado'}
          </span>
        )}

        {/* etiqueta solo-lectura */}
        <span className={styles['ju-pull']}>◔ solo lectura · sin avisos</span>
      </div>

      {/* ── titular ── */}
      <h2 className={styles['ju-question']}>Cómo va tu jugada, en hechos.</h2>

      {/* ── gráfico ── */}
      <LaJugadaChart
        symbol={symbol}
        plan={plan}
        live={plan.entry}
        state={liveState}
        phaseLabel="en curso"
        height={452}
      />

      {/* ── lista de hechos vivos ── */}
      {live.hechos && live.hechos.length > 0 && (
        <ul className={styles['ju-state-facts']}>
          {/* Divergencia aceptada del handoff: el backend entrega hechos como string[] ya redactados (sin campo 'tono'), así que el dot del li queda neutro — el contrato /plan no expone tono. */}
          {live.hechos.map((h, i) => (
            <li className={styles['ju-sf']} key={i}>
              <span className={styles['ju-sf__dot']} />
              <div className={styles['ju-sf__t']}>{h}</div>
            </li>
          ))}
        </ul>
      )}

      {/* ── aviso: estado incierto ── */}
      {incierto && (
        <div className={styles['ju-warn']}>
          <span className={styles['ju-warn__icon']}>⚠</span>
          <div>
            <div className={styles['ju-warn__t']}>Transición sin confirmar</div>
            <div className={styles['ju-warn__s']}>
              El último plano llegó a medias y no se pudo confirmar el estado.{' '}
              <b>Revisa en Binance</b> antes de asumir nada — el Instrumento no adivina.
            </div>
          </div>
        </div>
      )}

      {/* ── aviso: datos rancios ── */}
      {rancio && (
        <div className={styles['ju-warn']}>
          <span className={styles['ju-warn__icon']}>⧖</span>
          <div>
            <div className={styles['ju-warn__t']}>Este plano es de hace un rato…</div>
            <div className={styles['ju-warn__s']}>
              Lo que ves es de{' '}
              <b>{formatEdad(live.frescura?.edad_seg ?? null)}</b>. El seguimiento corre
              cada pocos minutos; pudo cambiar desde la última foto.
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
