// ============================================================
// MercadoView.tsx — pestaña Mercado en el lenguaje CÁLIDO.
//
// Port del handoff del equipo de diseño (mercado-warm.jsx) a TSX
// con datos reales. Subsume SymbolsGrid + SignalsTable + el slot
// belowPageBar (StatusBar/FocusPanel) en una sola columna editorial.
//
// Doctrina: hechos, nunca veredictos. El orden es por cuántas
// condiciones coinciden (filtro mecánico). La frescura es honesta
// (fresco / rancio / muerto se ven distinto). El estado "escáner
// caído" NO muestra pares viejos disfrazados de actuales.
//
// Estilos: hoja global frontend/src/styles/mercado-warm.css (.mw-*).
// Tokens cálidos: frontend/src/styles/warm-tokens.css.
// ============================================================

import React, { useMemo, useState } from 'react';
import type {
  SymbolStatus, Signal, StatusResponse, Frescura, ScoreComponent,
} from '../types';
import type { MacroResponse } from '../api';
import type { FocusItem, FocusKind } from '../helpers/hierarchy';
import { bucketSymbols } from '../helpers/hierarchy';

// ── nombres legibles (mismo léxico que Valles) ──────────────
const MW_NAMES: Record<string, string> = {
  BTC: 'Bitcoin', ETH: 'Ethereum', ADA: 'Cardano', AVAX: 'Avalanche', DOGE: 'Dogecoin',
  UNI: 'Uniswap', XLM: 'Stellar', PENDLE: 'Pendle', JUP: 'Jupiter', RUNE: 'THORChain',
  SOL: 'Solana', BNB: 'BNB', XRP: 'XRP', LINK: 'Chainlink', DOT: 'Polkadot',
  MATIC: 'Polygon', LTC: 'Litecoin', ATOM: 'Cosmos', NEAR: 'NEAR', APT: 'Aptos',
};
const pairOf = (symbol: string): string => symbol.replace(/USDT$/, '');
const mwName = (p: string): string => MW_NAMES[p] || p;

// ── etiquetas de las 7 condiciones reales del scanner (C1–C7).
//    Neutrales a dirección (sirven para largo y corto) y en
//    español venezolano. claro = plain · técnico = label. ──────
const CONDITION_META: Record<string, { label: string; plain: string }> = {
  RSI: { label: 'RSI',         plain: 'RSI en zona extrema' },
  DIV: { label: 'Divergencia', plain: 'Divergencia en el oscilador' },
  SR:  { label: 'Nivel',       plain: 'Precio cerca de un nivel clave' },
  BB:  { label: 'Bollinger',   plain: 'Precio en la banda de Bollinger' },
  VOL: { label: 'Volumen',     plain: 'El volumen acompaña' },
  CVD: { label: 'Presión',     plain: 'La presión de volumen acompaña' },
  SMA: { label: 'Medias',      plain: 'Las medias móviles se cruzaron a favor' },
};
const condName = (key: string, lenguaje: Lenguaje): string => {
  const m = CONDITION_META[key];
  if (!m) return key;
  return lenguaje === 'claro' ? m.plain : m.label;
};

type Lenguaje = 'claro' | 'tecnico';
type Side = 'LONG' | 'SHORT';

// ── formato ─────────────────────────────────────────────────
function mwPrice(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1000) return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(5);
}
function mwChg(n: number): string {
  return (n >= 0 ? '+' : '−') + Math.abs(n).toFixed(1) + '%';
}
function mwEdad(seg: number | null): string | null {
  if (seg == null) return null;
  if (seg < 90) return 'hace ' + Math.round(seg) + 's';
  if (seg < 5400) return 'hace ' + Math.round(seg / 60) + ' min';
  if (seg < 172800) return 'hace ' + Math.round(seg / 3600) + ' h';
  return 'hace ' + Math.round(seg / 86400) + ' días';
}
function minsSince(ts: string | null, now: number): number | null {
  if (!ts) return null;
  const t = new Date(ts.replace(' UTC', 'Z')).getTime();
  if (!Number.isFinite(t)) return null;
  return Math.max(0, (now - t) / 60_000);
}
function lastSignalTxt(mins: number | null): string {
  if (mins == null) return 'sin registro';
  if (mins < 60) return `hace ${Math.round(mins)} min`;
  if (mins < 1440) return `hace ${Math.floor(mins / 60)} h`;
  return `hace ${Math.floor(mins / 1440)} días`;
}
function fngLabel(v: number): string {
  return v < 25 ? 'Miedo' : v < 45 ? 'Cautela' : v < 55 ? 'Neutral' : v < 75 ? 'Codicia' : 'Euforia';
}

