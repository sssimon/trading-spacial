// ============================================================
// ChartModal.tsx — Modal de gráfico estilo TradingView
// Usa lightweight-charts v4 (open-source de TradingView)
// ============================================================

import React, { useEffect, useRef, useState } from 'react';
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { SymbolStatus, OhlcvCandle } from '../types';
import { getOhlcv } from '../api';
import { formatPrice } from '../utils';

// ── Paleta del tema oscuro ────────────────────────────────────
const C_BG      = '#0d1117';
const C_GRID    = '#161b27';
const C_TEXT    = '#8892a4';
const C_BORDER  = '#1e293b';
const C_GREEN   = '#22c55e';
const C_RED     = '#ef4444';
const C_AMBER   = '#f59e0b';
const C_BLUE    = '#63b3ed';

// ── Timeframes disponibles ────────────────────────────────────
type TF = '5m' | '15m' | '1h' | '4h' | '1d';

const TIMEFRAMES: { label: string; value: TF }[] = [
  { label: '5m',  value: '5m'  },
  { label: '15m', value: '15m' },
  { label: '1H',  value: '1h'  },
  { label: '4H',  value: '4h'  },
  { label: '1D',  value: '1d'  },
];

// ── Utilidades ────────────────────────────────────────────────

function priceFormat(price: number) {
  if (price >= 1000) return { precision: 2, minMove: 0.01 };
  if (price >= 1)    return { precision: 4, minMove: 0.0001 };
  return               { precision: 6, minMove: 0.000001 };
}

function computeSMA(candles: OhlcvCandle[], period: number): { time: UTCTimestamp; value: number }[] {
  const out: { time: UTCTimestamp; value: number }[] = [];
  for (let i = period - 1; i < candles.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += candles[j].close;
    out.push({ time: candles[i].time as UTCTimestamp, value: sum / period });
  }
  return out;
}

// ── Componente ────────────────────────────────────────────────

interface ChartModalProps {
  symbol: SymbolStatus | null;
  onClose: () => void;
}

