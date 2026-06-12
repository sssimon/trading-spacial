// ============================================================
// ProjectDossier — panel de HECHOS citados de un proyecto.
// Sin veredicto: muestra lo que se encontró y lo que no.
// "opaco" (badge warn) ≠ "no disponible" (mensaje distinto).
// Spec Dossier C §2/§3.
// ============================================================

import React from 'react';
import type { Dossier } from '../types';
import styles from './ProjectDossier.module.css';

// Fuente: ancla cada hecho a su URL de origen.
const Fuente: React.FC<{ url: string | null }> = ({ url }) =>
  url ? <a className={styles.src} href={url} target="_blank" rel="noreferrer">fuente</a> : null;

export const ProjectDossier: React.FC<{ dossier: Dossier; loading: boolean }> = ({ dossier, loading }) => {
  if (loading) return <div className={styles.empty}>Investigando…</div>;
  const d = dossier;

  if (d.estado_general === 'no_disponible') {
    return (
      <div className={styles.empty}>
        No se pudo investigar ahora (búsqueda no disponible). Probá refrescar.
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <span className={styles.sym}>{d.symbol.replace('USDT', '')}</span>
        {d.estado_general === 'opaco' && (
          <span className={`${styles.badge} ${styles.badgeOpaco}`}>opaco</span>
        )}
        {d.generated_at && (
          <span className={styles.ts}>{new Date(d.generated_at).toLocaleDateString('es-ES')}</span>
        )}
      </header>

      {d.estado_general === 'opaco' && d.no_encontrado_en.length > 0 && (
        <p className={styles.gap}>No se encontró: {d.no_encontrado_en.join(', ')}.</p>
      )}

      {d.equipo.length > 0 && (
        <section className={styles.sec}>
          <h4>Equipo</h4>
          {d.equipo.map((m, i) => (
            <div key={i} className={styles.row}>
              <span>
                <span>{m.nombre}</span>
                {m.rol ? <span> — {m.rol}</span> : null}
              </span>
              <Fuente url={m.fuente} />
            </div>
          ))}
        </section>
      )}

      {Object.keys(d.presencia).length > 0 && (
        <section className={styles.sec}>
          <h4>Presencia</h4>
          {Object.entries(d.presencia).map(([k, c]) => (
            <div key={k} className={styles.row}>
              <span>{k.replace(/_/g, ' ')}: {c.activo}</span>
              <Fuente url={c.url ?? c.fuente} />
            </div>
          ))}
        </section>
      )}

      {Object.keys(d.actividad).length > 0 && (
        <section className={styles.sec}>
          <h4>Actividad</h4>
          {Object.entries(d.actividad).map(([k, c]) => (
            <div key={k} className={styles.row}>
              <span>{k.replace(/_/g, ' ')}: {c.valor}</span>
              <Fuente url={c.fuente} />
            </div>
          ))}
        </section>
      )}

      {d.financiacion.length > 0 && (
        <section className={styles.sec}>
          <h4>Financiación</h4>
          {d.financiacion.map((h, i) => (
            <div key={i} className={styles.row}>
              <span>{h.descripcion}{h.fecha ? ` (${h.fecha})` : ''}</span>
              <Fuente url={h.fuente} />
            </div>
          ))}
        </section>
      )}

      {d.hitos.length > 0 && (
        <section className={styles.sec}>
          <h4>Hitos</h4>
          {d.hitos.map((h, i) => (
            <div key={i} className={styles.row}>
              <span>{h.descripcion}{h.fecha ? ` (${h.fecha})` : ''}</span>
              <Fuente url={h.fuente} />
            </div>
          ))}
        </section>
      )}
    </div>
  );
};
