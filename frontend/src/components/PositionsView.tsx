// ============================================================
// PositionsView — open-positions-only redesign with per-card
// copilot insight row.
//
// Two intentional changes vs the legacy PositionsPanel:
//   1. Only OPEN positions render here. Closed history will live
//      under "Análisis → Historial" in a future PR.
//   2. Each card carries a deterministic copilot one-liner +
//      conversation-starter CTA. The CTA bubbles `(position,
//      insight)` to the parent, which prompts the AgentDock with
//      full position context.
//
// All data fetching, modal triggers, and SL/TP edit/close flows
// live in the parent (App.tsx). This component is pure render.
// ============================================================

import React, { useMemo } from 'react';
import styles from './PositionsView.module.css';
import type { Position, SymbolStatus, Capital } from '../types';
import { formatPrice } from '../utils';
import { getPositionInsight, type PositionInsight } from '../helpers/position-insight';
import { ObservedOrdersList } from './ObservedOrders';

// ── Public types ─────────────────────────────────────────────

export interface PortfolioSummary {
  equity:   number;
  pnlToday: number;     // % — derive from backend or 0 with TODO
  drawdown: number;     // % from peak (capital.max_drawdown_pct)
}

interface PositionsViewProps {
  positions:        Position[];              // open only
  portfolio:        PortfolioSummary;
  closedRecent7d:   Position[];              // for win-rate + closed P&L hero metric
  freshSetup?:      SymbolStatus | null;     // empty state suggestion
  symbols:          SymbolStatus[];          // for current-price lookup per position
  onOpenSymbol:     (symbol: string) => void;
  onAbrirPosicion:  () => void;
  onAskAgent:       (p: Position, insight: PositionInsight) => void;
  onEditSlTp:       (p: Position) => void;
  onClosePosition:  (p: Position) => void;
  mobile?:          boolean;
}

// ── Helpers ──────────────────────────────────────────────────

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat('es-ES', {
      hour:   '2-digit',
      minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return '—';
  }
}

// Derive the current price for a position. Prefer the live price from
// the symbols watchlist; fall back to entry × (1 + pnl_pct/100).
function currentPriceFor(p: Position, symbols: SymbolStatus[]): number {
  const sym = symbols.find((s) => s.symbol === p.symbol);
  const live = sym?.live_price ?? sym?.price ?? null;
  if (live != null) return live;
  // TODO: when /positions exposes a server-side computed current_price,
  //       use it instead of this derivation (which loses precision when
  //       pnl_pct is rounded server-side).
  return p.entry_price * (1 + (p.pnl_pct ?? 0) / 100);
}

// P&L NO REALIZADO en vivo de una posición abierta, marcado a `currentPrice`.
// `pnl_usd`/`pnl_pct` están NULL hasta el cierre (solo se setean al cerrar),
// así que una abierta mostraría $0.00 — lo computamos contra el precio actual.
function livePnlFor(p: Position, currentPrice: number): { usd: number; pct: number } {
  const qty = p.qty ?? 0;
  const isLong = p.direction === 'LONG';
  const diff = isLong ? (currentPrice - p.entry_price) : (p.entry_price - currentPrice);
  return {
    usd: diff * qty,
    pct: p.entry_price > 0 ? (diff / p.entry_price) * 100 : 0,
  };
}

// ── Main ─────────────────────────────────────────────────────

