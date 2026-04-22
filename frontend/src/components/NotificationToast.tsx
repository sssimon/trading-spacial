// ============================================================
// NotificationToast.tsx — transient top-right toast stack (#162)
//
// On first dashboard load, shows a toast for each unread CRITICAL or
// WARNING notification. Dismiss individually or let them auto-fade after
// AUTO_DISMISS_MS. Info-priority events are NOT toasted — the bell badge
// is sufficient for those.
//
// Designed so the Bell component remains the persistent store; toasts
// are only a loud-on-arrival overlay. A toast is rendered once per
// component mount; re-rendering (polling refresh) does not resurface it.
// ============================================================

import React, { useEffect, useState, useRef } from 'react';
import { getNotifications, markNotificationRead } from '../api';
import type { Notification } from '../types';

const AUTO_DISMISS_MS = 8000;
const MAX_TOASTS = 3;

function summary(ev: Notification): string {
  try {
    const p = JSON.parse(ev.payload_json);
    if (ev.event_type === 'health') {
      return `${p.symbol ?? '?'}: ${p.from_state ?? ''} → ${p.to_state ?? ''}`;
    }
    if (ev.event_type === 'infra') {
      return `${p.component ?? '?'}: ${p.message ?? ev.event_key}`;
    }
    if (ev.event_type === 'position_exit') {
      const pnl = typeof p.pnl_usd === 'number' ? p.pnl_usd.toFixed(2) : '?';
      return `${p.symbol ?? '?'} ${p.exit_reason ?? ''} · P&L ${pnl}`;
    }
  } catch {
    /* fall through */
  }
  return ev.event_key;
}

function priorityIcon(priority: string): string {
  if (priority === 'critical') return '🚨';
  if (priority === 'warning') return '⚠️';
  return 'ℹ️';
}

const NotificationToast: React.FC = () => {
  const [toasts, setToasts] = useState<Notification[]>([]);
  const dismissed = useRef<Set<number>>(new Set());

  // Load once on mount.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const resp = await getNotifications({ unread: true, limit: 50 });
        if (!alive) return;
        const important = (resp.notifications ?? [])
          .filter((n) => n.priority === 'critical' || n.priority === 'warning')
          .slice(0, MAX_TOASTS);
        setToasts(important);
      } catch (err) {
        console.warn('NotificationToast initial load failed', err);
      }
    })();
    return () => { alive = false; };
  }, []);

  // Auto-dismiss each toast AUTO_DISMISS_MS after it enters.
  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts.map((t) =>
      setTimeout(() => dismiss(t.id), AUTO_DISMISS_MS),
    );
    return () => { timers.forEach(clearTimeout); };
  }, [toasts]);

  const dismiss = (id: number) => {
    if (dismissed.current.has(id)) return;
    dismissed.current.add(id);
    setToasts((prev) => prev.filter((n) => n.id !== id));
  };

  const dismissAndRead = async (id: number) => {
    dismiss(id);
    try {
      await markNotificationRead(id);
    } catch (err) {
      console.warn('NotificationToast mark-read failed', err);
    }
  };

  if (toasts.length === 0) return null;

  return (
    <div className="toast-stack" role="alert" aria-live="polite">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`toast toast--${t.priority}`}
          data-testid="notification-toast"
        >
          <span className="toast-icon">{priorityIcon(t.priority)}</span>
          <div className="toast-body">
            <div className="toast-summary">{summary(t)}</div>
            <div className="toast-type">{t.event_type}</div>
          </div>
          <button
            className="toast-close"
            onClick={() => dismissAndRead(t.id)}
            title="Cerrar y marcar leída"
            aria-label="Cerrar"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
};

export default NotificationToast;
