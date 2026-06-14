// ============================================================
// BottomNav — primary nav for mobile.
// ============================================================

import React from 'react';
import styles from './BottomNav.module.css';
import RailIcon, { type RailIconName } from './atoms/RailIcon';
import type { MainTab } from '../types-ui';
import type { NavCounts } from './LeftRail';

interface BottomNavProps {
  active:   MainTab | 'menu';
  counts:   NavCounts;
  onSelect: (tab: MainTab | 'menu') => void;
}

interface BNavItem {
  id:    MainTab | 'menu';
  label: string;
  icon:  RailIconName;
  count?: number;
}

const BottomNav: React.FC<BottomNavProps> = ({ active, counts, onSelect }) => {
  const items: BNavItem[] = [
    { id: 'mercado',     label: 'Mercado',    icon: 'mercado',    count: counts.market },
    { id: 'posiciones',  label: 'Posiciones', icon: 'positions',  count: counts.positions },
    { id: 'kill-switch', label: 'Kill-sw.',   icon: 'killswitch', count: counts.killswitch },
    { id: 'valles',      label: 'Valles',     icon: 'history' },
    { id: 'menu',        label: 'Más',        icon: 'config' },
  ];

  return (
    <nav className={styles.bnav} aria-label="Navegación inferior">
      {items.map((it) => (
        <button
          key={it.id}
          className={[styles.item, active === it.id ? styles.itemActive : ''].filter(Boolean).join(' ')}
          onClick={() => onSelect(it.id)}
          aria-current={active === it.id ? 'page' : undefined}
        >
          <RailIcon name={it.icon} size={18} className={styles.icon} />
          <span className={styles.label}>{it.label}</span>
          {it.count !== undefined && it.count > 0 && (
            <span className={styles.count}>{it.count}</span>
          )}
        </button>
      ))}
    </nav>
  );
};

export default BottomNav;
