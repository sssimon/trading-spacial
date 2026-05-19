// ============================================================
// SymbolDetail v2 — conversational copilot drawer.
//
// Replaces the v1 tabbed layout (Setup / Posición / Historial) with a
// chat-first right pane. An AI agent reads the symbol state, opens with
// a proactive greeting + setup card, and renders rich data cards inline
// as it reaches for "tools" via trailing markers like <<<TOOL:setup>>>.
//
// Architecture
//   - Left pane: lightweight-charts (TradingView's open-source lib)
//   - Right pane: <Copilot/> — header + scroll + suggestion chips + input
//   - Tool cards rendered inline by <CopilotMessage/>:
//       <<<TOOL:setup>>>    → <SetupCard/>
//       <<<TOOL:position>>> → <PositionCard/>
//       <<<TOOL:history>>>  → <HistoryCard/>
//   - Agent backend: POST /agent/chat (proxies to Anthropic Haiku 4.5)
//   - Feature flag: server-driven via GET /agent/status (epic #400, Phase 0).
//     App.tsx owns the polling (useAgentEnabled) and passes the resolved
//     boolean down as `agentEnabled`. When false, the input row is hidden
//     and only the synchronous greeting + setup card render.
// ============================================================

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type UTCTimestamp,
} from 'lightweight-charts';

import styles from './SymbolDetail.module.css';
import type { SymbolStatus, Position, OhlcvCandle, OhlcvVolume } from '../types';
import { formatPrice } from '../utils';
import { getOhlcv, getPositions, chatAgent, type AgentMessage } from '../api';
import { SCORE_FACTORS } from '../constants/score-factors';

// ── Public types ─────────────────────────────────────────────

export type Timeframe = '5m' | '15m' | '1h' | '4h' | '1d';

export interface PositionPreset {
  symbol:    string;
  direction: 'LONG' | 'SHORT';
  entry:     number;
  sl:        number;
  tp:        number;
  qty:       number;
}

interface SymbolDetailProps {
  symbol:          SymbolStatus | null;
  onClose:         () => void;
  onOpenPosition?: (preset: PositionPreset) => void;
  // Server-driven feature flag — App.tsx owns the polling via
  // useAgentEnabled() (epic #400 Phase 0). Pass `true` to enable the
  // copilot input row, `false` to render the read-only fallback. This
  // replaces the previous compile-time `VITE_AGENT_ENABLED` read.
  agentEnabled:    boolean;
}

// ── Helpers ──────────────────────────────────────────────────

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

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// TODO: replace when backend exposes symbol.score_components: boolean[].
// See: frontend/src/constants/score-factors.ts for the 9-factor catalog.
function buildFactors(symbol: SymbolStatus) {
  const score   = symbol.score ?? 0;
  const lrc     = symbol.lrc_pct ?? 50;
  const trigger = symbol.señal === true;
  const seed = symbol.symbol.split('').reduce((a, c) => a + c.charCodeAt(0), 0);

  const passes = new Set<number>();
  if (lrc < 25) passes.add(0);   // LRC
  if (trigger)  passes.add(8);   // TRIG
  let i = 0;
  while (passes.size < score && i < 100) {
    const idx = (seed + i * 7) % 9;
    passes.add(idx);
    i++;
  }
  return SCORE_FACTORS.map((f, idx) => ({ ...f, pass: passes.has(idx) }));
}

// Strip and collect `<<<TOOL:name>>>` markers from agent text.
function extractTools(text: string): { tools: string[]; cleaned: string } {
  const tools: string[] = [];
  const cleaned = text.replace(/<<<TOOL:([a-z_]+)>>>/g, (_, name: string) => {
    tools.push(name);
    return '';
  }).trim();
  return { tools, cleaned };
}

const TIMEFRAMES: { v: Timeframe; l: string }[] = [
  { v: '5m',  l: '5m'  },
  { v: '15m', l: '15m' },
  { v: '1h',  l: '1H'  },
  { v: '4h',  l: '4H'  },
  { v: '1d',  l: '1D'  },
];

// ============================================================
// MAIN — SymbolDetail
// ============================================================