const ChartModal: React.FC<ChartModalProps> = ({ symbol, onClose }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const [tf, setTf]       = useState<TF>('1h');
  const [retryCount, setRetryCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const [hoverPrice, setHoverPrice] = useState<number | null>(null);

  // Cerrar con Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Construir/reconstruir el gráfico cuando cambia symbol o timeframe
  useEffect(() => {
    if (!symbol || !containerRef.current) return;

    // Destruir chart anterior si existe
    chartRef.current?.remove();
    chartRef.current = null;

    const container = containerRef.current;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: C_BG },
        textColor:  C_TEXT,
        fontFamily: "'JetBrains Mono', 'Fira Mono', 'Consolas', monospace",
        fontSize:   11,
      },
      grid: {
        vertLines: { color: C_GRID },
        horzLines: { color: C_GRID },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#3b4a6b', style: 2, width: 1, labelBackgroundColor: '#1e293b' },
        horzLine: { color: '#3b4a6b', style: 2, width: 1, labelBackgroundColor: '#1e293b' },
      },
      rightPriceScale: {
        borderColor: C_BORDER,
        textColor:   C_TEXT,
        scaleMargins: { top: 0.08, bottom: 0.22 },
      },
      timeScale: {
        borderColor:      C_BORDER,
        timeVisible:      true,
        secondsVisible:   false,
        fixLeftEdge:      false,
        fixRightEdge:     false,
      },
      width:  container.clientWidth  || 900,
      height: container.clientHeight || 520,
    });

    chartRef.current = chart;

    // ResizeObserver para actualizar dimensiones
    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width:  containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(container);

    // Flag para evitar actualizar un chart ya desmontado
    let alive = true;

    setLoading(true);
    setError(null);
    setHoverPrice(null);

    getOhlcv(symbol.symbol, tf, 300)
      .then((data) => {
        if (!alive || chartRef.current !== chart) return;

        const candles = data.candles;
        if (!candles.length) return;

        const lastClose = candles[candles.length - 1].close;
        const fmt       = priceFormat(lastClose);

        // ── Candlestick ──────────────────────────────────────
        const candleSeries = chart.addCandlestickSeries({
          upColor:         C_GREEN,
          downColor:       C_RED,
          borderUpColor:   C_GREEN,
          borderDownColor: C_RED,
          wickUpColor:     C_GREEN,
          wickDownColor:   C_RED,
          priceFormat:     { type: 'price', ...fmt },
        });

        candleSeries.setData(
          candles.map((c) => ({
            time:  c.time as UTCTimestamp,
            open:  c.open,
            high:  c.high,
            low:   c.low,
            close: c.close,
          }))
        );

        // Crosshair: mostrar precio en estado
        chart.subscribeCrosshairMove((param) => {
          if (!param.time || !param.seriesData) {
            setHoverPrice(null);
            return;
          }
          const bar = param.seriesData.get(candleSeries) as { close?: number } | undefined;
          if (bar && typeof bar.close === 'number') setHoverPrice(bar.close);
          else setHoverPrice(null);
        });

        // ── Volumen (histograma) ─────────────────────────────
        const volSeries = chart.addHistogramSeries({
          color:        'rgba(99,179,237,0.25)',
          priceFormat:  { type: 'volume' },
          priceScaleId: 'vol',
        });
        chart.priceScale('vol').applyOptions({
          scaleMargins: { top: 0.82, bottom: 0 },
        });
        volSeries.setData(
          data.volumes.map((v) => ({
            time:  v.time as UTCTimestamp,
            value: v.value,
            color: v.color,
          }))
        );

        // ── SMA 20 (ámbar — tendencia corta) ─────────────────
        const sma20 = computeSMA(candles, 20);
        if (sma20.length) {
          const s20 = chart.addLineSeries({
            color:                  C_AMBER,
            lineWidth:              1,
            priceLineVisible:       false,
            lastValueVisible:       false,
            crosshairMarkerVisible: false,
            title:                  'SMA 20',
          });
          s20.setData(sma20);
        }

        // ── SMA 100 (azul — macro, igual que el scanner) ─────
        const sma100 = computeSMA(candles, 100);
        if (sma100.length) {
          const s100 = chart.addLineSeries({
            color:                  C_BLUE,
            lineWidth:              1,
            priceLineVisible:       false,
            lastValueVisible:       false,
            crosshairMarkerVisible: false,
            title:                  'SMA 100',
          });
          s100.setData(sma100);
        }

        chart.timeScale().fitContent();
      })
      .catch((err) => {
        if (!alive) return;
        setError(err instanceof Error ? err.message : 'Error cargando datos');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
      ro.disconnect();
      if (chartRef.current === chart) {
        chart.remove();
        chartRef.current = null;
      }
    };
  }, [symbol, tf, retryCount]);

  if (!symbol) return null;

  const displayPrice = hoverPrice ?? symbol.price ?? 0;
  const lrc          = symbol.lrc_pct;
  const score        = symbol.score ?? 0;
  const lrcColor     = lrc != null && lrc <= 25 ? C_GREEN : C_RED;
  const scoreColor   = score >= 7 ? C_GREEN : score >= 4 ? C_AMBER : C_RED;

  return (
    <>
      {/* Overlay */}
      <div className="chart-overlay" onClick={onClose} aria-hidden="true" />

      {/* Modal */}
      <div className="chart-modal" role="dialog" aria-modal="true">

        {/* ── Header ──────────────────────────────────────── */}
        <div className="chart-modal-header">
          <div className="chart-symbol-info">
            <span className="chart-symbol-name">
              {symbol.symbol.replace('USDT', '')}
              <span className="chart-symbol-quote">/USDT</span>
            </span>
            <span className="chart-live-price">
              ${formatPrice(displayPrice)}
            </span>
            {symbol.señal && (
              <span className="chart-signal-pill">⚡ SEÑAL ACTIVA</span>
            )}
          </div>

          {/* Timeframe selector */}
          <nav className="chart-tf-nav">
            {TIMEFRAMES.map(({ label, value }) => (
              <button
                key={value}
                className={`chart-tf-btn ${tf === value ? 'chart-tf-btn--active' : ''}`}
                onClick={() => setTf(value)}
              >
                {label}
              </button>
            ))}
          </nav>

          <button className="chart-close-btn" onClick={onClose} aria-label="Cerrar">✕</button>
        </div>

        {/* ── Info bar (datos del scanner) ─────────────────── */}
        <div className="chart-info-bar">
          <div className="chart-chip">
            <span className="chart-chip-label">LRC 1H</span>
            <span className="chart-chip-val" style={{ color: lrcColor }}>
              {lrc != null ? `${lrc.toFixed(1)}%` : '—'}
            </span>
          </div>
          <div className="chart-chip">
            <span className="chart-chip-label">Score</span>
            <span className="chart-chip-val" style={{ color: scoreColor }}>
              {score}/9
            </span>
          </div>
          {symbol.sizing_1h?.atr_1h && (
            <>
              <div className="chart-chip">
                <span className="chart-chip-label">ATR</span>
                <span className="chart-chip-val">${Math.round(symbol.sizing_1h.atr_1h).toLocaleString()}</span>
              </div>
              <div className="chart-chip">
                <span className="chart-chip-label">SL/TP</span>
                <span className="chart-chip-val">{symbol.sizing_1h.sl_pct} / {symbol.sizing_1h.tp_pct}</span>
              </div>
            </>
          )}
          <div className="chart-chip">
            <span className="chart-chip-label">Macro 4H</span>
            <span className="chart-chip-val" style={{ color: symbol.gatillo ? C_GREEN : C_RED }}>
              {symbol.gatillo ? 'Alcista ✓' : 'Adversa ✗'}
            </span>
          </div>
          <div className="chart-chip">
            <span className="chart-chip-label">Estado</span>
            <span className="chart-chip-val chart-chip-val--estado">
              {symbol.estado?.slice(0, 28) || '—'}
            </span>
          </div>

          {/* Leyenda de medias */}
          <div className="chart-legend">
            <span className="chart-legend-item">
              <span className="chart-legend-line" style={{ background: C_AMBER }} />
              SMA 20
            </span>
            <span className="chart-legend-item">
              <span className="chart-legend-line" style={{ background: C_BLUE }} />
              SMA 100
            </span>
          </div>
        </div>

        {/* ── Área del gráfico ─────────────────────────────── */}
        <div className="chart-area" ref={containerRef}>
          {loading && (
            <div className="chart-loading-overlay">
              <div className="chart-spinner" />
              <span>Cargando velas…</span>
            </div>
          )}
          {error && !loading && (
            <div className="chart-error-overlay">
              <span>⚠ {error}</span>
              <button className="btn btn-secondary" onClick={() => setRetryCount(c => c + 1)}>
                Reintentar
              </button>
            </div>
          )}
        </div>

        {/* ── Footer ───────────────────────────────────────── */}
        <div className="chart-modal-footer">
          <span>Binance Spot · {tf} · últimas 300 velas</span>
          <span>LRC ≤ 25% = zona LONG &nbsp;·&nbsp; SMA100 = filtro macro scanner</span>
        </div>
      </div>
    </>
  );
};

export default ChartModal;
