// ============================================================
// NoticiasSection.tsx — sección "Lo último que se dijo"
//
// V1 (track 2): solo empty-state honesto.
// NO fabrica noticias ni sentimiento.
// Estructura preparada para {titular, fuente, url, fecha}
// cuando el backend de noticias aterrice.
// ============================================================

import React from 'react';
import styles from './idea.module.css';

export interface NoticiaItem {
  titular: string;
  fuente:  string;
  url:     string;
  fecha:   string;
}

export interface NoticiasSectionProps {
  symbol: string;
  /** Track 2: el backend de noticias no existe aún. Siempre undefined en V1. */
  noticias?: NoticiaItem[];
}

export const NoticiasSection: React.FC<NoticiasSectionProps> = ({
  symbol: _symbol,
  noticias,
}) => {
  return (
    <section id="idea-noticias" className={styles['na-block']}>
      <h3 className={styles['na-heading']}>Lo último que se dijo</h3>

      {noticias && noticias.length > 0 ? (
        // Estructura reservada para cuando llegue el backend de noticias
        // (track 2 — no se usa en V1)
        <ul className={styles['na-list']}>
          {noticias.map((n, i) => (
            <li key={i}>
              <a href={n.url} target="_blank" rel="noopener noreferrer">
                {n.titular}
              </a>{' '}
              — {n.fuente} · {n.fecha}
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles['na-empty']}>
          Todavía no traemos las noticias de esta moneda.
        </p>
      )}
    </section>
  );
};