const SymbolDetail: React.FC<SymbolDetailProps> = ({ symbol, onClose, onOpenPosition, agentEnabled }) => {
  // Local alias keeps the rest of the file readable (rename-only change).
  const AGENT_ENABLED = agentEnabled;
  const [tf, setTf] = useState<Timeframe>('1h');

  useEffect(() => {
    if (!symbol) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [symbol, onClose]);

  if (!symbol) return null;

  const base       = symbol.symbol.replace('USDT', '');
  const score      = symbol.score ?? 0;
  const lrc        = symbol.lrc_pct ?? 0;
  const change24h  = symbol.change_24h ?? 0;
  const livePrice  = symbol.live_price ?? symbol.price ?? 0;
  const isFreshSenal = score >= 5 && symbol.señal === true;
  const macroTone: 'bull' | 'bear' = lrc < 25 ? 'bull' : 'bear';
  const scoreTone: 'bull' | 'warn' | 'dim' = score >= 5 ? 'bull' : score >= 3 ? 'warn' : 'dim';

  return (
    <>
      <div className={styles.backdrop} onClick={onClose} aria-hidden="true" />
      <aside
        className={styles.sheet}
        role="dialog"
        aria-label={`Detalle ${base} con copiloto`}
      >
        {/* ── Header ── */}
        <header className={styles.hd}>
          <div className={styles.hdBrand}>
            <div className={styles.hdPair}>
              <span className={styles.hdBase}>{base}</span>
              <span className={styles.hdQuote}>/USDT</span>
            </div>
            <div className={styles.hdPriceRow}>
              <span className={`num ${styles.hdPrice}`}>${formatPrice(livePrice)}</span>
              <span className={`${styles.hdChange} ${change24h >= 0 ? styles.hdChangeBull : styles.hdChangeBear}`}>
                {change24h >= 0 ? '▲' : '▼'} <span className="num">{change24h.toFixed(2)}%</span>
              </span>
            </div>
          </div>

          <div className={styles.hdChips}>
            <Chip label="SCORE"    value={`${score}/9`}                                  tone={scoreTone} />
            <Chip label="LRC 1H"   value={`${lrc.toFixed(1)}%`}                          tone={macroTone} />
            <Chip label="MACRO 4H" value={macroTone === 'bull' ? 'Alcista ✓' : 'Adversa ✗'} tone={macroTone} />
            <Chip
              label="ESTADO"
              value={isFreshSenal ? 'SETUP VÁLIDO' : symbol.señal ? 'Esperando filtros' : 'Sin gatillo'}
              tone={isFreshSenal ? 'bull' : 'warn'}
              long
            />
          </div>

          <div className={styles.hdTools}>
            <nav className={styles.tf}>
              {TIMEFRAMES.map(({ v, l }) => (
                <button
                  key={v}
                  className={[styles.tfBtn, tf === v ? styles.tfBtnActive : ''].filter(Boolean).join(' ')}
                  onClick={() => setTf(v)}
                >{l}</button>
              ))}
            </nav>
            <button className={styles.close} onClick={onClose} aria-label="Cerrar">×</button>
          </div>
        </header>

        {/* ── Body ── */}
        <div className={styles.body}>
          <section className={styles.chartPane}>
            <div className={styles.chartLegend}>
              <span className={styles.legendItem}>
                <span className={`${styles.legendLine} ${styles.legendLineSma20}`} /> SMA 20
              </span>
              <span className={styles.legendItem}>
                <span className={`${styles.legendLine} ${styles.legendLineSma100}`} /> SMA 100
              </span>
              <span className={`${styles.legendItem} ${styles.legendItemRight} prose`}>
                Binance Spot · {tf} · 300 velas
              </span>
            </div>
            <div className={styles.chartWrap}>
              <ChartCanvas symbol={symbol} timeframe={tf} />
            </div>
          </section>

          <Copilot symbol={symbol} onOpenPosition={onOpenPosition} agentEnabled={AGENT_ENABLED} />
        </div>

        {/* ── Footer ── */}
        <footer className={styles.ft}>
          <span className="prose">
            <span className="num">enter</span> envía · <span className="num">esc</span> cierra
          </span>
          <span className="prose">
            copiloto powered by Claude · razonamiento basado en señal en vivo
          </span>
        </footer>
      </aside>
    </>
  );
};

// ============================================================
// CHART — lightweight-charts pane
// ============================================================

interface ChartCanvasProps {
  symbol:    SymbolStatus;
  timeframe: Timeframe;
}

const ChartCanvas: React.FC<ChartCanvasProps> = ({ symbol, timeframe }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    chartRef.current?.remove();
    chartRef.current = null;
    const container = containerRef.current;

    const C_BG     = cssVar('--nbc-bg',            '#0a0d0b');
    const C_GRID   = cssVar('--nbc-border-dimmer', '#6ad7ff19');
    const C_BORDER = cssVar('--nbc-border-dim',    '#6ad7ff33');
    const C_TEXT   = cssVar('--nbc-fg-muted',      '#8093a0');
    const C_BULL   = cssVar('--bull',              '#6ad7ff');
    const C_BEAR   = cssVar('--bear',              '#ffb84e');
    const C_SMA20  = cssVar('--warn',              '#ffb84e');
    const C_SMA100 = cssVar('--bull',              '#6ad7ff');

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: C_BG },
        textColor:  C_TEXT,
        fontFamily: "'JetBrains Mono', 'Geist Mono', ui-monospace, monospace",
        fontSize:   11,
      },
      grid: { vertLines: { color: C_GRID }, horzLines: { color: C_GRID } },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: C_TEXT, style: 2, width: 1, labelBackgroundColor: C_BORDER },
        horzLine: { color: C_TEXT, style: 2, width: 1, labelBackgroundColor: C_BORDER },
      },
      rightPriceScale: {
        borderColor:  C_BORDER,
        textColor:    C_TEXT,
        scaleMargins: { top: 0.08, bottom: 0.22 },
      },
      timeScale: {
        borderColor:    C_BORDER,
        timeVisible:    true,
        secondsVisible: false,
      },
      width:  container.clientWidth  || 800,
      height: container.clientHeight || 420,
    });
    chartRef.current = chart;

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width:  containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
    ro.observe(container);

    let alive = true;
    setLoading(true);
    setError(null);

    getOhlcv(symbol.symbol, timeframe, 300)
      .then((data) => {
        if (!alive || chartRef.current !== chart) return;
        const candles = data.candles;
        if (!candles.length) return;
        const fmt = priceFormat(candles[candles.length - 1].close);

        const candleSeries = chart.addCandlestickSeries({
          upColor:         C_BULL,
          downColor:       C_BEAR,
          borderUpColor:   C_BULL,
          borderDownColor: C_BEAR,
          wickUpColor:     C_BULL,
          wickDownColor:   C_BEAR,
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

        const volSeries = chart.addHistogramSeries({
          color:        'rgba(106,215,255,0.20)',
          priceFormat:  { type: 'volume' },
          priceScaleId: 'vol',
        });
        chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        volSeries.setData(
          (data.volumes as OhlcvVolume[]).map((v) => ({
            time:  v.time as UTCTimestamp,
            value: v.value,
            color: v.color,
          }))
        );

        const sma20 = computeSMA(candles, 20);
        if (sma20.length) {
          chart.addLineSeries({ color: C_SMA20, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: 'SMA 20' }).setData(sma20);
        }
        const sma100 = computeSMA(candles, 100);
        if (sma100.length) {
          chart.addLineSeries({ color: C_SMA100, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: 'SMA 100' }).setData(sma100);
        }
        chart.timeScale().fitContent();
      })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : 'Error cargando datos'); })
      .finally(() => { if (alive) setLoading(false); });

    return () => {
      alive = false;
      ro.disconnect();
      if (chartRef.current === chart) {
        chart.remove();
        chartRef.current = null;
      }
    };
  }, [symbol.symbol, timeframe]);

  return (
    <>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {loading && <div className={styles.chartLoading}>Cargando…</div>}
      {error   && <div className={styles.chartError}>{error}</div>}
    </>
  );
};

