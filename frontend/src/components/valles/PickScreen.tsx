// PickScreen.tsx
import React, { useState } from 'react';
import type { ValleySnapshot } from '../../types';
import { FreshnessTag } from '../FreshnessTag';
import { humanName } from './atoms';
import { formatPrice } from '../../utils';
import styles from './valles.module.css';

export const PickScreen: React.FC<{ snapshot: ValleySnapshot; onPick: (sym: string) => void }> = ({ snapshot, onPick }) => {
  const [q, setQ] = useState('');
  const [mostrarOcultas, setMostrarOcultas] = useState(false);
  const { candidates, coverage, frescura, candidatas_ocultas } = snapshot;
  const ocultas = candidatas_ocultas ?? [];

  return (
    <div className={styles.vwScreen}>
      {/* Titular POR ENCIMA del semáforo de frescura (§5.1) */}
      <h1 className={styles.vwPickQ}>
        {candidates.length > 0
          ? `Hoy hay ${candidates.length} ${candidates.length === 1 ? 'moneda' : 'monedas'} en la parte baja de su rango.`
          : coverage.complete
            ? 'Hoy ninguna en la parte baja de su rango.'
            : 'El screener todavía no corrió.'}
      </h1>
      <div className={styles.vwEyebrow}>{frescura && <FreshnessTag frescura={frescura} />}</div>

      {candidates.length > 0 && (
        <p className={styles.vwPickLead}>
          En el cuartil inferior de su rango de 30d — la réplica del filtro que usaba el
          canal de 2019, mecánico, no un consejo. Elige una para mirarla de cerca.
        </p>
      )}

      <div className={styles.vwCands}>
        {candidates.map((c) => (
          <button key={c.symbol} className={styles.vwCand} onClick={() => onPick(c.symbol)}>
            <div className={styles.vwCandId}>
              <div className={styles.vwCandName}>{humanName(c.symbol)}</div>
              <div className={styles.vwCandSym}>{c.symbol}</div>
            </div>
            <div className={styles.vwCandFact}>
              <span className={styles.vwCandTag} aria-hidden="true">● parte baja del rango</span>
              cuartil inferior (pos <b>{(c.pos_in_30d_range * 100).toFixed(0)}%</b>) · RSI <b>{c.rsi14.toFixed(0)}</b>
              {c.clima_ambiguo && (
                <span className={styles.vwClimaAmbiguo} title="El régimen del mercado es mixto — ni claramente hacia alts ni hacia BTC">
                  clima ambiguo
                </span>
              )}
            </div>
            <div className={styles.vwCandPrice}>${formatPrice(c.price)}</div>
            <div className={styles.vwCandGo} aria-hidden="true">→</div>
          </button>
        ))}
      </div>

      {/* Válvula "ver ocultas" — solo si el gate está activo y hay alts excluidas */}
      {ocultas.length > 0 && (
        <div className={styles.vwOcultas}>
          <button
            className={styles.vwVerOcultas}
            onClick={() => setMostrarOcultas((v) => !v)}
            aria-expanded={mostrarOcultas}
          >
            {mostrarOcultas
              ? 'Ocultar'
              : `${ocultas.length} ${ocultas.length === 1 ? 'alt' : 'alts'} fuera de alt-season — ver`}
          </button>
          {mostrarOcultas && (
            <div className={styles.vwOcultasList} role="list">
              {ocultas.map((c) => (
                <button
                  key={c.symbol}
                  role="listitem"
                  className={`${styles.vwCand} ${styles.vwCandAtenuada}`}
                  onClick={() => onPick(c.symbol)}
                >
                  <div className={styles.vwCandId}>
                    <div className={styles.vwCandName}>{humanName(c.symbol)}</div>
                    <div className={styles.vwCandSym}>{c.symbol}</div>
                  </div>
                  <div className={styles.vwCandFact}>
                    <span className={styles.vwCandTag} aria-hidden="true">● parte baja del rango</span>
                    cuartil inferior (pos <b>{(c.pos_in_30d_range * 100).toFixed(0)}%</b>) · RSI <b>{c.rsi14.toFixed(0)}</b>
                    <span className={styles.vwClimaHecho} title={c.clima}>{c.clima}</span>
                  </div>
                  <div className={styles.vwCandPrice}>${formatPrice(c.price)}</div>
                  <div className={styles.vwCandGo} aria-hidden="true">→</div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className={styles.vwEntryMeta}>
        <span>Se miraron <b>{coverage.evaluated}</b> de {coverage.universe} monedas del universo.</span>
        <span className={styles.vwEntrySep} />
        <span>Ordenadas por volumen — no por preferencia.</span>
        <span className={styles.vwEntrySep} />
        <span>La marca no ordena la lista ni la puntúa.</span>
      </div>

      <div className={styles.vwEntrySearch}>
        <div className={styles.vwEntrySearchLabel}>¿Buscas otra que no está en la lista?</div>
        <div className={styles.vwPickSearch}>
          <span aria-hidden="true">⌕</span>
          <input
            placeholder="escribe su símbolo (ej. SOLUSDT)"
            value={q}
            onChange={(e) => setQ(e.target.value.toUpperCase())}
            onKeyDown={(e) => { if (e.key === 'Enter' && q.trim()) onPick(q.endsWith('USDT') ? q : `${q}USDT`); }}
          />
        </div>
      </div>
    </div>
  );
};
