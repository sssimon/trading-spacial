// JugadaGate.tsx — body de JuGate + JuGateFijada (sin chrome/wrapper JuFrame).
// Props: { symbol, plan, entry }
// Estado interno: fijada / enviando / error

import React, { useState } from 'react';
import type { PlanDerived } from '../../../types';
import { confirmPlan } from '../../../api';
import { Eyebrow } from '../atoms';
import styles from './jugada.module.css';

// ── Helpers ────────────────────────────────────────────────────────────────

const JNAMES: Record<string, string> = {
  ADAUSDT: 'Cardano',
  JUPUSDT: 'Jupiter',
  XLMUSDT: 'Stellar',
  RUNEUSDT: 'THORChain',
  ZBCUSDT: 'Zebec',
};

function jnm(sym: string): string {
  return JNAMES[sym] ?? sym.replace('USDT', '');
}

function pr(n: number | null | undefined): string {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { maximumFractionDigits: 6 });
}

function jPct(frac: number): string {
  return `${Math.round(frac * 100)}%`;
}

// ── Props ──────────────────────────────────────────────────────────────────

export interface JugadaGateProps {
  symbol: string;
  plan: PlanDerived;
  entry: number;
}

// ── Componente ─────────────────────────────────────────────────────────────

export const JugadaGate: React.FC<JugadaGateProps> = ({ symbol, plan, entry }) => {
  const [fijada, setFijada] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFijar() {
    setEnviando(true);
    setError(null);
    try {
      await confirmPlan(symbol, entry);
      setFijada(true);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : 'No se pudo fijar la jugada. Revisa tu conexión e intenta de nuevo.'
      );
    } finally {
      setEnviando(false);
    }
  }

  // ── Vista: ya fijada ─────────────────────────────────────────────────────

  if (fijada) {
    return (
      <div className={styles['ju-screen']}>
        {/* Eyebrow */}
        <Eyebrow symbol={symbol} />
        {/* Pastilla de estado — específica de esta vista */}
        <span className={`${styles['ju-livestate']} ${styles['ju-livestate--activo']}`}>
          <i />en curso
        </span>

        {/* Titular */}
        <h2 className={styles['ju-question']}>
          Jugada fijada. Desde acá se sigue en vivo.
        </h2>

        {/* Bloque verde fijada */}
        <div className={styles['ju-fixed']}>
          <span className={styles['ju-fixed__icon']}>⌖</span>
          <div>
            <div className={styles['ju-fixed__t']}>
              El Instrumento guardó tu plan como ley.
            </div>
            <div className={styles['ju-fixed__s']}>
              No se ejecutó ninguna orden —{' '}
              <b>la entrada la haces tú en Binance</b>. A partir de ahora, el
              plano vivo te muestra cómo va tu jugada contra este plan, y al
              cierre puedes leer si lo honraste.
            </div>
          </div>
        </div>

        {/* Procedencia */}
        <div className={styles['ju-prov']}>
          El plano vivo es <b>solo lectura y se mira cuando tú quieres</b> — sin
          avisos, sin notificaciones. Se refresca a su propio ritmo (cada pocos
          minutos), no en tiempo real.
        </div>
      </div>
    );
  }

  // ── Vista: gate (pre-confirmación) ──────────────────────────────────────

  return (
    <div className={styles['ju-screen']}>
      {/* Eyebrow */}
      <Eyebrow symbol={symbol} />

      {/* Titular */}
      <h2 className={styles['ju-question']}>Fija la jugada, en frío.</h2>

      {/* Lede */}
      <p className={styles['ju-lede']}>
        Confirmar <b>no compra nada</b>. Solo le dice al Instrumento cuál es la
        ley que vas a honrar — para que, al cierre, pueda mostrarte si la
        cumpliste. La entrada la haces tú, por tu flujo normal en Binance.
      </p>

      {/* Gate card */}
      <div className={styles['ju-gate']}>
        <div className={styles['ju-gate__hd']}>
          <div className={styles['ju-gate__hd-k']}>La ley que vas a fijar</div>
          <div className={styles['ju-gate__hd-t']}>
            {jnm(symbol)} · salida ordenada contra las paredes
          </div>
        </div>

        <div className={styles['ju-gate__rows']}>
          {/* Zona de entrada */}
          <div className={styles['ju-grow']}>
            <div className={styles['ju-grow__k']}>Zona de entrada (rango)</div>
            <div className={styles['ju-grow__v']}>
              {plan.entry_zone
                ? `$${pr(plan.entry_zone.precio_bajo)} – $${pr(plan.entry_zone.precio_alto)}`
                : `$${pr(entry)}`}
            </div>
            <div className={styles['ju-grow__note']}>
              {plan.entry_zone
                ? `soporte · ${plan.entry_zone.toques} toques`
                : 'precio de entrada'}
            </div>
          </div>

          {/* Stop */}
          <div className={styles['ju-grow']}>
            <div className={styles['ju-grow__k']}>Stop · hasta dónde aguantas</div>
            <div
              className={`${styles['ju-grow__v']} ${styles['ju-grow__v--ochre']}`}
            >
              ${pr(plan.sl_plan)}
            </div>
            <div className={styles['ju-grow__note']}>
              {plan.sl_piso
                ? `bajo el piso de $${pr(plan.sl_piso.centro)}`
                : 'nivel de stop'}
            </div>
          </div>

          {/* Mini-escalera de salidas */}
          <div className={`${styles['ju-grow']} ${styles['ju-grow--full']}`}>
            <div className={styles['ju-grow__k']}>
              Escalera de salidas — primera la más grande
            </div>
            <div className={styles['ju-ladder-mini']}>
              {plan.rungs.map((r, i) => (
                <span className={styles['ju-lm']} key={i}>
                  <span className={styles['ju-lm__pct']}>salida {i + 1}</span>
                  <b>${pr(r.tp_price)}</b>
                  <span className={styles['ju-lm__pct']}>{jPct(r.size_frac)}</span>
                </span>
              ))}
              <span className={styles['ju-lm']}>
                <span className={styles['ju-lm__pct']}>runner</span>
                <b>↑ abierto</b>
                <span className={styles['ju-lm__pct']}>{jPct(plan.runner_frac)}</span>
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Disclaimer ocre */}
      <div className={styles['ju-disc']}>
        <span className={styles['ju-disc__icon']}>⚑</span>
        <div className={styles['ju-disc__t']}>
          <b>Confirmar no ejecuta.</b> No hay botón de comprar acá. Fijar la
          jugada solo guarda este plan como la ley contra la que se va a medir
          tu conducta. El stop "no puede perder" recién aplica cuando mueves a
          break-even.
        </div>
      </div>

      {/* Error (si ocurrió) */}
      {error && (
        <div className={styles['ju-warn']} style={{ marginTop: 14 }}>
          <span className={styles['ju-warn__icon']}>⚠</span>
          <div>
            <div className={styles['ju-warn__t']}>No se pudo fijar</div>
            <div className={styles['ju-warn__s']}>{error}</div>
          </div>
        </div>
      )}

      {/* Botón primario + hint */}
      <div className={styles['ju-confirm']}>
        <button
          className={`${styles['ju-btn']} ${styles['ju-btn--primary']} ${styles['ju-btn--block']}`}
          onClick={handleFijar}
          disabled={enviando}
        >
          {enviando ? 'Fijando…' : 'Fijar esta jugada'}
        </button>
        <div className={styles['ju-confirm__hint']}>
          Puedes cerrar esto sin fijar nada. Fijar es solo tuyo, a propósito.
        </div>
      </div>
    </div>
  );
};