// ── sparkline → path ────────────────────────────────────────
function sparkPath(arr: number[], w: number, h: number): string {
  if (!arr || arr.length < 2) return '';
  const min = Math.min(...arr), max = Math.max(...arr);
  const range = (max - min) || 1;
  const dx = w / (arr.length - 1);
  return arr.map((v, i) => `${i ? 'L' : 'M'}${(i * dx).toFixed(1)} ${(h - ((v - min) / range) * h).toFixed(1)}`).join(' ');
}
const Spark: React.FC<{ arr?: number[]; w?: number; h?: number }> = ({ arr, w = 96, h = 30 }) => {
  if (!arr || arr.length < 2) return null;
  const up = arr[arr.length - 1] >= arr[0];
  return (
    <svg className="mw-spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path d={sparkPath(arr, w, h)} style={{ stroke: up ? 'var(--sage)' : 'var(--down)' }} />
    </svg>
  );
};

// ── FRESCURA — semáforo corto ───────────────────────────────
const Fresh: React.FC<{ frescura: Frescura | null; noun?: string }> = ({ frescura, noun = 'foto' }) => {
  if (!frescura) return null;
  const e = frescura.estado;
  if (e === 'muerto') return <span className="mw-fresh mw-fresh--muerto"><span className="mw-fresh__dot" /> sin {noun} todavía</span>;
  if (e === 'rancio') return <span className="mw-fresh mw-fresh--rancio"><span className="mw-fresh__dot" /> {noun} de {mwEdad(frescura.edad_seg)} · pudo cambiar</span>;
  return <span className="mw-fresh mw-fresh--fresco"><span className="mw-fresh__dot" /> {noun} de {mwEdad(frescura.edad_seg)} · al día</span>;
};

// ── ESTADO DEL MERCADO ──────────────────────────────────────
const REGIME_TXT: Record<string, { word: string; note: string }> = {
  BULL:    { word: 'Al alza',   note: 'La tendencia general empuja para arriba.' },
  BEAR:    { word: 'A la baja',  note: 'La tendencia general empuja para abajo.' },
  NEUTRAL: { word: 'Sin rumbo',  note: 'El mercado no marca una dirección clara.' },
};

const MarketBand: React.FC<{ macro: MacroResponse | null; lenguaje: Lenguaje }> = ({ macro, lenguaje }) => {
  const regime = macro?.regime ?? null;
  const r = (regime && REGIME_TXT[regime]) || REGIME_TXT.NEUTRAL;
  const rclass = regime === 'BULL' ? 'mw-regime--bull' : regime === 'BEAR' ? 'mw-regime--bear' : 'mw-regime--neutral';
  const fng = macro?.fear_greed_index ?? null;
  const funding = macro?.funding_rate_pct ?? null;
  return (
    <section className="mw-market">
      <div className={`mw-mkt ${rclass}`}>
        <div className="mw-mkt__k">{lenguaje === 'claro' ? 'Hacia dónde va el mercado' : 'Régimen'}</div>
        <div className="mw-mkt__v mw-regime"><span className="mw-regime__dot" />{r.word}</div>
        <div className="mw-mkt__note">{r.note}</div>
      </div>
      <div className="mw-mkt">
        <div className="mw-mkt__k">{lenguaje === 'claro' ? 'Ánimo de la gente' : 'Miedo / Codicia'}</div>
        <div className="mw-mkt__v">
          {fng != null ? fng : '—'}
          {fng != null && <span style={{ fontSize: 15, color: 'var(--ink-3)', fontWeight: 500 }}> · {fngLabel(fng)}</span>}
        </div>
        {fng != null && (
          <div className="mw-fng"><div className="mw-fng__fill" style={{ width: '100%' }} /><div className="mw-fng__pin" style={{ left: `${fng}%` }} /></div>
        )}
        <div className="mw-mkt__note">0 = miedo · 100 = codicia</div>
      </div>
      <div className="mw-mkt">
        <div className="mw-mkt__k">{lenguaje === 'claro' ? 'Costo de los largos' : 'Funding'}</div>
        <div className="mw-mkt__v">{funding != null ? funding.toFixed(3) + '%' : '—'}</div>
        <div className="mw-mkt__note">{funding != null && funding >= 0 ? 'los largos pagan a los cortos' : 'los cortos pagan a los largos'}</div>
      </div>
    </section>
  );
};