const PositionsView: React.FC<PositionsViewProps> = ({
  positions, portfolio, closedRecent7d, freshSetup, symbols,
  onOpenSymbol, onAbrirPosicion, onAskAgent, onEditSlTp, onClosePosition,
  mobile = false,
}) => {
  const winningPos = positions.filter((p) => livePnlFor(p, currentPriceFor(p, symbols)).usd > 0).length;
  const losingPos  = positions.filter((p) => livePnlFor(p, currentPriceFor(p, symbols)).usd < 0).length;

  const metrics = useMemo(() => {
    const pnlOpen   = positions.reduce((a, p) => a + livePnlFor(p, currentPriceFor(p, symbols)).usd, 0);
    const pnlClosed = closedRecent7d.reduce((a, p) => a + (p.pnl_usd ?? 0), 0);
    const wins      = closedRecent7d.filter((p) => (p.pnl_usd ?? 0) > 0).length;
    const losses    = closedRecent7d.length - wins;
    const wr        = closedRecent7d.length === 0 ? null : (wins / closedRecent7d.length) * 100;
    const capitalLocked = positions.reduce((a, p) => {
      const px  = currentPriceFor(p, symbols);
      const qty = p.qty ?? 0;
      return a + Math.abs(qty * px);
    }, 0);
    return {
      pnlOpen, pnlClosed, wr,
      wins, losses,
      closedCount:   closedRecent7d.length,
      capitalLocked,
    };
  }, [positions, closedRecent7d, symbols]);

  return (
    <main className={styles.pv}>
      <div className={styles.pageBar}>
        <div className={styles.pageBarTitle}>
          <span className={styles.pageBarIndex}>02</span>
          <span className={styles.pageBarName}>Posiciones</span>
          <span className={styles.pageBarSep}>/</span>
          <span className={`${styles.pageBarHint} prose`}>
            {positions.length === 0
              ? 'sin posiciones abiertas'
              : `${positions.length} abiertas · ${winningPos} verdes · ${losingPos} rojas`}
          </span>
        </div>
        <button className={`btn btn--primary btn--sm ${styles.pageBarCta}`} onClick={onAbrirPosicion}>
          <span className="btn__caret">+</span> Abrir posición
        </button>
      </div>

      {/* Hero metrics strip */}
      <div className={styles.hero}>
        <div className={styles.heroMain}>
          <div className={`${styles.heroLabel} label`}>EQUITY</div>
          <div className={`${styles.heroEquity} num`}>
            ${portfolio.equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className={styles.heroDelta}>
            <span className={`num ${portfolio.pnlToday >= 0 ? styles.heroPnlBull : styles.heroPnlBear}`}>
              {portfolio.pnlToday >= 0 ? '▲' : '▼'} {portfolio.pnlToday >= 0 ? '+' : ''}{portfolio.pnlToday.toFixed(2)}%
            </span>
            <span className={`${styles.heroPnlLbl} prose`}>hoy</span>
            <span className={styles.heroSep}>·</span>
            <span className={`${styles.heroDd} num`}>DD {portfolio.drawdown.toFixed(2)}%</span>
          </div>
        </div>

        <div className={styles.heroMetrics}>
          <MetricCell
            label="P&L abierto"
            value={`${metrics.pnlOpen >= 0 ? '+' : ''}$${metrics.pnlOpen.toFixed(2)}`}
            tone={metrics.pnlOpen >= 0 ? 'bull' : 'bear'}
            sub={`${positions.length} ${positions.length === 1 ? 'posición' : 'posiciones'}`}
          />
          <MetricCell
            label="P&L cerrado (7d)"
            value={`${metrics.pnlClosed >= 0 ? '+' : ''}$${metrics.pnlClosed.toFixed(2)}`}
            tone={metrics.pnlClosed >= 0 ? 'bull' : 'bear'}
            // TODO: when the "Análisis → Historial" route exists, wire this link
            sub={
              <>
                {metrics.closedCount} operaciones ·{' '}
                <span
                  className={styles.mcSubLink}
                  title="próximamente: Análisis → Historial"
                >ver historial →</span>
              </>
            }
          />
          <MetricCell
            label="Win rate (7d)"
            value={metrics.wr == null ? '—' : `${metrics.wr.toFixed(0)}%`}
            tone={metrics.wr == null ? 'dim' : metrics.wr >= 60 ? 'bull' : metrics.wr >= 40 ? 'warn' : 'bear'}
            sub={metrics.wr == null ? 'sin operaciones' : `${metrics.wins} wins · ${metrics.losses} losses`}
          />
          <MetricCell
            label="Capital en uso"
            value={`$${metrics.capitalLocked.toFixed(0)}`}
            tone="neutral"
            sub={portfolio.equity > 0
              ? `${((metrics.capitalLocked / portfolio.equity) * 100).toFixed(1)}% del equity`
              : '—'}
          />
        </div>
      </div>

      {/* Section header */}
      <div className={styles.secHd}>
        <span className={`label ${styles.secHdLabel}`}>▸ Abiertas</span>
        <span className={styles.secHdCount}>{positions.length}</span>
        <span className={`prose ${styles.secHdHint}`}>
          posiciones vivas · el historial completo está en Análisis →
        </span>
      </div>

      {positions.length === 0 ? (
        <EmptyState
          freshSetup={freshSetup ?? null}
          onOpenSymbol={onOpenSymbol}
          onAbrirPosicion={onAbrirPosicion}
        />
      ) : (
        <div className={styles.cards}>
          {positions.map((p) => (
            <PositionCard
              key={p.id}
              position={p}
              currentPrice={currentPriceFor(p, symbols)}
              onView={() => onOpenSymbol(p.symbol)}
              onAskAgent={onAskAgent}
              onEditSlTp={onEditSlTp}
              onClosePosition={onClosePosition}
              mobile={mobile}
            />
          ))}
        </div>
      )}
    </main>
  );
};

// ── Hero metric cell ─────────────────────────────────────────

type MetricTone = 'bull' | 'bear' | 'warn' | 'neutral' | 'dim';
const MetricCell: React.FC<{
  label: string;
  value: string;
  tone:  MetricTone;
  sub?:  React.ReactNode;
}> = ({ label, value, tone, sub }) => {
  const toneClass =
    tone === 'bull'    ? styles.mcValBull :
    tone === 'bear'    ? styles.mcValBear :
    tone === 'warn'    ? styles.mcValWarn :
    tone === 'dim'     ? styles.mcValDim  :
                         styles.mcValNeutral;
  return (
    <div className={styles.mc}>
      <div className={`${styles.mcLabel} label`}>{label}</div>
      <div className={`${styles.mcVal} ${toneClass} num`}>{value}</div>
      {sub && <div className={`${styles.mcSub} prose`}>{sub}</div>}
    </div>
  );
};

// ── Position card ────────────────────────────────────────────

interface PositionCardProps {
  position:        Position;
  currentPrice:    number;
  onView:          () => void;
  onAskAgent:      (p: Position, insight: PositionInsight) => void;
  onEditSlTp:      (p: Position) => void;
  onClosePosition: (p: Position) => void;
  mobile?: boolean;
}
const PositionCard: React.FC<PositionCardProps> = ({
  position: p, currentPrice, onView, onAskAgent, onEditSlTp, onClosePosition,
}) => {
  const isLong  = p.direction === 'LONG';
  // P&L no realizado en vivo (abierta): contra el precio actual, no el
  // pnl_usd/pnl_pct guardado (NULL hasta el cierre).
  const { usd: pnlAbs, pct: pnlPct } = livePnlFor(p, currentPrice);
  const tone: 'bull' | 'bear' = pnlAbs >= 0 ? 'bull' : 'bear';
  const insight = useMemo(() => getPositionInsight(p, currentPrice), [p, currentPrice]);

  const hasGauge = p.sl_price != null && p.tp_price != null;
  const sl = p.sl_price ?? 0;
  const tp = p.tp_price ?? 0;
  const lo = hasGauge ? Math.min(sl, p.entry_price, tp, currentPrice) : 0;
  const hi = hasGauge ? Math.max(sl, p.entry_price, tp, currentPrice) : 1;
  const span = (hi - lo) || 1;
  const posOf = (price: number) => ((price - lo) / span) * 100;

  const distSlPct = hasGauge ? ((currentPrice - sl) / currentPrice * 100) * (isLong ? 1 : -1) : 0;
  const distTpPct = hasGauge ? ((tp - currentPrice) / currentPrice * 100) * (isLong ? 1 : -1) : 0;

  return (
    <article className={`${styles.card} ${tone === 'bull' ? styles.cardBull : styles.cardBear}`}>
      <header className={styles.cardHead}>
        <div className={styles.cardId}>
          <div className={`${styles.side} ${isLong ? styles.sideLong : styles.sideShort}`}>
            <span className={styles.sideShape}>{isLong ? '▲' : '▼'}</span>
            <span className={styles.sideLetter}>{isLong ? 'L' : 'S'}</span>
          </div>
          <div className={styles.pair}>
            <span className={styles.pairBase}>{p.symbol.replace('USDT', '')}</span>
            <span className={styles.pairQuote}>/USDT</span>
          </div>
          <div className={`${styles.opened} prose`}>abierta {fmtTime(p.entry_ts)}</div>
        </div>
        <div className={styles.pnl}>
          <div className={`${styles.pnlPct} num ${tone === 'bull' ? styles.pnlPctBull : styles.pnlPctBear}`}>
            {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
          </div>
          <div className={`${styles.pnlAbs} num ${tone === 'bull' ? styles.pnlAbsBull : styles.pnlAbsBear}`}>
            {pnlAbs >= 0 ? '+' : ''}${pnlAbs.toFixed(2)}
          </div>
        </div>
      </header>

      <div className={styles.cardBody}>
        {/* Gauge or placeholder when SL/TP missing */}
        {hasGauge ? (
          <div className={styles.gauge}>
            <div className={styles.gaugeTrack}>
              <div
                className={`${styles.gaugeFill} ${tone === 'bull' ? styles.gaugeFillBull : styles.gaugeFillBear}`}
                style={{
                  left:  `${Math.min(posOf(p.entry_price), posOf(currentPrice))}%`,
                  width: `${Math.abs(posOf(currentPrice) - posOf(p.entry_price))}%`,
                }}
              />
            </div>
            {[
              { key: 'sl' as const,    val: sl,            cls: styles.gaugeMarkSl },
              { key: 'entry' as const, val: p.entry_price, cls: styles.gaugeMarkEntry },
              { key: 'now' as const,   val: currentPrice,  cls: styles.gaugeMarkNow },
              { key: 'tp' as const,    val: tp,            cls: styles.gaugeMarkTp },
            ].map((m) => (
              <div key={m.key} className={`${styles.gaugeMark} ${m.cls}`} style={{ left: `${posOf(m.val)}%` }}>
                <span className={styles.gaugeMarkLbl}>{m.key.toUpperCase()}</span>
                <span className={`${styles.gaugeMarkVal} num`}>{formatPrice(m.val)}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className={`${styles.gaugeMissing} prose`}>
            Define SL y TP para ver el gauge de proximidad.
          </div>
        )}

        <div className={styles.cardMeta}>
          <KV label="Cantidad"   value={(p.qty ?? 0).toString()} tone="neutral" />
          <KV label="Valor pos." value={`$${((p.qty ?? 0) * currentPrice).toFixed(2)}`} tone="neutral" />
          {hasGauge ? (
            <>
              <KV label="A SL" value={`${Math.abs(distSlPct).toFixed(2)}%`} tone="bear" />
              <KV label="A TP" value={`${Math.abs(distTpPct).toFixed(2)}%`} tone="bull" />
            </>
          ) : (
            <>
              <KV label="A SL" value="—" tone="neutral" />
              <KV label="A TP" value="—" tone="neutral" />
            </>
          )}
        </div>

        {p.observed_orders && <ObservedOrdersList orders={p.observed_orders} />}
      </div>

      {/* Insight row */}
      {insight && (
        <button
          type="button"
          className={[styles.insight, toneClassForInsight(insight.tone)].filter(Boolean).join(' ')}
          onClick={() => onAskAgent(p, insight)}
          title="Conversar con el copiloto sobre esta posición"
        >
          <span className={`${styles.insightAvatar} ${avatarClassForInsight(insight.tone)}`}>
            <span className={styles.insightGlyph}>{insight.glyph}</span>
          </span>
          <span className={styles.insightBody}>
            <span className={styles.insightTag}>copiloto</span>
            <span className={`${styles.insightText} prose`}>{insight.text}</span>
          </span>
          <span
            className={`${styles.insightCta} ${ctaClassForInsight(insight.tone)}`}
            onClick={(e) => { e.stopPropagation(); onAskAgent(p, insight); }}
          >
            {insight.action} <span className={styles.insightCtaArrow}>→</span>
          </span>
        </button>
      )}

      <footer className={styles.cardFt}>
        <button className="btn btn--ghost btn--sm" onClick={() => onEditSlTp(p)}>Editar SL/TP</button>
        <button className="btn btn--ghost btn--sm" onClick={onView}>Ver gráfico →</button>
        <button className={`btn btn--danger btn--sm ${styles.cardClose}`} onClick={() => onClosePosition(p)}>Cerrar</button>
      </footer>
    </article>
  );
};

function toneClassForInsight(tone: PositionInsight['tone']): string {
  if (tone === 'bull')    return styles.insightBull;
  if (tone === 'bear')    return styles.insightBear;
  if (tone === 'warn')    return styles.insightWarn;
  return '';
}
function avatarClassForInsight(tone: PositionInsight['tone']): string {
  if (tone === 'bull')    return styles.insightAvatarBull;
  if (tone === 'bear')    return styles.insightAvatarBear;
  if (tone === 'warn')    return styles.insightAvatarWarn;
  if (tone === 'neutral') return styles.insightAvatarNeutral;
  return styles.insightAvatarDim;
}
function ctaClassForInsight(tone: PositionInsight['tone']): string {
  if (tone === 'bull')    return styles.insightCtaBull;
  if (tone === 'bear')    return styles.insightCtaBear;
  if (tone === 'warn')    return styles.insightCtaWarn;
  if (tone === 'neutral') return styles.insightCtaNeutral;
  return styles.insightCtaDim;
}

// ── KV cell ──────────────────────────────────────────────────

const KV: React.FC<{ label: string; value: string; tone: MetricTone }> = ({ label, value, tone }) => {
  const toneClass =
    tone === 'bull' ? styles.kvBull :
    tone === 'bear' ? styles.kvBear :
    tone === 'warn' ? styles.kvWarn :
                      styles.kvNeutral;
  return (
    <div className={`${styles.kv} ${toneClass}`}>
      <span className={`${styles.kvLabel} label`}>{label}</span>
      <span className={`${styles.kvVal} num`}>{value}</span>
    </div>
  );
};

// ── Empty state with smart suggestion ────────────────────────

const EmptyState: React.FC<{
  freshSetup:      SymbolStatus | null;
  onOpenSymbol:    (symbol: string) => void;
  onAbrirPosicion: () => void;
}> = ({ freshSetup, onOpenSymbol, onAbrirPosicion }) => (
  <section className={styles.empty}>
    <div className={styles.emptyMark}>∅</div>
    <div className={styles.emptyTitle}>Sin posiciones abiertas</div>
    <div className={`${styles.emptyBody} prose`}>
      El capital está disponible. Cuando el escáner dispare un setup firme, podrás abrirlo desde aquí o desde el detalle del par.
    </div>

    {freshSetup && (
      <div className={styles.emptySuggest}>
        <div className={`${styles.emptySuggestLbl} label`}>Sugerencia · score más alto ahora</div>
        <div className={styles.emptySuggestRow}>
          <div className={styles.emptySuggestPair}>
            <span className={styles.emptySuggestSide}>{(freshSetup.direction ?? 'LONG') === 'LONG' ? '▲ L' : '▼ S'}</span>
            <span className={styles.emptySuggestBase}>{freshSetup.symbol.replace('USDT', '')}</span>
            <span className={styles.emptySuggestQuote}>/USDT</span>
          </div>
          <div className={styles.emptySuggestScore}>
            score <span className="num">{freshSetup.score ?? 0}/9</span>
          </div>
          <div className={styles.emptySuggestPrice}>
            <span className="num">${formatPrice(freshSetup.live_price ?? freshSetup.price)}</span>
          </div>
          <button className="btn btn--primary btn--sm" onClick={() => onOpenSymbol(freshSetup.symbol)}>
            Revisar {freshSetup.symbol.replace('USDT', '')} →
          </button>
        </div>
      </div>
    )}

    <button className={`btn btn--ghost btn--sm ${styles.emptyManual}`} onClick={onAbrirPosicion}>
      o abrir una posición manualmente
    </button>
  </section>
);

// ============================================================
// NOTE: HistoryTable / HistoryCard from the mock are intentionally
// NOT included here. The closed-positions history will get its own
// dedicated view under "Análisis → Historial" in a future PR with
// window selectors (7d/30d/90d), per-symbol filter, and export.
// Keep this file focused on open positions.
// ============================================================

// Used by Capital → PortfolioSummary conversion in the caller.
export type { Capital };

export default PositionsView;
