// ============================================================
// JugadaSinJugada.tsx — body de JuSinJugada.
//
// Vacío honesto: la moneda no tiene estructura para derivar
// una jugada. Se muestra el hecho sin inventar un plan.
// Sin props de plan ni de live — solo el símbolo.
// ============================================================

import React from 'react';
import { Eyebrow } from '../atoms';
import styles from './jugada.module.css';

export interface JugadaSinJugadaProps {
  symbol: string;
}

export const JugadaSinJugada: React.FC<JugadaSinJugadaProps> = ({ symbol }) => (
  <div className={styles['ju-screen']}>
    {/* eyebrow */}
    <Eyebrow symbol={symbol} />

    {/* titular */}
    <h2 className={styles['ju-question']}>Esta moneda no da estructura para una jugada.</h2>

    {/* aviso principal */}
    <div className={styles['ju-warn']} style={{ marginTop: 24 }}>
      <span className={styles['ju-warn__icon']}>▢</span>
      <div>
        <div className={styles['ju-warn__t']}>Sin paredes claras, no hay salida que escalonar</div>
        <div className={styles['ju-warn__s']}>
          No aparece un soporte nítido para la entrada ni techos donde apoyar las salidas.{' '}
          <b>No se inventa una jugada</b> para llenar la pantalla — cuando no hay geometría, no hay plan.
        </div>
      </div>
    </div>

    {/* procedencia */}
    <div className={styles['ju-prov']}>
      La jugada se deriva de Niveles. Si Niveles no encontró paredes, el Instrumento se queda callado — eso
      también es un hecho honesto sobre la moneda.
    </div>
  </div>
);
