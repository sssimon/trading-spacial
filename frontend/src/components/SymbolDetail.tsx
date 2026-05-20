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
import type { SymbolStatus, OhlcvCandle, OhlcvVolume } from '../types';
import { formatPrice } from '../utils';
import { getOhlcv } from '../api';
import { useAgentStream } from '../agent/useAgentStream';
import { SURFACE_SYMBOL_DETAIL } from '../agent/surfaces';
import type { ProposalChip, ToolChip } from '../agent/types';
import { SCORE_FACTORS } from '../constants/score-factors';

// ── Public types ─────────────────────────────────────────────

export type Timeframe = '5m' | '15m' | '1h' | '4h' | '1d';

interface SymbolDetailProps {
  symbol:          SymbolStatus | null;
  onClose:         () => void;
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

// Note: the `<<<TOOL:name>>>` marker protocol the Copilot used to parse
// is dead post-Phase-3. The model now emits typed tool_use events; the
// renderer below consumes those via useAgentStream's tool_chips and
// proposals state. The Setup/Position/History card components remain in
// this file (below) but are no longer wired up — they'll be revived via
// a future inline-card protocol if the UX needs the richer surfaces back.

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

const SymbolDetail: React.FC<SymbolDetailProps> = ({ symbol, onClose, agentEnabled }) => {
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

          <Copilot symbol={symbol} agentEnabled={AGENT_ENABLED} />
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

const SUGGESTIONS = [
  { label: '¿qué muestra el score?', msg: 'Explícame el desglose del score.' },
  { label: 'simular posición $1000',  msg: 'Si abro $1000 aquí con 1% de riesgo, ¿cómo queda?' },
  { label: 'historial del par',       msg: '¿Cómo le ha ido al sistema con este par últimamente?' },
  { label: '¿debo operar ahora?',     msg: '¿Recomiendas que abra una posición ahora mismo?' },
];

interface CopilotProps {
  symbol:          SymbolStatus;
  // Server-driven feature flag forwarded from <SymbolDetail/>.
  agentEnabled:    boolean;
}

const Copilot: React.FC<CopilotProps> = ({ symbol, agentEnabled }) => {
  const AGENT_ENABLED = agentEnabled;
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);

  // Phase 3 rewire: replace the legacy one-shot chatAgent + frontend
  // system prompt with the streaming hook. The server owns the prompt
  // (api/agent/prompts/system.py + surfaces.py), the tool schema, and
  // the audit. The model never sees a frontend-built system prompt.
  const { msgs, loading, sendTurn, confirmProposal } = useAgentStream({
    surface: SURFACE_SYMBOL_DETAIL,
  });

  // Proactive synthetic greeting computed locally — never goes on the
  // wire, never enters the rolling transcript. Re-derives whenever
  // the symbol or its score-relevant fields change.
  const greeting = useMemo<{ text: string; verdict: Verdict } | null>(() => {
    if (!symbol) return null;
    const factors = buildFactors(symbol);
    const passing = factors.filter((f) => f.pass);
    const failing = factors.filter((f) => !f.pass);
    const verdict: Verdict =
      passing.length >= 6 ? 'bull' :
      passing.length >= 4 ? 'warn' :
      'bear';
    const base = symbol.symbol.replace('USDT', '');
    const text =
      verdict === 'bull'
        ? `Detecté **setup firme** en ${base}. Score ${passing.length}/9, gatillo activo. ¿Quieres que te explique los factores o prefieres simular una posición?`
        : verdict === 'warn'
        ? `${base} muestra **setup parcial** (${passing.length}/9). Faltan ${failing.length} confirmaciones. ¿Te explico cuáles?`
        : `${base} **no recomienda entrar ahora** — sólo ${passing.length} de 9 filtros se cumplen. ¿Te muestro qué falta?`;
    return { text, verdict };
  }, [symbol]);

  // Autoscroll on new messages
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, loading]);

  const send = async (text: string) => {
    const t = text.trim();
    if (!t || loading) return;
    setInput('');
    if (!AGENT_ENABLED) return;
    // Pass the current symbol as a context hint so the server-side
    // prompt can scope its tool calls (e.g. default get_symbol_setup
    // to this pair).
    await sendTurn(t, { symbol: symbol.symbol });
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
        {greeting && msgs.length === 0 && (
          <CopilotMessage
            role="assistant"
            text={greeting.text}
            verdict={greeting.verdict}
          />
        )}
        {msgs.map((m, i) => (
          <CopilotMessage
            key={i}
            role={m.role}
            text={m.text}
            reasoning={m.reasoning}
            toolChips={m.tool_chips}
            proposals={m.proposals}
            onConfirmProposal={confirmProposal}
            showTyping={
              m.role === 'assistant' && loading && i === msgs.length - 1 && m.text === ''
            }
          />
        ))}
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
  role:               'user' | 'assistant';
  text:               string;
  reasoning?:         string;
  verdict?:           Verdict;
  toolChips?:         ToolChip[];
  proposals?:         ProposalChip[];
  onConfirmProposal?: (proposal_id: string) => void;
  showTyping?:        boolean;
}
const CopilotMessage: React.FC<CopilotMessageProps> = ({
  role, text, reasoning, verdict, toolChips, proposals, onConfirmProposal, showTyping,
}) => {
  if (role === 'user') {
    return (
      <div className={`${styles.cpMsg} ${styles.cpMsgUser}`}>
        <div className={`${styles.cpBubble} ${styles.cpBubbleUser}`}>{text}</div>
      </div>
    );
  }

  const verdictClass =
    verdict === 'bull' ? styles.cpBubbleVerdictBull :
    verdict === 'warn' ? styles.cpBubbleVerdictWarn :
    verdict === 'bear' ? styles.cpBubbleVerdictBear : '';

  return (
    <div className={`${styles.cpMsg} ${styles.cpMsgAsst}`}>
      <div className={styles.cpMsgAvatar}>◈</div>
      <div className={styles.cpMsgBody}>
        {showTyping && (
          <div className={`${styles.cpBubble} ${styles.cpBubbleAsst} ${styles.cpBubbleTyping}`}>
            <span className={styles.cpTypingDot} />
            <span className={styles.cpTypingDot} />
            <span className={styles.cpTypingDot} />
          </div>
        )}
        {!showTyping && text && (
          <div className={[styles.cpBubble, styles.cpBubbleAsst, verdictClass].filter(Boolean).join(' ')}>
            <FormattedText text={text} />
          </div>
        )}
        {reasoning && reasoning.length > 0 && (
          <details className={styles.reasoning}>
            <summary className={styles.reasoningSummary}>Razonamiento</summary>
            {/* PR #414 review pickup 3: render as PLAIN TEXT (no
                markdown parsing). Reasoning is the model's chain-of-
                thought, not formatted output for the user. Same
                rationale as AgentDock — see comment there. */}
            <div className={styles.reasoningBody}>{reasoning}</div>
          </details>
        )}
        {toolChips && toolChips.length > 0 && (
          <div className={styles.toolChipsRow}>
            {toolChips.map((c, i) => (
              <span
                key={i}
                className={[
                  styles.toolChip,
                  c.status === 'pending' ? styles.toolChipPending :
                  c.status === 'ok'      ? styles.toolChipOk :
                                            styles.toolChipError,
                ].join(' ')}
                title={`tool ${c.tool} — ${c.status}`}
              >
                {c.status === 'pending' ? '⋯' : c.status === 'ok' ? '✓' : '✗'} {c.tool}
              </span>
            ))}
          </div>
        )}
        {proposals && proposals.length > 0 && proposals.map((p) => {
          const labelByState: Record<ProposalChip['state'], string> = {
            pending:   'Confirmar',
            in_flight: 'Procesando…',
            ok:        'Confirmado ✓',
            expired:   'Expirado',
            drift:     'Estado cambió — re-pregunta',
            error:     'Falló — re-pregunta',
          };
          const stateClass =
            p.state === 'in_flight' ? styles.toolConfirmInFlight :
            p.state === 'ok'        ? styles.toolConfirmOk :
            (p.state === 'expired' || p.state === 'drift' || p.state === 'error')
                                    ? styles.toolConfirmError :
            '';
          const isInteractive = p.state === 'pending';
          return (
            <div key={p.proposal_id} className={styles.proposalRow}>
              <div className={styles.proposalSummary}>{p.summary}</div>
              <button
                type="button"
                className={[styles.toolConfirm, stateClass].filter(Boolean).join(' ')}
                disabled={!isInteractive}
                onClick={() => isInteractive && onConfirmProposal?.(p.proposal_id)}
              >
                {labelByState[p.state]}
              </button>
            </div>
          );
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

// ============================================================
// Tiny atoms
// ============================================================

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
