// ============================================================
// FreshnessTag — la frescura del dato, honesta. fresco/rancio/
// muerto. Liveness operacional: el dato dice su edad; el sistema
// no finge frescura. Spec §3.
// ============================================================
import React from 'react';
import type { Frescura } from '../types';
import styles from './FreshnessTag.module.css';

function _hace(edad_seg: number | null): string {
  if (edad_seg == null) return '';
  const h = edad_seg / 3600;
  if (h < 1) return `hace ${Math.round(edad_seg / 60)} min`;
  if (h < 48) return `hace ${Math.round(h)} h`;
  return `hace ${Math.round(h / 24)} dias`;
}

export const FreshnessTag: React.FC<{ frescura?: Frescura }> = ({ frescura }) => {
  if (!frescura) return null;
  if (frescura.estado === 'muerto') {
    return <span className={`${styles.tag} ${styles.muerto}`}>sin foto — el screener no ha corrido</span>;
  }
  if (frescura.estado === 'rancio') {
    return <span className={`${styles.tag} ${styles.rancio}`}>foto {_hace(frescura.edad_seg)} · rancia</span>;
  }
  return <span className={`${styles.tag} ${styles.fresco}`}>foto {_hace(frescura.edad_seg)}</span>;
};
