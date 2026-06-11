// ============================================================
// LeftRail — vertical primary nav for desktop.
// Sections: trading (mercado / posiciones / kill-switch) +
// análisis (historial / auto-tune / config). Footer = user.
// ============================================================

import React from 'react';
import styles from './LeftRail.module.css';
import RailIcon, { type RailIconName } from './atoms/RailIcon';
import type { MainTab } from '../types-ui';
import { useAuth } from '../auth/useAuth';

export interface NavCounts {
  market:     number;
  positions:  number;
  killswitch: number;
}

interface LeftRailProps {
  active:     MainTab;
  counts:     NavCounts;
  onSelect:   (tab: MainTab) => void;
  onLogout?:  () => void;
  onTuneOpen?: () => void;
  hasPendingTune?: boolean;
}

interface RailItemDef {
  id:        string;
  label:     string;
  icon:      RailIconName;
  count?:    number;
  dimWhenZero?: boolean;
  badge?:    string;
  tab?:      MainTab;  // if undefined, this is a non-tab item
  onClick?:  () => void;
}

const LeftRail: React.FC<LeftRailProps> = ({
  active, counts, onSelect, onLogout, hasPendingTune,
}) => {
  const { user } = useAuth();

  const tradingItems: RailItemDef[] = [
    { id: 'mercado',     label: 'Mercado',     icon: 'mercado',    count: counts.market,     tab: 'mercado' },
    { id: 'posiciones',  label: 'Posiciones',  icon: 'positions',  count: counts.positions,  tab: 'posiciones' },
    { id: 'kill-switch', label: 'Kill-switch', icon: 'killswitch', count: counts.killswitch, dimWhenZero: true, tab: 'kill-switch' },
  ];

  const analysisItems: RailItemDef[] = [
    { id: 'tune', label: 'Auto-tune', icon: 'tune', badge: hasPendingTune ? 'PEND' : undefined, tab: 'autotune' },
    { id: 'history', label: 'Historial', icon: 'history', tab: 'historial' },
    { id: 'valles', label: 'Valles', icon: 'history', tab: 'valles' },
  ];

  return (
    <nav className={styles.rail} aria-label="Navegación principal">
      <div className={styles.brand}>
        <div className={styles.brandMark}>
          <svg width="22" height="22" viewBox="0 0 20 20" fill="none">
            <rect x="1" y="1" width="18" height="18" stroke="currentColor" strokeWidth="1" />
            <rect x="5" y="5" width="10" height="10" fill="currentColor" />
            <rect x="8" y="8" width="4"  height="4"  fill="var(--nbc-bg)" />
          </svg>
        </div>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionLabel}>trading</div>
        {tradingItems.map((it) => (
          <RailItem
            key={it.id}
            def={it}
            active={it.tab === active}
            onClick={() => {
              if (it.tab) onSelect(it.tab);
              else if (it.onClick) it.onClick();
            }}
          />
        ))}
      </div>

      <div className={styles.section}>
        <div className={styles.sectionLabel}>análisis</div>
        {analysisItems.map((it) => (
          <RailItem
            key={it.id}
            def={it}
            active={it.tab === active}
            onClick={() => {
              if (it.tab) onSelect(it.tab);
              else if (it.onClick) it.onClick();
            }}
          />
        ))}
      </div>

      <div className={styles.spacer} />

      {user && (
        <div className={styles.user}>
          <div className={styles.userRow}>
            <div className={styles.userAvatar}>{(user.email[0] || '?').toUpperCase()}</div>
            <div className={styles.userText}>
              <div className={styles.userEmail}>{user.email.split('@')[0]}</div>
              <div className={styles.userRole}>{user.role}</div>
            </div>
          </div>
          {onLogout && (
            <button className={styles.logout} onClick={onLogout} title="Cerrar sesión">
              <span>↗</span> salir
            </button>
          )}
        </div>
      )}
    </nav>
  );
};

interface RailItemProps {
  def:    RailItemDef;
  active: boolean;
  onClick?: () => void;
}

const RailItem: React.FC<RailItemProps> = ({ def, active, onClick }) => {
  const dim = def.dimWhenZero && (def.count === 0 || def.count == null);
  return (
    <button
      className={[
        styles.item,
        active ? styles.itemActive : '',
        dim ? styles.itemDim : '',
      ].filter(Boolean).join(' ')}
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
    >
      <span className={styles.indicator} />
      <RailIcon name={def.icon} className={styles.icon} />
      <span className={styles.label}>{def.label}</span>
      {def.count !== undefined && (
        <span className={[
          styles.count,
          def.count > 0 ? styles.countOn : '',
        ].filter(Boolean).join(' ')}>
          {def.count}
        </span>
      )}
      {def.badge && <span className={styles.badge}>{def.badge}</span>}
    </button>
  );
};

export default LeftRail;
