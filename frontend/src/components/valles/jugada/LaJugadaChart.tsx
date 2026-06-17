// ============================================================
// LaJugadaChart.tsx — gráfico de velas cálido + capa de overlays.
//
// Lightweight Charts dibuja las velas en tonos neutros cálidos
// (sin verde/rojo de semáforo, sin paneo: es una lámina editorial).
// Encima, una capa HTML sincronizada al precio real dibuja la jugada:
// zona de entrada como banda, stop/BE, peldaños de escalera, runner
// y el precio vivo. Al cambiar de fase las anotaciones transicionan.
//
// Montaje idéntico al ChartCanvas de SymbolDetail.tsx: useRef,
// useEffect keyed on symbol, ResizeObserver, chart.remove() cleanup.
// ============================================================

import React, { useEffect, useRef, useState } from 'react';
import { createChart, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import { getOhlcv } from '../../../api';
import type { PlanDerived } from '../../../types';
import { buildOverlays, type LiveState } from './overlays';
import styles from './jugada.module.css';

// Formatea un precio con hasta 4 decimales significativos
function fmt(p: number): string {
  if (p >= 1000) return p.toLocaleString('en-US', { maximumFractionDigits: 2 });
  if (p >= 1)    return p.toFixed(4).replace(/\.?0+$/, '');
  return p.toPrecision(4);
}

export interface LaJugadaChartProps {
  symbol: string;
  plan: PlanDerived;
  live: number;
  state?: LiveState | null;
  phaseLabel: string;
  height?: number;
}

export const LaJugadaChart: React.FC<LaJugadaChartProps> = ({
  symbol,
  plan,
  live,
  state = null,
  phaseLabel,
  height = 420,
}) => {
  const wrapRef  = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  // Contador de re-renders para reposicionar overlays cuando cambia
  // el rango visible o el contenedor redimensiona
  const [, force] = useState(0);

  // ── MONTAR EL GRÁFICO ─────────────────────────────────────
  useEffect(() => {
    if (!wrapRef.current) return;
    const container = wrapRef.current;

    // Jiggle de 1px: fancy-canvas (interno de Lightweight Charts)
    // solo actualiza el bitmap ante un CAMBIO de tamaño; el primer
    // resize con el tamaño correcto a veces no dispara el redraw.
    const applySize = (chart: IChartApi) => {
      const W = Math.round(container.clientWidth);
      const H = Math.round(container.clientHeight);
      if (W < 1 || H < 1) return;
      chart.resize(W - 1, H - 1, true);
      chart.resize(W, H, true);
    };

    const chart = createChart(container, {
      layout: {
        // 'solid' es el valor correcto; se castea para compatibilidad
        // de tipos con la versión exacta de lightweight-charts instalada
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
        borderColor:  '#E4DCCC',
        scaleMargins: { top: 0.07, bottom: 0.07 },
        entireTextOnly: true,
      },
      timeScale:  { visible: false, borderVisible: false },
      crosshair:  { mode: 0 as never },
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
      priceLineVisible:  false,
      lastValueVisible:  false,
      priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
    });

    chartRef.current  = chart;
    seriesRef.current = series;

    // Escala fija al rango de precios del plan: el gráfico se centra
    // en la jugada, no hace autoscale libre
    const priceMin = Math.min(plan.sl_plan, live) * 0.993;
    const priceMax = Math.max(
      ...plan.rungs.map((r) => r.tp_price),
      plan.entry,
      live,
    ) * 1.007;
    series.applyOptions({
      autoscaleInfoProvider: () => ({
        priceRange: { minValue: priceMin, maxValue: priceMax },
      }),
    });

    // Cargar velas reales
    getOhlcv(symbol, '1d', 180).then((res) => {
      if (chartRef.current !== chart) return; // ya destruido
      series.setData(
        res.candles.map((c) => ({
          time:  c.time as never,
          open:  c.open,
          high:  c.high,
          low:   c.low,
          close: c.close,
        })),
      );
      chart.timeScale().fitContent();
      // Dos frames: dar tiempo a que fitContent propague antes de reposicionar
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          applySize(chart);
          force((n) => n + 1);
        }),
      );
    }).catch(() => { /* sin datos: el gráfico queda vacío pero montado */ });

    // ResizeObserver → ajustar bitmap + reposicionar overlays
    const ro = new ResizeObserver(() => {
      if (!chartRef.current) return;
      applySize(chart);
      force((n) => n + 1);
    });
    ro.observe(container);

    // Reposicionar overlays cuando cambia el rango visible (scroll/zoom).
    // En lightweight-charts v4 subscribe devuelve void; se cancela con
    // unsubscribeVisibleLogicalRangeChange pasando la misma referencia.
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

  // ── GEOMETRÍA DE OVERLAYS ─────────────────────────────────
  const s = seriesRef.current;
  // priceToCoordinate devuelve null cuando el precio está fuera del
  // rango visible o la serie aún no tiene datos
  const Y = (p: number): number | null =>
    s ? s.priceToCoordinate(p) : null;

  const ov = buildOverlays({ plan, live, state });

  const zTop = ov.zone ? Y(ov.zone.priceHigh) : null;
  const zBot = ov.zone ? Y(ov.zone.priceLow)  : null;

  return (
    <div className={styles['ju-chart']} style={{ height }}>
      {/* El canvas de Lightweight Charts ocupa todo el div */}
      <div ref={wrapRef} className={styles['ju-chart__canvas']} />

      {/* Leyenda mínima */}
      <div className={styles['ju-chart__legend']}>
        <span className="coin">{symbol}</span>
        <span className="sep" />
        <span>diario · paredes de D.1</span>
      </div>

      {/* Píldora de fase */}
      {phaseLabel && (
        <div className={styles['ju-chart__phase']}>{phaseLabel}</div>
      )}

      {/* ── CAPA DE ANOTACIONES HTML ── */}
      <div className={styles['ju-chart__ann']}>

        {/* runner — banda abierta arriba del último techo */}
        {ov.runner && Y(ov.runner.fromPrice) != null && (
          <div
            className={`${styles['ju-ann']} ${styles['ju-ann-runner']}`}
            style={{ top: 0, height: Math.max(0, Y(ov.runner.fromPrice)!) }}
          >
            <span className={styles['ju-ann-runner__lbl']}>
              runner · {Math.round(ov.runner.frac * 100)}% abierto ↑
            </span>
          </div>
        )}

        {/* zona de entrada (banda rayada) */}
        {ov.zone && zTop != null && zBot != null && (
          <div
            className={`${styles['ju-ann']} ${styles['ju-ann-zone']}`}
            style={{ top: zTop, height: Math.max(2, zBot - zTop) }}
          >
            <span className={styles['ju-ann-zone__lbl']}>
              ZONA DE ENTRADA
              <b className="num">${fmt(ov.zone.priceLow)}–${fmt(ov.zone.priceHigh)}</b>
            </span>
          </div>
        )}

        {/* peldaños de salida */}
        {ov.rungs.map((r, i) => {
          const ry = Y(r.price);
          if (ry == null) return null;
          return (
            <div
              key={i}
              className={[
                styles['ju-ann'],
                styles['ju-ann-line'],
                styles['ju-ann--rung'],
                r.filled ? styles['is-filled'] : '',
              ].join(' ')}
              style={{ top: ry }}
            >
              <span className={styles['ju-ann-line__rule']} />
              <span className={styles['ju-ann-line__tag']}>
                <span className={styles['ju-ann-line__pct']}>
                  {r.filled ? 'llena' : `salida ${i + 1}`}
                </span>
                <span className="num">${fmt(r.price)}</span>
                <span className={styles['ju-ann-line__pct']}>
                  {Math.round(r.sizeFrac * 100)}%
                  {r.toques != null ? ` · ${r.toques} toques` : ''}
                </span>
              </span>
            </div>
          );
        })}

        {/* stop / break-even */}
        {Y(ov.stop.price) != null && (
          <div
            className={[
              styles['ju-ann'],
              styles['ju-ann-line'],
              ov.stop.be ? styles['ju-ann--be'] : styles['ju-ann--stop'],
            ].join(' ')}
            style={{ top: Y(ov.stop.price)! }}
          >
            <span className={styles['ju-ann-line__rule']} />
            <span className={styles['ju-ann-line__tag']}>
              <span className={styles['ju-ann-line__pct']}>
                {ov.stop.be ? 'break-even' : 'stop'}
              </span>
              <span className="num">${fmt(ov.stop.price)}</span>
            </span>
          </div>
        )}

        {/* precio vivo */}
        {Y(ov.live.price) != null && (
          <div
            className={[
              styles['ju-ann'],
              styles['ju-ann-live'],
              ov.live.fuera ? styles['ju-ann-live--fuera'] : '',
            ].join(' ')}
            style={{ top: Y(ov.live.price)! }}
          >
            <span className={styles['ju-ann-live__tag']}>
              {ov.live.fuera ? 'precio de ahora ' : 'ahora '}
              <span className="num">${fmt(ov.live.price)}</span>
            </span>
          </div>
        )}

        {/* hueco honesto: escalera corta, no se inventan techos */}
        {ov.gap && (
          <div
            className={styles['ju-chart__gap']}
            style={{ top: Math.max(8, (zTop ?? 64) - 64) }}
          >
            Arriba del primer techo <b>no hay más paredes claras</b>.
            La escalera queda corta — no se inventan techos.
          </div>
        )}

      </div>
    </div>
  );
};
