// atoms.tsx
import React, { useState } from 'react';
import type { Frescura } from '../../types';
import { FreshnessTag } from '../FreshnessTag';
import styles from './valles.module.css';

const NAMES: Record<string, string> = {
  ADAUSDT: 'Cardano', XLMUSDT: 'Stellar', RUNEUSDT: 'THORChain', PENDLEUSDT: 'Pendle',
  JUPUSDT: 'Jupiter', UNIUSDT: 'Uniswap', INJUSDT: 'Injective', GMXUSDT: 'GMX',
  BTCUSDT: 'Bitcoin', PYTHUSDT: 'Pyth', ZBCUSDT: 'Zebec',
};
export const humanName = (s: string): string => NAMES[s] ?? s.replace('USDT', '');

export const Eyebrow: React.FC<{ symbol: string; frescura?: Frescura }> = ({ symbol, frescura }) => (
  <div className={styles.vwEyebrow}>
    <span className={styles.vwEyebrowCoin}>{humanName(symbol)}</span>
    <span className={styles.vwEyebrowSym}>{symbol}</span>
    {frescura && <FreshnessTag frescura={frescura} />}
  </div>
);

export const Retry: React.FC<{ onClick: () => void }> = ({ onClick }) => (
  <button className={styles.vwCalloutRetry} onClick={onClick}>↻ Intentar de nuevo</button>
);

export const Loading: React.FC<{ label: string }> = ({ label }) => (
  <div className={`${styles.vwCallout} ${styles.vwCalloutMute}`} aria-busy="true">
    <span className={styles.vwCalloutIcon} aria-hidden="true">⧖</span>
    <div><div className={styles.vwCalloutTitle}>{label}</div></div>
  </div>
);

export const Callout: React.FC<{
  tone: 'mute' | 'ochre'; icon: string; title: string; sub?: React.ReactNode; children?: React.ReactNode;
}> = ({ tone, icon, title, sub, children }) => (
  <div className={`${styles.vwCallout} ${tone === 'ochre' ? styles.vwCalloutOchre : styles.vwCalloutMute}`}>
    <span className={styles.vwCalloutIcon} aria-hidden="true">{icon}</span>
    <div>
      <div className={styles.vwCalloutTitle}>{title}</div>
      {sub && <div className={styles.vwCalloutSub}>{sub}</div>}
      {children}
    </div>
  </div>
);

export const VerNumeros: React.FC<{ items: { k: string; v: string; note?: string }[] }> = ({ items }) => {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <div className={styles.vwMore}>
      <button className={styles.vwMoreToggle} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className={`${styles.vwMoreCaret} ${open ? styles.vwMoreCaretOpen : ''}`} aria-hidden="true">▸</span>
        {open ? 'Ocultar los números' : 'Ver los números'}
      </button>
      {open && (
        <div className={styles.vwMorePanel}>
          {items.map((it, i) => (
            <div className={styles.vwNum} key={i}>
              <div className={styles.vwNumK}>{it.k}</div>
              <div className={styles.vwNumV}>{it.v}</div>
              {it.note && <div className={styles.vwNumNote}>{it.note}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
