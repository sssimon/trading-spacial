// PickScreen.tsx
import React, { useState } from 'react';
import type { ValleySnapshot } from '../../types';
import { FreshnessTag } from '../FreshnessTag';
import { humanName } from './atoms';
import { formatPrice } from '../../utils';
import styles from './valles.module.css';

export const PickScreen: React.FC<{ snapshot: ValleySnapshot; onPick: (sym: string) => void }> = ({ snapshot, onPick }) => {
  const [q, setQ] = useState('');
  const { candidates, coverage, frescura } = snapshot;

  return (
    <div className={styles.vwScreen}>
      {/* Titular POR ENCIMA del semáforo de frescura (§5.1) */}
      <h1 className={styles.vwPickQ}>
        {candidates.length > 0
          ? `Hoy hay ${candidates.length} ${candidates.length === 1 ? 'moneda' : 'monedas'} en valle.`
          : coverage.complete
            ? 'Hoy ninguna moneda en valle.'
            : 'El screener todavía no corrió.'}
      </h1>
      <div className={styles.vwEyebrow}>{frescura && <FreshnessTag frescura={frescura} />}</div>

      {candidates.length > 0 && (
        <p className={styles.vwPickLead}>
          Son las que ahora mismo se mueven poco y siguen vivas: el filtro que hace Valles,
          mecánico, no un consejo. Elige una para mirarla de cerca con las tres lentes.
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
              <span className={styles.vwCandTag} aria-hidden="true">● en valle</span>
              se mueve un <b>{(c.pct_rango * 100).toFixed(1)}%</b> · <b>{c.semanas_consolidando} semanas</b> quieta
            </div>
            <div className={styles.vwCandPrice}>${formatPrice(c.price)}</div>
            <div className={styles.vwCandGo} aria-hidden="true">→</div>
          </button>
        ))}
      </div>

      <div className={styles.vwEntryMeta}>
        <span>Se miraron <b>{coverage.evaluated}</b> de {coverage.universe} monedas del universo.</span>
        <span className={styles.vwEntrySep} />
        <span>Ordenadas por volumen — no por preferencia.</span>
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
