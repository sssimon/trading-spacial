// ============================================================
// IdeaChart.tsx — gráfico unificado Valles: velas cálidas +
// 3 capas toggleables (vida / paredes / jugada) con leyenda
// clicable dentro del gráfico.
//
// Montaje idéntico a LaJugadaChart (warm createChart config,
// ResizeObserver, unsubscribeVisibleLogicalRangeChange).
// Capa de overlays HTML sincronizada al precio real, gateada
// por LayerVisibility.
// ============================================================

import React, { useEffect, useRef, useState } from 'react';
import { createChart, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import type { ValleyEval, SrLevels, PlanDerived } from '../../../types';
import { type LiveState } from '../jugada/overlays';
import { buildLayers, LAYER_KEYS, type LayerVisibility, DEFAULT_LAYERS } from './chartLayers';
import { formatPrice } from '../../../utils';
import juStyles from '../jugada/jugada.module.css';
import styles from './idea.module.css';


export interface IdeaChartProps {
  symbol: string;
  vida: ValleyEval | null;
  levels: SrLevels | null;
  plan: PlanDerived | null;
  live: number;
  state?: LiveState | null;
  height?: number;
}

const LAYER_LABELS: Record<typeof LAYER_KEYS[number], string> = {
  vida:    'Vida (¿viva? · posición)',
  paredes: 'Paredes',
  jugada:  'La jugada',
};

export const IdeaChart: React.FC<IdeaChartProps> = ({
  symbol,
  vida,
  levels,
  plan,
  live,
  state = null,
  height = 520,
}) => {
  const wrapRef   = useRef<HTMLDivElement>(null);
  const chartRef  = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const [, force] = useState(0);
  const [layers, setLayers] = useState<LayerVisibility>(DEFAULT_LAYERS);

  // ── MONTAR EL GRÁFICO ─────────────────────────────────────
  useEffect(() => {
    if (!wrapRef.current) return;
    const container = wrapRef.current;

    // Jiggle de 1px para forzar redraw en fancy-canvas
    const applySize = (chart: IChartApi) => {
      const W = Math.round(container.clientWidth);
      const H = Math.round(container.clientHeight);
      if (W < 1 || H < 1) return;
      chart.resize(W - 1, H - 1, true);
      chart.resize(W, H, true);
    };

    const chart = createChart(container, {
      layout: {
        background: { type: 'solid' as never, color: 'transparent' },
        textColor:  '#8A8270',
        fontFamily: "'Instrument Sans', sans-serif",
        fontSize:   11,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: 'rgba(228,220,204,0.55)' },
      },
      rightPriceScale: {
        borderColor:    '#E4DCCC',
        scaleMargins:   { top: 0.07, bottom: 0.07 },
        entireTextOnly: true,
      },
      timeScale:    { visible: false, borderVisible: false },
      crosshair:    { mode: 0 as never },
      handleScroll: false,
      handleScale:  false,
      width:  Math.max(1, container.clientWidth),
      height: Math.max(1, height),
    });

    const series = chart.addCandlestickSeries({
      upColor:         '#DAD0BD',
      downColor:       '#9E947F',
      borderUpColor:   '#A89A82',
      borderDownColor: '#6F6657',
      wickUpColor:     '#A89A82',
      wickDownColor:   '#7C7263',
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
    });

    chartRef.current  = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver(() => {
      if (!chartRef.current) return;
      applySize(chart);
      force((n) => n + 1);
    });
    ro.observe(container);

    const onRangeChange = () => force((n) => n + 1);
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeChange);

    return () => {
      ro.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRangeChange);
      chart.remove();
      chartRef.current  = null;
      seriesRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, height]);

  // ── DATOS DEL GRÁFICO (desde levels.candles) ──────────
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    const candles = levels?.candles;
    if (!candles?.length) return;
    series.setData(
      candles.map((c) => ({
        time:  c.time as never,
        open:  c.open,
        high:  c.high,
        low:   c.low,
        close: c.close,
      })),
    );
    chart.timeScale().fitContent();
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        if (!chartRef.current) return;
        const container = wrapRef.current;
        if (!container) return;
        const W = Math.round(container.clientWidth);
        const H = Math.round(container.clientHeight);
        if (W < 1 || H < 1) return;
        chartRef.current.resize(W - 1, H - 1, true);
        chartRef.current.resize(W, H, true);
        force((n) => n + 1);
      }),
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [levels?.candles]);

  // ── GEOMETRÍA ─────────────────────────────────────────────
  const s = seriesRef.current;
  const Y = (p: number): number | null =>
    s ? s.priceToCoordinate(p) : null;

  const m = buildLayers({
    vida:   vida  ?? ({} as ValleyEval),
    levels: levels ?? ({ zonas: [] } as unknown as SrLevels),
    plan,
    live,
    state: state ?? null,
  });

  const ov = m.jugada;
  const zTop = ov.zone ? Y(ov.zone.priceHigh) : null;
  const zBot = ov.zone ? Y(ov.zone.priceLow)  : null;

  // ── ANTI-COLISIÓN GLOBAL ───────────────────────────────────
  // Recoge todos los candidatos a etiqueta en el borde derecho,
  // los ordena por Y (top → bottom) y marca cuáles se renderizan.
  // La LÍNEA siempre se dibuja; solo la ETIQUETA se suprime.
  const LABEL_GAP = 16; // px mínimos entre etiquetas

  type LabelCandidate = { key: string; y: number };

  const buildCandidates = (): LabelCandidate[] => {
    const candidates: LabelCandidate[] = [];

    // Paredes S/R
    if (layers.paredes) {
      for (const w of m.paredes.walls) {
        const wy = Y(w.centro);
        if (wy != null) candidates.push({ key: `wall-${w.centro}`, y: wy });
      }
    }

    // Peldaños de jugada
    if (layers.jugada && plan) {
      for (let i = 0; i < ov.rungs.length; i++) {
        const ry = Y(ov.rungs[i].price);
        if (ry != null) candidates.push({ key: `rung-${i}`, y: ry });
      }
      // Stop / BE
      if (ov.stop.price > 0) {
        const sy = Y(ov.stop.price);
        if (sy != null) candidates.push({ key: 'stop', y: sy });
      }
      // Precio vivo
      const ly = Y(ov.live.price);
      if (ly != null) candidates.push({ key: 'live', y: ly });
    }

    return candidates.sort((a, b) => a.y - b.y);
  };

  const allCandidates = buildCandidates();
  const visibleLabels = new Set<string>();
  {
    let lastY: number | null = null;
    for (const c of allCandidates) {
      if (lastY == null || c.y - lastY >= LABEL_GAP) {
        visibleLabels.add(c.key);
        lastY = c.y;
      }
    }
  }

  const showLabel = (key: string): boolean => visibleLabels.has(key);

  return (
    <div className={juStyles['ju-chart']} style={{ height }}>
      {/* Canvas de Lightweight Charts */}
      <div ref={wrapRef} className={juStyles['ju-chart__canvas']} />

      {/* ── ESTADO DEL GRÁFICO ── */}
      {levels == null && (
        <div className={styles['idea-chart-state']}>
          Cargando las velas…
        </div>
      )}
      {levels != null && (levels.estado === 'no_disponible' || !levels.candles?.length) && (
        <div className={styles['idea-chart-state']}>
          No se pudieron cargar las velas de esta moneda.
        </div>
      )}

      {/* Leyenda clicable de capas — top-left, debajo del área de marca */}
      <div className={styles['idea-legend']} role="group" aria-label="capas del gráfico">
        {LAYER_KEYS.map((k) => (
          <button
            key={k}
            className={[
              styles['idea-legend__item'],
              layers[k] ? styles['idea-legend__item--on'] : styles['idea-legend__item--off'],
              styles[`idea-legend__item--${k}`],
            ].join(' ')}
            aria-pressed={layers[k]}
            disabled={k === 'jugada' && plan === null}
            onClick={() => setLayers((l) => ({ ...l, [k]: !l[k] }))}
            type="button"
          >
            {LAYER_LABELS[k]}
          </button>
        ))}
      </div>

      {/* ── CAPA DE ANOTACIONES HTML ── */}
      <div className={juStyles['ju-chart__ann']}>

        {/* ── VIDA (sello de posición — banda dibujada es SP3) ── */}
        {layers.vida && m.vida.vivoStamp && (
          <div className={styles['idea-vida-band']}>
            <span className={styles['idea-vida-stamp']}>{m.vida.vivoStamp}</span>
          </div>
        )}

        {/* ── PAREDES (S/R horizontales) ── */}
        {layers.paredes && m.paredes.walls.map((w, i) => {
          const wy = Y(w.centro);
          const esRes = w.tipo === 'resistencia';
          const show = wy == null || showLabel(`wall-${w.centro}`);
          return (
            <div
              key={i}
              className={[
                styles['idea-wall'],
                esRes ? styles['idea-wall--res'] : styles['idea-wall--sup'],
              ].join(' ')}
              style={{ top: wy ?? undefined }}
            >
              <span className={styles['idea-wall__rule']} />
              {show && (
                <span className={styles['idea-wall__tag']}>
                  {esRes ? 'techo' : 'piso'} · ${formatPrice(w.centro)} · {w.toques} toques
                </span>
              )}
            </div>
          );
        })}

        {/* ── JUGADA (delegada al modelo de overlays) ── */}
        {layers.jugada && plan && (
          <>
            {/* runner — banda abierta arriba */}
            {ov.runner && Y(ov.runner.fromPrice) != null && (
              <div
                className={`${juStyles['ju-ann']} ${juStyles['ju-ann-runner']}`}
                style={{ top: 0, height: Math.max(0, Y(ov.runner.fromPrice)!) }}
              >
                <span className={juStyles['ju-ann-runner__lbl']}>
                  runner · {Math.round(ov.runner.frac * 100)}% abierto ↑
                </span>
              </div>
            )}

            {/* zona de entrada (banda rayada) */}
            {ov.zone && zTop != null && zBot != null && (
              <div
                className={`${juStyles['ju-ann']} ${juStyles['ju-ann-zone']}`}
                style={{ top: zTop, height: Math.max(2, zBot - zTop) }}
              >
                <span className={juStyles['ju-ann-zone__lbl']}>
                  ZONA DE ENTRADA
                  <b className="num">${formatPrice(ov.zone.priceLow)}–${formatPrice(ov.zone.priceHigh)}</b>
                </span>
              </div>
            )}

            {/* peldaños de salida */}
            {ov.rungs.map((r, i) => {
              const ry = Y(r.price);
              if (ry == null) return null;
              const show = showLabel(`rung-${i}`);
              return (
                <div
                  key={i}
                  className={[
                    juStyles['ju-ann'],
                    juStyles['ju-ann-line'],
                    juStyles['ju-ann--rung'],
                    r.filled ? juStyles['is-filled'] : '',
                  ].join(' ')}
                  style={{ top: ry }}
                >
                  <span className={juStyles['ju-ann-line__rule']} />
                  {show && (
                    <span className={juStyles['ju-ann-line__tag']}>
                      <span className={juStyles['ju-ann-line__pct']}>
                        {r.filled ? 'llena' : `salida ${i + 1}`}
                      </span>
                      <span className="num">${formatPrice(r.price)}</span>
                      <span className={juStyles['ju-ann-line__pct']}>
                        {Math.round(r.sizeFrac * 100)}%
                        {r.toques != null ? ` · ${r.toques} toques` : ''}
                      </span>
                    </span>
                  )}
                </div>
              );
            })}

            {/* stop / break-even */}
            {ov.stop.price > 0 && Y(ov.stop.price) != null && (
              <div
                className={[
                  juStyles['ju-ann'],
                  juStyles['ju-ann-line'],
                  ov.stop.be ? juStyles['ju-ann--be'] : juStyles['ju-ann--stop'],
                ].join(' ')}
                style={{ top: Y(ov.stop.price)! }}
              >
                <span className={juStyles['ju-ann-line__rule']} />
                {showLabel('stop') && (
                  <span className={juStyles['ju-ann-line__tag']}>
                    <span className={juStyles['ju-ann-line__pct']}>
                      {ov.stop.be ? 'break-even' : 'stop'}
                    </span>
                    <span className="num">${formatPrice(ov.stop.price)}</span>
                  </span>
                )}
              </div>
            )}

            {/* precio vivo */}
            {Y(ov.live.price) != null && (
              <div
                className={[
                  juStyles['ju-ann'],
                  juStyles['ju-ann-live'],
                  ov.live.fuera ? juStyles['ju-ann-live--fuera'] : '',
                ].join(' ')}
                style={{ top: Y(ov.live.price)! }}
              >
                {showLabel('live') && (
                  <span className={juStyles['ju-ann-live__tag']}>
                    {ov.live.fuera ? 'precio de ahora ' : 'ahora '}
                    <span className="num">${formatPrice(ov.live.price)}</span>
                  </span>
                )}
              </div>
            )}

            {/* hueco honesto */}
            {ov.gap && (
              <div
                className={juStyles['ju-chart__gap']}
                style={{ top: Math.max(8, (zTop ?? 64) - 64) }}
              >
                Arriba del primer techo <b>no hay más paredes claras</b>.
                La escalera queda corta — no se inventan techos.
              </div>
            )}
          </>
        )}

      </div>
    </div>
  );
};
