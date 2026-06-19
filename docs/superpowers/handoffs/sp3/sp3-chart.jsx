/* global React, window */
// ============================================================
// Valles · SP3 — gráfico de velas + capas.
// Pieza 2: banda del rango de 30d (rectángulo punteado arcilla) +
// marcador de posición. Convive con vida / paredes / jugada.
// En producción las velas las monta lightweight-charts; aquí se
// dibujan con SVG (rects) y las anotaciones van como overlay
// sincronizado al eje de precio — idéntico patrón de capas.
// ============================================================

const { useState: chUseState } = React;
const ch$ = window.sp3$;
const PLOT_H = 460;

// oculta etiquetas que quedan a < 16px (la línea siempre se dibuja)
function thinLabels(items) {
  const sorted = items.slice().sort((a, b) => a.y - b.y);
  let last = -999;
  const keep = new Set();
  for (const it of sorted) { if (it.y - last >= 16) { keep.add(it.key); last = it.y; } }
  return keep;
}

function IdeaChart({ levels, evalData, plan, initLayers, height }) {
  const baseLayers = Object.assign({ vida: true, paredes: true, jugada: false }, initLayers || {});
  const [layers, setLayers] = chUseState(baseLayers);
  const H = height || PLOT_H;

  if (!levels || levels.estado === 'no_disponible' || !levels.candles) {
    return (
      <div className="ch">
        <div className="ch__empty">No se pudieron cargar las velas de esta moneda.</div>
      </div>
    );
  }

  const candles = levels.candles;
  const zonas = levels.zonas || [];
  const range = levels.range30;
  const hasPlan = plan && plan.estado_vivo != null && plan.plan;
  const live = levels.price_live;
  const candidata = evalData && evalData.candidata === true;
  const vivo = evalData && (evalData.candidata || evalData.vivo);
  const pos = evalData && evalData.pos_in_30d_range;

  // límites de precio (incluye velas, zonas, plan, rango) + padding
  let lo = Infinity, hi = -Infinity;
  candles.forEach((c) => { lo = Math.min(lo, c.low); hi = Math.max(hi, c.high); });
  zonas.forEach((z) => { lo = Math.min(lo, z.precio_bajo); hi = Math.max(hi, z.precio_alto); });
  if (hasPlan) { lo = Math.min(lo, plan.plan.sl_plan); plan.plan.rungs.forEach((r) => { hi = Math.max(hi, r.tp_price); }); }
  const padHi = (hi - lo) * 0.08, padLo = (hi - lo) * 0.08;
  hi += padHi; lo -= padLo;
  const yPct = (p) => ((hi - p) / (hi - lo)) * 100;
  const yPx = (p) => (yPct(p) / 100) * H;

  // ── velas (SVG, viewBox = nº de velas × 100) ──────────────
  const N = candles.length;
  const VB_H = 1000; // resolución vertical del viewBox
  const yvb = (p) => ((hi - p) / (hi - lo)) * VB_H;
  const bodyW = 0.62;
  const rects = candles.map((c, i) => {
    const x = i + 0.5;
    const up = c.close >= c.open;
    const top = yvb(Math.max(c.open, c.close));
    const bot = yvb(Math.min(c.open, c.close));
    const bh = Math.max(2, bot - top);
    return (
      <g key={i}>
        <line x1={x} y1={yvb(c.high)} x2={x} y2={yvb(c.low)} stroke="#6E6757" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <rect x={x - bodyW / 2} y={top} width={bodyW} height={bh} fill={up ? '#D8CDB8' : '#5C564A'} />
      </g>
    );
  });

  // banda de rango de 30d ocupa la franja de las últimas 30 velas
  const rangeLeftPct = N > 30 ? ((N - 30) / N) * 100 : 0;

  // ── etiquetas a la izquierda (rango/marcador/jugada) ──────
  const leftItems = [];
  if (layers.vida && pos != null) leftItems.push({ key: 'mark', y: yPx(live != null ? live : evalData.price) });
  if (layers.jugada && hasPlan) {
    leftItems.push({ key: 'stop', y: yPx(plan.plan.sl_plan) });
    plan.plan.rungs.forEach((r, i) => leftItems.push({ key: 'tp' + i, y: yPx(r.tp_price) }));
  }
  const leftKeep = thinLabels(leftItems);
  // etiquetas a la derecha (paredes)
  const rightKeep = layers.paredes ? thinLabels(zonas.map((z, i) => ({ key: 'w' + i, y: yPx(z.centro) }))) : new Set();

  const lifeSeal = layers.vida && (
    <div className={`ch-life ${vivo ? '' : 'ch-life--off'}`}>
      <span className="ch-life__dot" />
      {vivo ? `viva · pos ${Math.round(pos * 100)}% del rango 30d` : 'sin actividad'}
    </div>
  );

  return (
    <div className="ch">
      <div className="ch__legend" role="group" aria-label="capas del gráfico">
        <button className={`ch__leg ${layers.vida ? '' : 'ch__leg--off'}`} onClick={() => setLayers((l) => ({ ...l, vida: !l.vida }))}>
          <span className="ch__leg-sw ch__leg-sw--rango" /> Vida · rango 30d
        </button>
        <button className={`ch__leg ${layers.paredes ? '' : 'ch__leg--off'}`} onClick={() => setLayers((l) => ({ ...l, paredes: !l.paredes }))}>
          <span className="ch__leg-sw ch__leg-sw--paredes" /> Paredes
        </button>
        <button className={`ch__leg ${layers.jugada ? '' : 'ch__leg--off'}`} disabled={!hasPlan} onClick={() => setLayers((l) => ({ ...l, jugada: !l.jugada }))}>
          <span className="ch__leg-sw ch__leg-sw--jugada" /> La jugada
        </button>
      </div>

      <div className="ch__plot" style={{ height: H }}>
        <svg className="ch__svg" viewBox={`0 0 ${N} ${VB_H}`} preserveAspectRatio="none">{rects}</svg>

        <div className="ch__caps">
          {lifeSeal}

          {/* ── paredes S/R (neutral slate, banda con relleno) ── */}
          {layers.paredes && zonas.map((z, i) => {
            const top = yPx(z.precio_alto), bot = yPx(z.precio_bajo);
            return (
              <React.Fragment key={'wall' + i}>
                <div className="ch-wall__band" style={{ top, height: Math.max(3, bot - top) }} />
                {rightKeep.has('w' + i) && (
                  <div className="ch-tag ch-tag--wall ch-tag--right" style={{ top: yPx(z.centro) }}>
                    {z.tipo === 'resistencia' ? 'techo' : 'piso'} · ${ch$(z.centro)} · {z.toques} toques
                  </div>
                )}
              </React.Fragment>
            );
          })}

          {/* ── jugada (entrada / stop / salidas / runner) ── */}
          {layers.jugada && hasPlan && (() => {
            const pl = plan.plan;
            const ez = pl.entry_zone;
            return (
              <React.Fragment>
                {ez && <div className="ch-zone" style={{ top: yPx(ez.precio_alto), height: Math.max(4, yPx(ez.precio_bajo) - yPx(ez.precio_alto)) }} />}
                {ez && <div className="ch-tag ch-tag--entry ch-tag--left" style={{ top: yPx(ez.centro) }}>zona de entrada ${ch$(ez.precio_bajo)}–${ch$(ez.precio_alto)}</div>}
                <div className="ch-line-stop" style={{ top: yPx(pl.sl_plan) }} />
                {leftKeep.has('stop') && <div className="ch-tag ch-tag--stop ch-tag--left" style={{ top: yPx(pl.sl_plan) }}>stop ${ch$(pl.sl_plan)}</div>}
                {pl.rungs.map((r, i) => (
                  <React.Fragment key={'tp' + i}>
                    <div className="ch-line-tp" style={{ top: yPx(r.tp_price) }} />
                    {leftKeep.has('tp' + i) && <div className="ch-tag ch-tag--tp ch-tag--left" style={{ top: yPx(r.tp_price) }}>salida {i + 1} ${ch$(r.tp_price)} · {Math.round(r.size_frac * 100)}%</div>}
                  </React.Fragment>
                ))}
                {pl.runner_frac > 0 && <div className="ch-tag ch-tag--tp ch-tag--right" style={{ top: 14 }}>runner · {Math.round(pl.runner_frac * 100)}% abierto ↑</div>}
              </React.Fragment>
            );
          })()}

          {/* ── Pieza 2 · banda de rango 30d + marcador ── */}
          {layers.vida && range && (() => {
            const top = yPx(range.hi), bot = yPx(range.lo);
            const markY = yPx(live != null ? live : evalData.price);
            return (
              <React.Fragment>
                <div className="ch-range" style={{ top, height: bot - top, left: `${rangeLeftPct}%`, right: '6px' }}>
                  <span className="ch-range__cap ch-range__cap--hi">techo del rango 30d · ${ch$(range.hi)}</span>
                  <span className="ch-range__cap ch-range__cap--lo">piso del rango 30d · ${ch$(range.lo)}</span>
                </div>
                <div className="ch-mark" style={{ top: markY, left: `${rangeLeftPct}%`, right: '6px' }}>
                  <span className="ch-mark__dot" />
                  <span className="ch-mark__line" />
                </div>
                {leftKeep.has('mark') && (
                  <div className="ch-mark__tag" style={{ top: markY, left: `calc(${rangeLeftPct}% - 12px)`, transform: 'translate(-100%, -50%)' }}>
                    pos {Math.round(pos * 100)}% · ahora ${ch$(live != null ? live : evalData.price)}
                  </div>
                )}
              </React.Fragment>
            );
          })()}
        </div>
      </div>

      {layers.jugada && hasPlan && plan.plan.rungs.length === 1 && (
        <div className="ch__gap">Arriba del primer techo no hay más paredes claras. La escalera queda corta — no se inventan techos.</div>
      )}
    </div>
  );
}

window.IdeaChart = IdeaChart;