// ============================================================
// COPILOT — chat-first right pane
// ============================================================

type Verdict = 'bull' | 'warn' | 'bear';

interface CopilotMsg {
  role:    'user' | 'assistant';
  text:    string;
  tools?:  string[];
  verdict?: Verdict;
  error?:  boolean;
}

const SUGGESTIONS = [
  { label: '¿qué muestra el score?', msg: 'Explícame el desglose del score.' },
  { label: 'simular posición $1000',  msg: 'Si abro $1000 aquí con 1% de riesgo, ¿cómo queda?' },
  { label: 'historial del par',       msg: '¿Cómo le ha ido al sistema con este par últimamente?' },
  { label: '¿debo operar ahora?',     msg: '¿Recomiendas que abra una posición ahora mismo?' },
];

interface CopilotProps {
  symbol:          SymbolStatus;
  onOpenPosition?: (preset: PositionPreset) => void;
  // Server-driven feature flag forwarded from <SymbolDetail/>.
  agentEnabled:    boolean;
}

const Copilot: React.FC<CopilotProps> = ({ symbol, onOpenPosition, agentEnabled }) => {
  const AGENT_ENABLED = agentEnabled;
  const [msgs,    setMsgs]    = useState<CopilotMsg[]>([]);
  const [input,   setInput]   = useState('');
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Proactive greeting when a symbol opens — runs sync, no API call.
  useEffect(() => {
    if (!symbol) return;
    const factors = buildFactors(symbol);
    const passing = factors.filter((f) => f.pass);
    const failing = factors.filter((f) => !f.pass);
    const verdict: Verdict =
      passing.length >= 6 ? 'bull' :
      passing.length >= 4 ? 'warn' :
      'bear';
    const base = symbol.symbol.replace('USDT', '');
    const greeting =
      verdict === 'bull'
        ? `Detecté **setup firme** en ${base}. Score ${passing.length}/9, gatillo activo. ¿Quieres que te explique los factores o prefieres simular una posición?`
        : verdict === 'warn'
        ? `${base} muestra **setup parcial** (${passing.length}/9). Faltan ${failing.length} confirmaciones. ¿Te explico cuáles?`
        : `${base} **no recomienda entrar ahora** — sólo ${passing.length} de 9 filtros se cumplen. ¿Te muestro qué falta?`;

    setMsgs([{ role: 'assistant', text: greeting, verdict, tools: ['setup'] }]);
  }, [symbol.symbol]);

  // Autoscroll on new messages
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, loading]);

  const send = async (text: string) => {
    const t = text.trim();
    if (!t || loading) return;
    setMsgs((prev) => [...prev, { role: 'user', text: t }]);
    setInput('');
    if (!AGENT_ENABLED) return;
    setLoading(true);

    try {
      const factors = buildFactors(symbol);
      const passing = factors.filter((f) => f.pass);
      const failing = factors.filter((f) => !f.pass);
      const base    = symbol.symbol.replace('USDT', '');
      const sysPrompt = `Eres un copiloto de trading conversando en español casual y directo.
El usuario está mirando el par ${base}/USDT a $${formatPrice(symbol.live_price ?? symbol.price ?? 0)}.

DATOS ACTUALES:
- Score: ${passing.length}/9
- LRC%: ${(symbol.lrc_pct ?? 0).toFixed(1)}%
- Cambio 24h: ${(symbol.change_24h ?? 0).toFixed(2)}%
- Side sugerido: ${symbol.direction ?? 'LONG'}
- Gatillo 5M activo: ${symbol.señal ? 'sí' : 'no'}

FACTORES QUE PASAN: ${passing.map((f) => f.key).join(', ') || 'ninguno'}
FACTORES QUE FALLAN: ${failing.map((f) => f.key).join(', ') || 'ninguno'}

INSTRUCCIONES:
- Responde MUY breve (1-3 oraciones máximo). El usuario está en una UI compacta.
- Si el usuario pregunta sobre el score o factores, termina tu respuesta con <<<TOOL:setup>>>
- Si quiere simular una posición o calcular riesgo, termina con <<<TOOL:position>>>
- Si pregunta por historial pasado, termina con <<<TOOL:history>>>
- Si pregunta "¿debo operar?", da una recomendación clara basada en el score y termina con <<<TOOL:setup>>>
- NUNCA inventes datos. Si no sabes algo, dilo.
- No uses asteriscos para negrita más de 1-2 veces por respuesta.`;

      // Keep last 6 turns of context — enough for follow-ups, cheap on tokens.
      const history: AgentMessage[] = msgs.slice(-6).map((m) => ({
        role:    m.role,
        content: m.text,
      }));

      const resp = await chatAgent({
        system:   sysPrompt,
        messages: [...history, { role: 'user', content: t }],
      });
      const { tools, cleaned } = extractTools(resp.text || '');
      setMsgs((prev) => [...prev, { role: 'assistant', text: cleaned, tools }]);
    } catch (err) {
      setMsgs((prev) => [...prev, {
        role:  'assistant',
        text:  err instanceof Error ? `No pude analizar eso: ${err.message}` : 'No pude analizar eso. Intenta de nuevo.',
        error: true,
      }]);
    } finally {
      setLoading(false);
    }
  };

  const submitInput = (e: React.FormEvent) => {
    e.preventDefault();
    send(input);
  };

  const base = symbol.symbol.replace('USDT', '');

  return (
    <section className={styles.cp}>
      <header className={styles.cpHd}>
        <div className={styles.cpHdId}>
          <span className={styles.cpHdGlyph}>◈</span>
          <div>
            <div className={styles.cpHdName}>copiloto</div>
            <div className={styles.cpHdSub}>leyendo {base}/USDT en vivo</div>
          </div>
        </div>
        <div className={`${styles.cpHdStatus} ${AGENT_ENABLED ? '' : styles.cpHdStatusOff}`}>
          <span className={`${styles.cpHdDot} ${AGENT_ENABLED ? '' : styles.cpHdDotOff}`} />
          <span className={styles.cpHdStatusLbl}>{AGENT_ENABLED ? 'online' : 'offline'}</span>
        </div>
      </header>

      <div className={styles.cpScroll} ref={scrollRef}>
        {msgs.map((m, i) => (
          <CopilotMessage key={i} message={m} symbol={symbol} onOpenPosition={onOpenPosition} />
        ))}
        {loading && <TypingBubble />}
      </div>

      {AGENT_ENABLED ? (
        <>
          <div className={styles.cpSuggestions}>
            {SUGGESTIONS.map((s, i) => (
              <button
                key={i}
                className={styles.cpChip}
                onClick={() => send(s.msg)}
                disabled={loading}
              >{s.label}</button>
            ))}
          </div>
          <form className={styles.cpInputRow} onSubmit={submitInput}>
            <span className={styles.cpInputPrompt}>&gt;</span>
            <input
              className={styles.cpInput}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={loading ? 'pensando…' : 'pregunta lo que quieras sobre este par'}
              disabled={loading}
              autoFocus
            />
            <button
              className={styles.cpSend}
              type="submit"
              disabled={loading || !input.trim()}
            >↵</button>
          </form>
        </>
      ) : (
        <div className={styles.cpFlagOff}>
          copiloto desactivado. Modo solo-lectura.
        </div>
      )}
    </section>
  );
};