// ── CARTERA ─────────────────────────────────────────────────
export interface MercadoWallet {
  equity:        number;
  peak:          number;
  drawdown:      number;   // porcentaje (negativo o 0)
  pnlToday:      number;
  openCount:     number;
  capitalLocked: number;
}
const Wallet: React.FC<{ w: MercadoWallet; lenguaje: Lenguaje }> = ({ w, lenguaje }) => (
  <section className="mw-wallet">
    <div className="mw-wallet__hero">
      <div className="mw-wallet__k">{lenguaje === 'claro' ? 'Lo que tienes' : 'Equity'}</div>
      <div className="mw-wallet__equity">${mwPrice(w.equity)}</div>
      <div className="mw-wallet__sub">
        Pico <b>${mwPrice(w.peak)}</b> · {lenguaje === 'claro' ? 'bajada desde el pico' : 'drawdown'} <b className={w.drawdown < 0 ? 'mw-down' : ''}>{w.drawdown.toFixed(2)}%</b>
      </div>
    </div>
    <div className="mw-wallet__stats">
      <div>
        <div className="mw-stat__k">{lenguaje === 'claro' ? 'Hoy' : 'P&L hoy'}</div>
        <div className={`mw-stat__v ${w.pnlToday >= 0 ? 'mw-up' : 'mw-down'}`}>{w.pnlToday >= 0 ? '+' : '−'}${Math.abs(w.pnlToday).toFixed(2)}</div>
      </div>
      <div>
        <div className="mw-stat__k">{lenguaje === 'claro' ? 'Abiertas' : 'Posiciones'}</div>
        <div className="mw-stat__v">{w.openCount}</div>
      </div>
      <div>
        <div className="mw-stat__k">{lenguaje === 'claro' ? 'En uso' : 'Capital'}</div>
        <div className="mw-stat__v">${mwPrice(w.capitalLocked)}</div>
      </div>
    </div>
  </section>
);

// ── PANEL DE ATENCIÓN ───────────────────────────────────────
const ATT_META: Record<FocusKind, { cls: string; icon: string }> = {
  'risk-position': { cls: 'mw-att--risk', icon: '⚠' },
  'fresh-signal':  { cls: 'mw-att--fresh', icon: '✦' },
  'near-tp':       { cls: 'mw-att--tp', icon: '◎' },
  'kill-switch':   { cls: 'mw-att--sys', icon: '⏸' },
  'error':         { cls: 'mw-att--sys', icon: '!' },
};
const FocusBlock: React.FC<{ items: FocusItem[]; onOpen: (pair: string) => void }> = ({ items, onOpen }) => (
  <section>
    <div className="mw-sec__head">
      <span className="mw-sec__label">Qué mirar ahora</span>
      <span className="mw-sec__rule" />
      <span className="mw-sec__hint">lo más urgente primero</span>
    </div>
    {items.length === 0 ? (
      <div className="mw-callout mw-callout--mute">
        <span className="mw-callout__icon">✓</span>
        <div>
          <div className="mw-callout__title">Nada urgente ahora mismo</div>
          <div className="mw-callout__sub">No hay posiciones en riesgo ni señales frescas que pidan tu atención. Buen momento para no hacer nada.</div>
        </div>
      </div>
    ) : (
      <div className="mw-focus">
        {items.map((it, i) => {
          const m = ATT_META[it.kind] || ATT_META.error;
          return (
            <button className={`mw-att ${m.cls}`} key={i} onClick={() => it.pair && onOpen(it.pair)}>
              <span className="mw-att__icon">{m.icon}</span>
              <span className="mw-att__body">
                <span className="mw-att__title">{it.title}</span>
                <span className="mw-att__say">{it.body}</span>
              </span>
              <span className="mw-att__go">{it.action} <span>→</span></span>
            </button>
          );
        })}
      </div>
    )}
  </section>
);

