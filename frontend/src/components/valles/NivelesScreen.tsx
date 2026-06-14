// NivelesScreen.tsx
import React from 'react';
import type { SrLevels } from '../../types';
import type { AsyncState } from './useValleyBundle';
import { Eyebrow, Callout, Loading, VerNumeros } from './atoms';
import { formatPrice } from '../../utils';
import styles from './valles.module.css';

const px = (n: number | null | undefined) => (n == null ? '—' : formatPrice(n));

export const NivelesScreen: React.FC<{ symbol: string; state: AsyncState<SrLevels> }> = ({ symbol, state }) => {
  const { data, loading, error } = state;
  let answer: React.ReactNode;

  if (loading) {
    answer = <Loading label="Calculando dónde está el precio…" />;
  } else if (error || !data || data.estado === 'no_disponible') {
    answer = <Callout tone="mute" icon="?" title="No se pudo revisar ahora" sub="Prueba de nuevo en un momento." />;
  } else if (data.zonas.length === 0) {
    answer = (
      <Callout tone="ochre" icon="▢" title="Todavía no hay paredes claras"
        sub={<>El precio no giró suficientes veces en ningún lugar como para marcar una pared.{data.price_live != null && <> Hoy vale <b>${px(data.price_live)}</b>.</>}</>} />
    );
  } else {
    const u = data.ubicacion;
    const inside = u.dentro_de;
    let lead: string, say: React.ReactNode, ratio: number;
    if (inside && inside.tipo === 'soporte') {
      lead = 'El precio está sobre un piso'; ratio = 0.7;
      say = <>Es un piso donde el precio <b>ya giró ahí {inside.toques} veces</b> antes.</>;
    } else if (inside && inside.tipo === 'resistencia') {
      lead = 'El precio está contra un techo'; ratio = 0.3;
      say = <>Es un techo donde el precio <b>ya giró ahí {inside.toques} veces</b> antes.</>;
    } else {
      lead = 'Está en el medio';
      const t = u.techo ? u.techo.dist_pct : 1, p = u.piso ? Math.abs(u.piso.dist_pct) : 1;
      ratio = t / (t + p);
      say = <>No está pegado a ninguna pared: queda entre un techo arriba y un piso abajo.</>;
    }
    const hereTop = 58 + Math.max(0.08, Math.min(0.92, ratio)) * (280 - 116);
    answer = (
      <div className={styles.vwAnswer}>
        <div className={styles.vwAnswerRow}>
          <div className={styles.vwAnswerIcon} aria-hidden="true">⌖</div>
          <div><div className={styles.vwAnswerLead}>{lead}</div><div className={styles.vwAnswerSay}>{say}</div></div>
        </div>
        <div className={styles.vwLevels}>
          <div className={styles.vwBuilding}>
            {u.techo && (
              <div className={`${styles.vwFloor} ${styles.vwFloorTecho}`}>
                <span className={styles.vwFloorArrow} aria-hidden="true">↑</span>
                <span className={styles.vwFloorLbl}>Techo · pared de arriba<b>${px(u.techo.centro)}</b></span>
              </div>
            )}
            <div className={styles.vwHere} style={{ top: `${hereTop}px` }}>
              <span className={styles.vwHereDot} aria-hidden="true" /><span className={styles.vwHereLine} aria-hidden="true" />
              <span className={styles.vwHereTag}>estás acá · ${px(data.price_live)}</span>
            </div>
            {u.piso && (
              <div className={`${styles.vwFloor} ${styles.vwFloorPiso}`}>
                <span className={styles.vwFloorArrow} aria-hidden="true">↓</span>
                <span className={styles.vwFloorLbl}>Piso · pared de abajo<b>${px(u.piso.centro)}</b></span>
              </div>
            )}
          </div>
          <div className={styles.vwLevelsRead}>
            {u.techo && <div><div className={styles.vwDistK}>El techo más cercano está</div><div className={styles.vwDistV}>{u.techo.dist_pct.toFixed(1)}% más arriba</div></div>}
            {u.piso && <div><div className={styles.vwDistK}>El piso más cercano está</div><div className={styles.vwDistV}>{Math.abs(u.piso.dist_pct).toFixed(1)}% más abajo</div></div>}
          </div>
        </div>
        <VerNumeros items={data.zonas.map((z) => ({
          k: `${z.tipo === 'resistencia' ? 'Techo' : 'Piso'} · $${px(z.centro)}`,
          v: `${z.toques} toques`,
          note: `banda $${px(z.precio_bajo)}–$${px(z.precio_alto)}`,
        }))} />
      </div>
    );
  }

  return (
    <div className={styles.vwScreen}>
      <Eyebrow symbol={symbol} />
      <h2 className={styles.vwQuestion}>¿Dónde está el precio ahora?</h2>
      {answer}
      <div className={styles.vwNeutral}>
        Las paredes son lugares donde el precio <b>ya giró antes</b> — son hechos del gráfico.
        No son una señal de comprar ni de vender.
      </div>
    </div>
  );
};
