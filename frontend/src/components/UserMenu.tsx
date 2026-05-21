// ============================================================
// UserMenu — user dropdown anchored to the header user block.
// ============================================================

import React from 'react';
import styles from './UserMenu.module.css';
import type { AuthUser } from '../auth/api';

interface UserMenuProps {
  open:    boolean;
  user:    AuthUser;
  onClose: () => void;
  onLogout: () => void;
  onConnectionsOpen?: () => void;
  telegramConfigured?: boolean;
}

interface MenuItem {
  icon:  string;
  label: string;
  hint?: string;
  kbd?:  string;
  badge?: string;
  onClick?: () => void;
}

const UserMenu: React.FC<UserMenuProps> = ({
  open, user, onClose, onLogout,
  onConnectionsOpen, telegramConfigured = false,
}) => {
  if (!open) return null;

  const items: MenuItem[] = [
    { icon: '◧', label: 'Mi cuenta', hint: 'email · contraseña · 2FA' },
    { icon: '✦', label: 'Capital y riesgo', hint: 'gestión de balance' },
    {
      icon: '◐',
      label: 'Conexiones',
      hint: 'Telegram · Webhook',
      badge: telegramConfigured ? undefined : '1',
      // Only call onConnectionsOpen — the parent's setOpenOverlay('connections')
      // is the same setter as onClose's setOpenOverlay(null), so calling both
      // would race (last call wins → null → panel never opens). The menu closes
      // naturally because UserMenu's `open={openOverlay === 'user'}` flips false
      // once the overlay is 'connections'.
      onClick: () => { onConnectionsOpen?.(); },
    },
    { icon: '⌨', label: 'Atajos de teclado', kbd: '?' },
    { icon: '❑', label: 'Documentación' },
  ];

  return (
    <>
      <div className={styles.backdrop} onClick={onClose} aria-hidden="true" />
      <div className={styles.menu} role="menu">
        <header className={styles.header}>
          <div className={styles.avatar}>{(user.email[0] || '?').toUpperCase()}</div>
          <div className={styles.text}>
            <div className={styles.email}>{user.email}</div>
            <div className={styles.role}>{user.role}</div>
          </div>
        </header>
        <div className={styles.list}>
          {items.map((it, i) => (
            <button key={i} className={styles.item} onClick={it.onClick}>
              <span className={styles.icon}>{it.icon}</span>
              <span className={styles.itemText}>
                <span className={styles.label}>{it.label}</span>
                {it.hint && <span className={`${styles.hint} prose`}>{it.hint}</span>}
              </span>
              {it.kbd   && <span className={styles.kbd}>{it.kbd}</span>}
              {it.badge && <span className={styles.itemBadge}>{it.badge}</span>}
              <span className={styles.chev}>›</span>
            </button>
          ))}
        </div>
        <button className={styles.logout} onClick={onLogout}>
          <span className={styles.logoutIcon}>↗</span>
          <span>Cerrar sesión</span>
        </button>
      </div>
    </>
  );
};

export default UserMenu;