// ── piezas de tarjeta ───────────────────────────────────────
const CondTicks: React.FC<{ components: ScoreComponent[]; lenguaje: Lenguaje }> = ({ components, lenguaje }) => {
  if (!components.length) return null;
  return (
    <div className="mw-cond">
      <div className="mw-cond__ticks">
        {components.map((c, i) => (
          <span key={i} className={`mw-cond__tick ${c.pass ? 'mw-cond__tick--on' : ''}`} title={condName(c.key, lenguaje)} />
        ))}
      </div>
    </div>
  );
};

const SideBadge: React.FC<{ side: Side }> = ({ side }) => (
  <span className={`mw-side mw-side--${side === 'LONG' ? 'L' : 'S'}`}>{side === 'LONG' ? 'LARGO' : 'CORTO'}</span>
);

const Lrc: React.FC<{ lrc: number; lenguaje: Lenguaje }> = ({ lrc, lenguaje }) => {
  const pin = Math.max(0, Math.min(100, lrc));
  return (
    <div className="mw-lrc">
      <span className="mw-lrc__k">{lenguaje === 'claro' ? 'Canal' : 'LRC'} <b>{lrc.toFixed(1)}%</b></span>
      <span className="mw-lrc__bar"><span className="mw-lrc__zone" /><span className="mw-lrc__pin" style={{ left: pin + '%' }} /></span>
    </div>
  );
};

const Trig: React.FC<{ on: boolean }> = ({ on }) => (
  <span className={`mw-trig ${on ? '' : 'mw-trig--off'}`}><i />{on ? 'gatillo activo' : 'sin gatillo'}</span>
);

