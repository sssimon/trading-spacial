import React from 'react';
import type { PlanConducta, PlanConductaField } from '../../../types';
import { Eyebrow } from '../atoms';
import styles from './jugada.module.css';

// Marca visual y clase de tono por valor de 'ok'
const CF_MARK: Record<PlanConductaField['ok'], string> = {
  si:   '✓',
  no:   '○',
  dato: '·',
};

interface Props {
  conducta: PlanConducta;
}

export const JugadaConducta: React.FC<Props> = ({ conducta }) => {
  // El wrapper garantiza que solo se llega acá con cerrado + campos,
  // pero el contrato pide que el componente lo valide él mismo.
  if (conducta.estado_vivo !== 'cerrado' || !conducta.campos) return null;

  const { titular, campos } = conducta;

  return (
    <div className={styles['ju-screen']}>
      {/* Eyebrow compartido — nombre del instrumento */}
      <Eyebrow symbol={conducta.symbol} />
      {/* Pastilla "cerrada" — específica de esta pantalla */}
      <span className={`${styles['ju-livestate']} ${styles['ju-livestate--cerrado']}`}>
        <i />cerrada
      </span>

      {/* Pregunta principal */}
      <h2 className={styles['ju-question']}>¿Honraste tu plan?</h2>

      {/* Bloque de conducta */}
      <div className={styles['ju-conduct']}>
        {/* Titular narrativo: viene del backend, describe lo que pasó */}
        {titular && (
          <div className={styles['ju-conduct__lead']}>{titular}</div>
        )}

        {/* Bajada: contextualiza sin PnL ni juicio */}
        <p className={styles['ju-conduct__say']}>
          La jugada cerró. Esto es campo por campo lo que hiciste contra la ley que tú mismo aprobaste —{' '}
          <b>sin ganancia ni pérdida, sin nota</b>. No hay reproche: solo el espejo.
        </p>

        {/* Lista de campos */}
        <div className={styles['ju-cfields']}>
          {campos.map((campo, i) => (
            <div
              key={i}
              className={`${styles['ju-cf']} ${styles[`ju-cf--${campo.ok}`]}`}
            >
              <span className={styles['ju-cf__mark']}>{CF_MARK[campo.ok]}</span>
              <span className={styles['ju-cf__k']}>{campo.k}</span>
              {campo.v != null && (
                <span className={styles['ju-cf__v']}>{campo.v}</span>
              )}
            </div>
          ))}
        </div>

        {/* Bloque espejo — sin PnL, sin juicio, tuteo */}
        <div className={styles['ju-mirror']}>
          Es un <b>espejo, no un juez</b>. Un trade puede perder y ser conducta perfecta; o ganar y ser
          conducta pobre. Acá no hay PnL ni un número de "calidad" — solo los hechos de tu propia
          conducta, para que te veas.
        </div>
      </div>
    </div>
  );
};