// ── CopilotMessage + helpers ─────────────────────────────────

interface CopilotMessageProps {
  message:         CopilotMsg;
  symbol:          SymbolStatus;
  onOpenPosition?: (preset: PositionPreset) => void;
}
const CopilotMessage: React.FC<CopilotMessageProps> = ({ message, symbol, onOpenPosition }) => {
  if (message.role === 'user') {
    return (
      <div className={`${styles.cpMsg} ${styles.cpMsgUser}`}>
        <div className={`${styles.cpBubble} ${styles.cpBubbleUser}`}>{message.text}</div>
      </div>
    );
  }

  const verdictClass =
    message.verdict === 'bull' ? styles.cpBubbleVerdictBull :
    message.verdict === 'warn' ? styles.cpBubbleVerdictWarn :
    message.verdict === 'bear' ? styles.cpBubbleVerdictBear : '';
  const errorClass = message.error ? styles.cpBubbleError : '';

  return (
    <div className={`${styles.cpMsg} ${styles.cpMsgAsst}`}>
      <div className={styles.cpMsgAvatar}>◈</div>
      <div className={styles.cpMsgBody}>
        {message.text && (
          <div className={[styles.cpBubble, styles.cpBubbleAsst, errorClass, verdictClass].filter(Boolean).join(' ')}>
            <FormattedText text={message.text} />
          </div>
        )}
        {message.tools?.map((t, i) => {
          if (t === 'setup')    return <SetupCard    key={i} symbol={symbol} />;
          if (t === 'position') return <PositionCard key={i} symbol={symbol} onOpen={onOpenPosition} />;
          if (t === 'history')  return <HistoryCard  key={i} symbol={symbol} />;
          return null;
        })}
      </div>
    </div>
  );
};

