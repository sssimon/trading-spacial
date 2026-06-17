// JugadaLens.tsx — 4ta lente de Valles: selecciona la pantalla correcta
// según el estado del ciclo de vida de la jugada.
//
// Prioridad:
//   1. live.estado_vivo === 'activo' | 'incierto'  → JugadaLive
//   2. derived.data con escalera                   → JugadaDerivada + botón gate
//      showGate                                    → JugadaGate
//   3. conducta.estado_vivo === 'cerrado'           → JugadaConducta
//   4. fallback                                    → JugadaSinJugada

import React, { useEffect, useState } from 'react';
import { useJugada } from './useJugada';
import { JugadaDerivada } from './JugadaDerivada';
import { JugadaGate } from './JugadaGate';
import { JugadaLive } from './JugadaLive';
import { JugadaConducta } from './JugadaConducta';
import { JugadaSinJugada } from './JugadaSinJugada';
import styles from './jugada.module.css';

export interface JugadaLensProps {
  symbol: string;
  livePrice: number | null;
}

export const JugadaLens: React.FC<JugadaLensProps> = ({ symbol, livePrice }) => {
  const { derived, live, conducta } = useJugada(symbol, livePrice);
  const [showGate, setShowGate] = useState(false);

  // Resetea el gate cada vez que cambia el símbolo
  useEffect(() => {
    setShowGate(false);
  }, [symbol]);

  // Estado de carga inicial: ambas peticiones en vuelo
  if (derived.loading || live.loading) {
    return (
      <div className={styles['ju-screen']}>
        <p>Derivando la jugada de tus niveles…</p>
      </div>
    );
  }

  // 1. Jugada activa o incierta → plano vivo
  if (
    live.data?.estado_vivo === 'activo' ||
    live.data?.estado_vivo === 'incierto'
  ) {
    return <JugadaLive symbol={symbol} live={live.data} />;
  }

  // 2. Derivada con escalera → mostrar derivada + botón para ir al gate
  if (derived.data && derived.data.rungs.length > 0) {
    if (showGate) {
      return (
        <JugadaGate
          symbol={symbol}
          plan={derived.data}
          entry={livePrice ?? derived.data.entry}
        />
      );
    }
    return (
      <>
        <JugadaDerivada
          symbol={symbol}
          plan={derived.data}
          live={livePrice ?? derived.data.entry}
        />
        <button
          className={`${styles['ju-btn']} ${styles['ju-btn--primary']}`}
          onClick={() => setShowGate(true)}
        >
          Revisar y fijar →
        </button>
      </>
    );
  }

  // 3. Jugada cerrada → conducta
  if (conducta.data?.estado_vivo === 'cerrado') {
    return <JugadaConducta conducta={conducta.data} />;
  }

  // 4. Fallback honesto
  return <JugadaSinJugada symbol={symbol} />;
};
