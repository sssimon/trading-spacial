// ============================================================
// NotificationBell.tsx — bell icon + badge + dropdown (#162 PR C)
//
// Polls /notifications every 30s. Badge shows count of unread.
// Click opens a dropdown listing the N most recent unread events.
// Each row has a "mark read" button; header has "mark all read".
//
// Event realtime push is tracked in issue #62 (WebSocket/SSE). Until
// that lands, polling is the mechanism.
// ============================================================

import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  getNotifications,
  markNotificationRead,
  markAllNotificationsRead,
} from '../api';
import type { Notification } from '../types';

const POLL_INTERVAL_MS = 30_000;

function eventIcon(ev: Notification): string {
  if (ev.event_type === 'position_exit') return '📕';
  if (ev.event_type === 'health') {
    try {
      const payload = JSON.parse(ev.payload_json);
      if (payload.to_state === 'PAUSED') return '🛑';
      if (payload.to_state === 'REDUCED') return '⚠️';
      if (payload.to_state === 'ALERT') return '⚠️';
    } catch {
      /* fall through */
    }
    return 'ℹ️';
  }
  if (ev.event_type === 'infra') {
    if (ev.priority === 'critical') return '🚨';
    if (ev.priority === 'warning') return '⚠️';
    return 'ℹ️';
  }
  if (ev.event_type === 'signal') return '📈';
  return 'ℹ️';
}

function summary(ev: Notification): string {
  try {
    const p = JSON.parse(ev.payload_json);
    if (ev.event_type === 'signal') {
      return `${p.symbol ?? '?'} · score ${p.score ?? '?'} (${p.direction ?? ''})`;
    }
    if (ev.event_type === 'health') {
      return `${p.symbol ?? '?'} ${p.from_state ?? ''} → ${p.to_state ?? ''} (${p.reason ?? ''})`;
    }
    if (ev.event_type === 'position_exit') {
      const pnl = typeof p.pnl_usd === 'number' ? p.pnl_usd.toFixed(2) : '?';
      return `${p.symbol ?? '?'} ${p.exit_reason ?? ''} · P&L ${pnl}`;
    }
    if (ev.event_type === 'infra') {
      return `${p.component ?? '?'}: ${p.message ?? ''}`;
    }
    if (ev.event_type === 'system') {
      return `${p.kind ?? '?'}: ${p.message ?? ''}`;
    }
  } catch {
    /* fall through */
  }
  return ev.event_key;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
}

const NotificationBell: React.FC = () => {
  const [items, setItems] = useState<Notification[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await getNotifications({ unread: true, limit: 50 });
      setItems(resp.notifications ?? []);
    } catch (err) {
      // Silent: the bell should never crash the header.
      console.warn('NotificationBell refresh failed', err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial + periodic polling
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  const handleRead = async (id: number) => {
    try {
      await markNotificationRead(id);
      setItems((prev) => prev.filter((n) => n.id !== id));
    } catch (err) {
      console.warn('markNotificationRead failed', err);
    }
  };

  const handleReadAll = async () => {
    try {
      await markAllNotificationsRead();
      setItems([]);
    } catch (err) {
      console.warn('markAllNotificationsRead failed', err);
    }
  };

  const unreadCount = items.length;
  const hasUnread = unreadCount > 0;

  return (
    <div className="notification-bell" ref={dropdownRef}>
      <button
        className="btn btn-icon notification-bell-btn"
        onClick={() => setOpen((v) => !v)}
        title={hasUnread ? `${unreadCount} notificaciones sin leer` : 'Sin notificaciones nuevas'}
        aria-label="Notificaciones"
      >
        🔔
        {hasUnread && (
          <span className="notification-bell-badge" aria-hidden="true">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="notification-dropdown" role="menu">
          <div className="notification-dropdown-header">
            <span>Notificaciones {hasUnread ? `(${unreadCount})` : ''}</span>
            {hasUnread && (
              <button
                className="notification-dropdown-clear"
                onClick={handleReadAll}
                title="Marcar todas como leídas"
              >
                Marcar todas leídas
              </button>
            )}
          </div>

          {loading && items.length === 0 && (
            <div className="notification-dropdown-empty">Cargando…</div>
          )}

          {!loading && items.length === 0 && (
            <div className="notification-dropdown-empty">Sin notificaciones nuevas.</div>
          )}

          {items.length > 0 && (
            <ul className="notification-list">
              {items.map((ev) => (
                <li
                  key={ev.id}
                  className={`notification-item notification-item--${ev.priority}`}
                >
                  <span className="notification-icon">{eventIcon(ev)}</span>
                  <div className="notification-body">
                    <div className="notification-summary">{summary(ev)}</div>
                    <div className="notification-meta">
                      <span className="notification-type">{ev.event_type}</span>
                      <span className="notification-time">{formatTime(ev.sent_at)}</span>
                    </div>
                  </div>
                  <button
                    className="notification-read-btn"
                    onClick={() => handleRead(ev.id)}
                    title="Marcar como leída"
                    aria-label="Marcar como leída"
                  >
                    ✓
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};

export default NotificationBell;