const FormattedText: React.FC<{ text: string }> = ({ text }) => {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith('**') && p.endsWith('**')) {
          return <strong key={i}>{p.slice(2, -2)}</strong>;
        }
        return (
          <React.Fragment key={i}>
            {p.split('\n').map((line, j) => (
              <React.Fragment key={j}>{j > 0 && <br />}{line}</React.Fragment>
            ))}
          </React.Fragment>
        );
      })}
    </>
  );
};

const TypingBubble: React.FC = () => (
  <div className={`${styles.cpMsg} ${styles.cpMsgAsst}`}>
    <div className={styles.cpMsgAvatar}>◈</div>
    <div className={styles.cpMsgBody}>
      <div className={`${styles.cpBubble} ${styles.cpBubbleAsst} ${styles.cpBubbleTyping}`}>
        <span className={styles.cpTypingDot} />
        <span className={styles.cpTypingDot} />
        <span className={styles.cpTypingDot} />
      </div>
    </div>
  </div>
);

// ============================================================
// SETUP CARD — two-column pass/fail breakdown
// ============================================================

const SetupCard: React.FC<{ symbol: SymbolStatus }> = ({ symbol }) => {
  const factors = useMemo(() => buildFactors(symbol), [symbol]);
  const passing = factors.filter((f) => f.pass);
  const failing = factors.filter((f) => !f.pass);
  return (
    <div className={styles.cpCard}>
      <div className={styles.cpCardHead}>
        <span className={styles.cpCardIcon}>◧</span>
        <span className={styles.cpCardTitle}>Desglose del score</span>
        <span className={`${styles.cpCardScore} num`}>{passing.length}/9</span>
      </div>
      <div className={styles.cpFactCols}>
        <div>
          <div className={styles.cpFactColsHd}>
            <span className={`${styles.cpFactColsDot} ${styles.cpFactColsDotOk}`} />
            <span className={styles.cpFactColsLabel}>PASAN ({passing.length})</span>
          </div>
          <ul className={styles.cpFactList}>
            {passing.length === 0 ? (
              <li className={`${styles.cpFact} ${styles.cpFactEmpty} prose`}>— ninguno todavía</li>
            ) : passing.map((f) => (
              <li key={f.key} className={`${styles.cpFact} ${styles.cpFactPass}`}>
                <span className={styles.cpFactKey}>{f.key}</span>
                <span className={styles.cpFactTxt}>{f.label}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className={styles.cpFactColsHd}>
            <span className={`${styles.cpFactColsDot} ${styles.cpFactColsDotNo}`} />
            <span className={styles.cpFactColsLabel}>FALLAN ({failing.length})</span>
          </div>
          <ul className={styles.cpFactList}>
            {failing.length === 0 ? (
              <li className={`${styles.cpFact} ${styles.cpFactEmpty} prose`}>— ninguno, score perfecto</li>
            ) : failing.map((f) => (
              <li key={f.key} className={`${styles.cpFact} ${styles.cpFactFail}`}>
                <span className={styles.cpFactKey}>{f.key}</span>
                <span className={styles.cpFactTxt}>{f.label}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
};

// ============================================================
// POSITION CARD — inline knobs + RR + outputs + CTA
// ============================================================

interface PositionCardProps {
  symbol: SymbolStatus;
  onOpen?: (preset: PositionPreset) => void;
}
const PositionCard: React.FC<PositionCardProps> = ({ symbol, onOpen }) => {
  const [capital, setCapital] = useState(1000);
  const [riskPct, setRiskPct] = useState(1);
  const [rr,      setRr]      = useState(2);
  const [slPct,   setSlPct]   = useState(2.5);

  const entry  = symbol.live_price ?? symbol.price ?? 0;
  const isLong = (symbol.direction ?? 'LONG') === 'LONG';
  const sl     = isLong ? entry * (1 - slPct / 100) : entry * (1 + slPct / 100);
  const tp     = isLong ? entry * (1 + (slPct * rr) / 100) : entry * (1 - (slPct * rr) / 100);
  const riskUsd   = capital * (riskPct / 100);
  const slDistAbs = Math.abs(entry - sl);
  const qty       = slDistAbs > 0 ? riskUsd / slDistAbs : 0;
  const posValue  = qty * entry;
  const rewardUsd = qty * Math.abs(tp - entry);

  const minBar = Math.min(sl, entry, tp);
  const maxBar = Math.max(sl, entry, tp);
  const span   = (maxBar - minBar) || 1;
  const posOf  = (p: number) => ((p - minBar) / span) * 100;

  const disabled = entry <= 0 || qty <= 0;

  const handleOpen = () => {
    if (!onOpen || disabled) return;
    onOpen({
      symbol:    symbol.symbol,
      direction: isLong ? 'LONG' : 'SHORT',
      entry,
      sl,
      tp,
      qty,
    });
  };

  return (
    <div className={styles.cpCard}>
      <div className={styles.cpCardHead}>
        <span className={styles.cpCardIcon}>✦</span>
        <span className={styles.cpCardTitle}>Calculadora de posición</span>
        <span className={styles.cpCardScore}>
          <span className="num">${posValue.toFixed(0)}</span>
        </span>
      </div>

      <div className={styles.cpKnobs}>
        <Knob label="capital"  value={`$${capital}`}             onMinus={() => setCapital(Math.max(100, capital - 100))} onPlus={() => setCapital(capital + 100)} />
        <Knob label="riesgo %" value={riskPct.toFixed(1)}        onMinus={() => setRiskPct(Math.max(0.1, +(riskPct - 0.1).toFixed(1)))} onPlus={() => setRiskPct(+(riskPct + 0.1).toFixed(1))} />
        <Knob label="sl %"     value={slPct.toFixed(2)}          onMinus={() => setSlPct(Math.max(0.5, +(slPct - 0.25).toFixed(2)))}   onPlus={() => setSlPct(+(slPct + 0.25).toFixed(2))} />
        <Knob label="r:r"      value={`1:${rr}`}                 onMinus={() => setRr(Math.max(1, rr - 0.5))} onPlus={() => setRr(rr + 0.5)} />
      </div>

      <div className={styles.cpRr}>
        <div className={styles.cpRrBar}>
          <div className={`${styles.cpRrZone} ${styles.cpRrZoneLoss}`} style={{ left: '0%', width: `${posOf(entry)}%` }} />
          <div className={`${styles.cpRrZone} ${styles.cpRrZoneGain}`} style={{ left: `${posOf(entry)}%`, right: '0%' }} />
          {[
            { name: 'sl' as const,    val: sl,    cls: styles.cpRrMarkSl },
            { name: 'entry' as const, val: entry, cls: styles.cpRrMarkEntry },
            { name: 'tp' as const,    val: tp,    cls: styles.cpRrMarkTp },
          ].map((m) => (
            <div key={m.name} className={`${styles.cpRrMark} ${m.cls}`} style={{ left: `${posOf(m.val)}%` }}>
              <span className={styles.cpRrMarkLbl}>{m.name.toUpperCase()}</span>
              <span className={`${styles.cpRrMarkVal} num`}>{formatPrice(m.val)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className={styles.cpOuts}>
        <KV label="qty"          value={qty.toFixed(4)}              tone="neutral" />
        <KV label="-riesgo"      value={`$${riskUsd.toFixed(2)}`}    tone="bear" />
        <KV label="+reward"      value={`$${rewardUsd.toFixed(2)}`}  tone="bull" />
        <KV label="risk:reward"  value={`1:${rr}`}                   tone="neutral" />
      </div>

      <button className={styles.cpCardCta} onClick={handleOpen} disabled={disabled}>
        <span style={{ color: 'var(--bull)' }}>▸</span> abrir esta posición
      </button>
    </div>
  );
};

// ============================================================
// HISTORY CARD — real closed positions for this pair
// ============================================================

interface HistoryEntry {
  daysAgo: number;
  outcome: 'tp' | 'sl' | 'manual';
  pnl:     number;
}

const HistoryCard: React.FC<{ symbol: SymbolStatus }> = ({ symbol }) => {
  const [items, setItems] = useState<HistoryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setItems(null);
    setError(null);
    // TODO: when /positions accepts `?symbol=X`, drop the client-side filter.
    getPositions('closed')
      .then((resp) => {
        if (!alive) return;
        const now = Date.now();
        const list: HistoryEntry[] = (resp.positions ?? [])
          .filter((p: Position) => p.symbol === symbol.symbol)
          .slice(0, 6)
          .map((p: Position) => {
            const ts = p.exit_ts ?? p.entry_ts;
            const daysAgo = ts
              ? Math.max(0, Math.round((now - new Date(ts).getTime()) / (1000 * 60 * 60 * 24)))
              : 0;
            return {
              daysAgo,
              outcome: p.exit_reason === 'TP_HIT' ? 'tp' : p.exit_reason === 'SL_HIT' ? 'sl' : 'manual',
              pnl:     p.pnl_pct ?? 0,
            };
          });
        setItems(list);
      })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : 'Error cargando historial'); });
    return () => { alive = false; };
  }, [symbol.symbol]);

  if (items === null && !error) {
    return (
      <div className={styles.cpCard}>
        <div className={styles.cpCardHead}>
          <span className={styles.cpCardIcon}>◉</span>
          <span className={styles.cpCardTitle}>Historial reciente</span>
        </div>
        <div className={`${styles.cpHistEmpty} prose`}>Cargando…</div>
      </div>
    );
  }
  if (error) {
    return (
      <div className={styles.cpCard}>
        <div className={styles.cpCardHead}>
          <span className={styles.cpCardIcon}>◉</span>
          <span className={styles.cpCardTitle}>Historial reciente</span>
        </div>
        <div className={`${styles.cpHistEmpty} prose`}>Error: {error}</div>
      </div>
    );
  }
  const list = items ?? [];
  if (list.length === 0) {
    return (
      <div className={styles.cpCard}>
        <div className={styles.cpCardHead}>
          <span className={styles.cpCardIcon}>◉</span>
          <span className={styles.cpCardTitle}>Historial reciente</span>
        </div>
        <div className={`${styles.cpHistEmpty} prose`}>Sin operaciones previas en este par.</div>
      </div>
    );
  }
  const wins = list.filter((h) => h.pnl > 0).length;
  const wr   = (wins / list.length) * 100;

  return (
    <div className={styles.cpCard}>
      <div className={styles.cpCardHead}>
        <span className={styles.cpCardIcon}>◉</span>
        <span className={styles.cpCardTitle}>Historial reciente</span>
        <span className={styles.cpCardScore}>
          <span className="num">{wr.toFixed(0)}%</span>
          <span className={styles.cpCardScoreSuf}>WR · {list.length}</span>
        </span>
      </div>
      <div className={styles.cpHistStrip}>
        {list.map((h, i) => {
          const tone: 'bull' | 'bear' | 'warn' = h.pnl > 0 ? 'bull' : h.pnl < 0 ? 'bear' : 'warn';
          const tag  = h.outcome === 'tp' ? 'TP' : h.outcome === 'sl' ? 'SL' : 'MAN';
          const toneCellClass =
            tone === 'bull' ? styles.cpHistCellBull :
            tone === 'bear' ? styles.cpHistCellBear : styles.cpHistCellWarn;
          const tonePnlClass =
            tone === 'bull' ? styles.cpHistCellPnlBull :
            tone === 'bear' ? styles.cpHistCellPnlBear : styles.cpHistCellPnlWarn;
          return (
            <div
              key={i}
              className={`${styles.cpHistCell} ${toneCellClass}`}
              title={`hace ${h.daysAgo}d · ${tag} · ${h.pnl.toFixed(2)}%`}
            >
              <span className={styles.cpHistCellTag}>{tag}</span>
              <span className={`${styles.cpHistCellPnl} num ${tonePnlClass}`}>
                {h.pnl > 0 ? '+' : ''}{h.pnl.toFixed(1)}%
              </span>
              <span className={`${styles.cpHistCellWhen} prose`}>{h.daysAgo}d</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ============================================================
// Tiny atoms
// ============================================================

const Knob: React.FC<{ label: string; value: string; onMinus: () => void; onPlus: () => void }> = ({ label, value, onMinus, onPlus }) => (
  <div className={styles.cpKnob}>
    <span className={styles.cpKnobLabel}>{label}</span>
    <button className={`${styles.cpKnobBtn} ${styles.cpKnobBtnMinus}`} onClick={onMinus}>−</button>
    <span className={`${styles.cpKnobVal} num`}>{value}</span>
    <button className={`${styles.cpKnobBtn} ${styles.cpKnobBtnPlus}`} onClick={onPlus}>+</button>
  </div>
);

type KvTone = 'bull' | 'bear' | 'warn' | 'neutral';
const KV: React.FC<{ label: string; value: React.ReactNode; tone: KvTone }> = ({ label, value, tone }) => {
  const toneClass =
    tone === 'bull'    ? styles.cpKvBull :
    tone === 'bear'    ? styles.cpKvBear :
    tone === 'warn'    ? styles.cpKvWarn :
                         styles.cpKvNeutral;
  return (
    <div className={`${styles.cpKv} ${toneClass}`}>
      <span className={styles.cpKvLabel}>{label}</span>
      <span className={`${styles.cpKvVal} num`}>{value}</span>
    </div>
  );
};

type ChipTone = 'bull' | 'bear' | 'warn' | 'dim';
const Chip: React.FC<{ label: string; value: string; tone: ChipTone; long?: boolean }> = ({ label, value, tone, long }) => {
  const toneClass =
    tone === 'bull' ? styles.chipBull :
    tone === 'bear' ? styles.chipBear :
    tone === 'warn' ? styles.chipWarn :
                      styles.chipDim;
  return (
    <div className={[styles.chip, toneClass, long ? styles.chipLong : ''].filter(Boolean).join(' ')}>
      <span className={`${styles.chipLabel} label`}>{label}</span>
      <span className={`${styles.chipVal} num`}>{value}</span>
    </div>
  );
};

export default SymbolDetail;
