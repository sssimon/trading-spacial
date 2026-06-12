// ============================================================
// LevelsPanel — niveles S/R NEUTRALES de un símbolo (D.1).
// Sin veredicto, sin badge de compra: bandas de pivotes + dónde
// está el precio vivo. Spec D.1 §5.
// ============================================================
import React from 'react';
import type { SrLevels, SrZona } from '../types';
import { formatPrice } from '../utils';
import styles from './LevelsPanel.module.css';

const Banda: React.FC<{ z: SrZona }> = ({ z }) => (
  <div className={styles.row}>
    <span className={styles.banda}>
      {formatPrice(z.precio_bajo)} – {formatPrice(z.precio_alto)}
    </span>
    <span className={styles.centro}>centro {formatPrice(z.centro)}</span>
    <span className={styles.toques}>{z.toques} toques</span>
    {z.confluencia_redondo.length > 0 && (
      <span className={styles.redondo}>
        redondo {z.confluencia_redondo.map((r) => formatPrice(r)).join(', ')}
      </span>
    )}
  </div>
);

export const LevelsPanel: React.FC<{ levels: SrLevels; loading: boolean }> = ({ levels, loading }) => {
  if (loading) return <div className={styles.empty}>Calculando niveles…</div>;
  const l = levels;
  if (l.estado === 'no_disponible') {
    return <div className={styles.empty}>Sin datos ahora. Probá de nuevo.</div>;
  }

  const resistencias = l.zonas.filter((z) => z.tipo === 'resistencia').sort((a, b) => b.centro - a.centro);
  const soportes = l.zonas.filter((z) => z.tipo === 'soporte').sort((a, b) => b.centro - a.centro);

  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <span className={styles.sym}>{l.symbol.replace('USDT', '')}</span>
        {l.price_live != null && <span className={styles.price}>precio {formatPrice(l.price_live)}</span>}
        {l.generated_at && (
          <span className={styles.ts}>{new Date(l.generated_at).toLocaleTimeString('es-ES')}</span>
        )}
      </header>

      {resistencias.length > 0 && (
        <section className={styles.sec}>
          <h4>Resistencias</h4>
          {resistencias.map((z, i) => <Banda key={i} z={z} />)}
        </section>
      )}

      <div className={styles.locator}>
        {l.ubicacion.dentro_de ? (
          <span>Precio dentro de la zona {formatPrice(l.ubicacion.dentro_de.centro)}</span>
        ) : (
          <>
            <span>techo {l.ubicacion.techo
              ? `${formatPrice(l.ubicacion.techo.centro)} (+${l.ubicacion.techo.dist_pct}%)` : '—'}</span>
            <span>piso {l.ubicacion.piso
              ? `${formatPrice(l.ubicacion.piso.centro)} (${l.ubicacion.piso.dist_pct}%)` : '—'}</span>
          </>
        )}
      </div>

      {soportes.length > 0 && (
        <section className={styles.sec}>
          <h4>Soportes</h4>
          {soportes.map((z, i) => <Banda key={i} z={z} />)}
        </section>
      )}

      {l.zonas.length === 0 && (
        <p className={styles.gap}>No se detectaron niveles con los toques mínimos.</p>
      )}
    </div>
  );
};