const VerNumeros: React.FC<{ s: SymbolStatus; components: ScoreComponent[]; lenguaje: Lenguaje }> = ({ s, components, lenguaje }) => {
  const [open, setOpen] = useState(false);
  const chg = s.change_24h;
  const items: Array<{ k: string; v: string; on?: boolean }> = [
    ...(chg != null ? [{ k: lenguaje === 'claro' ? 'Cambio 24h' : 'Δ 24h', v: mwChg(chg) }] : []),
    { k: lenguaje === 'claro' ? 'Distancia al canal' : 'LRC', v: (s.lrc_pct ?? 0).toFixed(1) + '%' },
    ...(s.direction ? [{ k: lenguaje === 'claro' ? 'Lado' : 'Side', v: s.direction === 'LONG' ? 'Largo' : 'Corto' }] : []),
    ...components.map((c) => ({ k: condName(c.key, lenguaje) + (c.value ? ` · ${c.value}` : ''), v: c.pass ? 'coincide' : 'no', on: c.pass })),
  ];
  return (
    <div className="mw-more">
      <button className="mw-more__toggle" onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}>
        <span className={`mw-more__caret ${open ? 'mw-more__caret--open' : ''}`}>▸</span>
        {open ? 'Ocultar los números' : 'Ver los números'}
      </button>
      {open && (
        <div className="mw-more__panel" onClick={(e) => e.stopPropagation()}>
          {items.map((it, i) => (
            <div className={`mw-num ${it.on === true ? 'mw-num--on' : it.on === false ? 'mw-num--off' : ''}`} key={i}>
              <div className="mw-num__k">{it.k}</div>
              <div className="mw-num__v">{it.v}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── TARJETA DE PAR ──────────────────────────────────────────
type Variant = 'featured' | 'watching' | 'quiet';
const VARIANT_TAG: Record<Variant, { cls: string; txt: string }> = {
  featured: { cls: 'ready', txt: 'listo' },
  watching: { cls: 'near', txt: 'acercándose' },
  quiet:    { cls: 'quiet', txt: 'quieto' },
};

const MercadoCard: React.FC<{ s: SymbolStatus; variant: Variant; lenguaje: Lenguaje; onClick: () => void }> = ({ s, variant, lenguaje, onClick }) => {
  const pair = pairOf(s.symbol);
  const components = s.score_components ?? [];
  const passed = components.filter((c) => c.pass).length;
  const total = components.length;
  const featured = variant === 'featured';
  const quiet = variant === 'quiet';
  const tag = VARIANT_TAG[variant];
  const price = s.live_price ?? s.price;
  const chg = s.change_24h;
  return (
    <div className={`mw-card mw-card--${variant}`} role="button" tabIndex={0} onClick={onClick}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(); } }}>
      <div className="mw-card__head">
        <div>
          <div className="mw-card__name">{mwName(pair)}</div>
          <div className="mw-card__sym">{pair}/USDT</div>
        </div>
        {s.direction && <SideBadge side={s.direction} />}
      </div>
      <div className="mw-card__pricerow">
        <div className="mw-card__pxgroup">
          <span className="mw-card__price">${mwPrice(price)}</span>
          {chg != null && (
            <span className={`mw-card__chg ${chg >= 0 ? 'mw-up' : 'mw-down'}`}>{chg >= 0 ? '▲' : '▼'} {mwChg(chg)}</span>
          )}
        </div>
        <Spark arr={s.recent_closes} w={featured ? 92 : 70} h={featured ? 30 : 24} />
      </div>
      <div className="mw-card__status">
        <span className={`mw-tagpill mw-tagpill--${tag.cls}`}><span className="mw-tagpill__dot" />{tag.txt}</span>
        {total > 0 ? (
          <span className="mw-scorefact"><b>{passed} de {total}</b> {total === 1 ? 'señal' : 'señales'}{quiet ? '' : ' coinciden'}</span>
        ) : s.score != null ? (
          <span className="mw-scorefact">puntaje <b>{s.score}/9</b></span>
        ) : null}
      </div>
      <CondTicks components={components} lenguaje={lenguaje} />
      {!quiet && s.lrc_pct != null && <Lrc lrc={s.lrc_pct} lenguaje={lenguaje} />}
      {!quiet && (
        <div className="mw-card__foot">
          <Trig on={s.gatillo} />
          <span className="mw-card__cta">Ver detalle <span>→</span></span>
        </div>
      )}
      {featured && <VerNumeros s={s} components={components} lenguaje={lenguaje} />}
    </div>
  );
};

// ── LISTA DE PARES ──────────────────────────────────────────
interface Buckets { featured: SymbolStatus[]; watching: SymbolStatus[]; quiet: SymbolStatus[]; }
const SECTION_META: Array<{ k: keyof Buckets; label: string; hint: string }> = [
  { k: 'featured', label: 'Listos para mirar', hint: 'casi todas las señales, con gatillo' },
  { k: 'watching', label: 'Acercándose',       hint: 'algunas señales, todavía sin gatillo' },
  { k: 'quiet',    label: 'Quietos',            hint: 'sin nada que hacer ahora' },
];
const PairsBlock: React.FC<{ buckets: Buckets; lenguaje: Lenguaje; onOpen: (s: SymbolStatus) => void }> = ({ buckets, lenguaje, onOpen }) => (
  <>
    {SECTION_META.map(({ k, label, hint }) => {
      const list = buckets[k];
      if (!list.length) return null;
      return (
        <section key={k}>
          <div className="mw-sec__head">
            <span className="mw-sec__label">{label}</span>
            <span className="mw-sec__count">{list.length}</span>
            <span className="mw-sec__rule" />
            <span className="mw-sec__hint">{hint}</span>
          </div>
          <div className={`mw-grid mw-grid--${k}`}>
            {list.map((s) => (
              <MercadoCard key={s.symbol} s={s} variant={k} lenguaje={lenguaje} onClick={() => onOpen(s)} />
            ))}
          </div>
        </section>
      );
    })}
  </>
);

// ── SEÑALES RECIENTES ───────────────────────────────────────
function sigState(sig: Signal, lenguaje: Lenguaje): string {
  if (sig.señal) return lenguaje === 'claro' ? 'setup firme' : 'SETUP_OK';
  if (sig.setup) return lenguaje === 'claro' ? 'cerca del canal' : 'SETUP_LRC';
  return lenguaje === 'claro' ? 'sin gatillo' : 'NO_TRIGGER';
}
const SignalsBlock: React.FC<{ signals: Signal[]; lenguaje: Lenguaje; now: number; onOpen: (sig: Signal) => void }> = ({ signals, lenguaje, now, onOpen }) => {
  const rows = signals.slice(0, 12);
  return (
    <section>
      <div className="mw-sec__head">
        <span className="mw-sec__label">Señales recientes</span>
        <span className="mw-sec__rule" />
        <span className="mw-sec__hint">lo último que disparó el escáner</span>
      </div>
      <div className="mw-signals">
        <div className="mw-sig mw-sig--head">
          <span>Hora</span><span>Par</span><span>Precio</span><span>Estado</span><span />
        </div>
        {rows.length === 0 ? (
          <div className="mw-sig" style={{ color: 'var(--ink-3)', cursor: 'default' }}>
            <span>—</span><span>Sin señales recientes</span><span /><span /><span />
          </div>
        ) : rows.map((sig) => {
          const mins = minsSince(sig.ts, now);
          const fresh = mins != null && mins <= 5;
          const pair = pairOf(sig.symbol);
          return (
            <div className="mw-sig" key={sig.id} onClick={() => onOpen(sig)}>
              <span className="mw-sig__time">{mins != null ? lastSignalTxt(mins) : '—'}</span>
              <span className="mw-sig__pair">{mwName(pair)}</span>
              <span className="mw-sig__price">${mwPrice(sig.price)}</span>
              <span className={`mw-sig__state ${!sig.gatillo ? 'mw-sig__muted' : ''}`}>{sigState(sig, lenguaje)}</span>
              <span>{fresh && <span className="mw-sig__fresh"><i />nuevo</span>}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
};

// ════════════════════════════════════════════════════════════
// MercadoView
// ════════════════════════════════════════════════════════════
export interface MercadoViewProps {
  symbols:        SymbolStatus[];
  loading:        boolean;
  macro:          MacroResponse | null;
  wallet:         MercadoWallet;
  signals:        Signal[];
  status:         StatusResponse | null;
  frescura?:      Frescura | null;
  focus:          FocusItem[];
  onSymbolClick:  (s: SymbolStatus) => void;
  onOpenSignal:   (sig: Signal) => void;
  onRescan?:      () => void;
  mobile?:        boolean;
  lenguaje?:      Lenguaje;
  /** Slot del copiloto (AgentBrief) cuando AGENT_ENABLED. Si se pasa, se
   *  renderiza en lugar del FocusBlock calculado (preserva el o-uno-o-otro
   *  del comportamiento actual). */
  agentSlot?:     React.ReactNode;
}

const MercadoView: React.FC<MercadoViewProps> = ({
  symbols, loading, macro, wallet, signals, status, frescura,
  focus, onSymbolClick, onOpenSignal, onRescan, mobile = false,
  lenguaje = 'claro', agentSlot,
}) => {
  const now = Date.now();
  const buckets = useMemo(() => bucketSymbols(symbols) as Buckets, [symbols]);

  // Frescura honesta: usa la del backend (/symbols); si falta (backend viejo),
  // deriva una mínima del estado del scanner para no inventar "al día".
  const fresh: Frescura = frescura ?? {
    estado: status?.scanner_state?.running ? 'fresco' : 'muerto',
    edad_seg: null,
    generated_at: status?.scanner_state?.last_scan_ts ?? null,
    umbral_seg: 900,
  };
  const dead = fresh.estado === 'muerto';
  const errors = status?.scanner_state?.errors ?? 0;
  const scansTotal = status?.scanner_state?.scans_total ?? 0;
  const scannerRunning = status?.scanner_state?.running ?? false;

  const openByPair = (pair: string): void => {
    const full = pair.endsWith('USDT') ? pair : `${pair}USDT`;
    const sym = symbols.find((s) => s.symbol === full);
    if (sym) onSymbolClick(sym);
  };

  return (
    <div className={`mw ${mobile ? 'mw--mobile' : 'mw--desktop'}`}>
      {/* barra superior de la pestaña: título + frescura honesta + escaneo */}
      <div className="mw-top">
        <div className="mw-brand">
          <span className="mw-brand__mark">M</span>
          <span className="mw-brand__name">Mercado</span>
          <span className="mw-brand__tag">qué se mueve y qué está quieto</span>
        </div>
        <div className="mw-top__right">
          <Fresh frescura={fresh} noun="foto" />
          {scannerRunning
            ? <span className="mw-scan"><span className="mw-scan__dot" /> escaneando · {scansTotal.toLocaleString('es-ES')} ciclos</span>
            : <span className="mw-scan" style={{ color: 'var(--ochre)' }}>escáner detenido</span>}
        </div>
      </div>

      <div className="mw-wrap">
        {fresh.estado === 'rancio' && (
          <div className="mw-callout mw-callout--ochre">
            <span className="mw-callout__icon">⧖</span>
            <div>
              <div className="mw-callout__title">Esta foto es de {mwEdad(fresh.edad_seg) ?? 'hace rato'}</div>
              <div className="mw-callout__sub">El escáner no corrió en un rato. Los precios y las condiciones de abajo <b>pueden haber cambiado</b> — trátalos como una referencia vieja, no como el ahora.</div>
              {onRescan && <button className="mw-callout__retry" onClick={onRescan}>↻ Escanear de nuevo</button>}
            </div>
          </div>
        )}

        <MarketBand macro={macro} lenguaje={lenguaje} />
        <Wallet w={wallet} lenguaje={lenguaje} />

        {agentSlot ? <section>{agentSlot}</section> : <FocusBlock items={focus} onOpen={openByPair} />}

        {dead ? (
          <section>
            <div className="mw-sec__head">
              <span className="mw-sec__label">Pares</span>
              <span className="mw-sec__rule" />
            </div>
            <div className="mw-empty">
              <div className="mw-empty__mark">◴</div>
              <div className="mw-empty__title">Todavía no hay foto del mercado</div>
              <div className="mw-empty__body">
                El escáner no completó un ciclo {errors ? <>y reportó <b>{errors} errores</b></> : 'todavía'}.
                No mostramos pares viejos como si fueran de ahora — preferimos no mostrar nada.
              </div>
              {onRescan && <button className="mw-callout__retry" style={{ marginTop: 22 }} onClick={onRescan}>↻ Reintentar escaneo</button>}
            </div>
          </section>
        ) : loading && symbols.length === 0 ? (
          <section>
            <div className="mw-sec__head"><span className="mw-sec__label">Pares</span><span className="mw-sec__rule" /></div>
            <div className="mw-empty"><div className="mw-empty__body">Cargando el mercado…</div></div>
          </section>
        ) : (
          <PairsBlock buckets={buckets} lenguaje={lenguaje} onOpen={onSymbolClick} />
        )}

        <SignalsBlock signals={signals} lenguaje={lenguaje} now={now} onOpen={onOpenSignal} />

        <div className="mw-note">
          El orden es por <b>cuántas condiciones coinciden</b> — es un filtro mecánico, no un consejo de compra.
          Cada número se puede abrir en "ver los números". La decisión es tuya.
        </div>
      </div>
    </div>
  );
};

export default MercadoView;
