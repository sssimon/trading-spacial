// ============================================================
// NoticiasSection.tsx — sección "Lo último que se dijo"
//
// V1 (track 2): solo empty-state honesto.
// NO fabrica noticias ni sentimiento.
// ============================================================

import React from 'react';
import styles from './idea.module.css';

export interface NoticiasSectionProps {
  symbol: string;
}

export const NoticiasSection: React.FC<NoticiasSectionProps> = ({
  symbol: _symbol,
}) => {
  return (
    <section id="idea-noticias" className={styles['na-block']}>
      <h3 className={styles['na-heading']}>Lo último que se dijo</h3>
      <p className={styles['na-empty']}>
        Las noticias de esta moneda aún no están conectadas.
      </p>
    </section>
  );
};
