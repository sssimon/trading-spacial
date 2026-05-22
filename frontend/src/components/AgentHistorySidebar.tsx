// ============================================================
// AgentHistorySidebar — past conversation list anchored to AgentDock.
//
// Reads from H.3 endpoints (GET /agent/conversations, DELETE, POST /pin).
// Per-item click bubbles up via onSelectConversation — the H.5 wiring
// in AgentDock will hand that off to useAgentStream.loadConversation().
// For H.4 the click is a no-op stub; the sidebar just closes itself.
//
// Pre-reg: docs/superpowers/specs/es/2026-05-22-conversation-history-pre-reg.md
// ============================================================

import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
  deleteConversation,
  listConversations,
  togglePinConversation,
} from '../agent/client';
import type { ConversationSummary } from '../agent/types';
import styles from './AgentHistorySidebar.module.css';

interface AgentHistorySidebarProps {
  open:                   boolean;
  onClose:                () => void;
  /** H.5 will wire this to useAgentStream.loadConversation. For H.4
   *  the sidebar just closes itself after invoking. */
  onSelectConversation?:  (id: string) => void;
}

const PAGE_SIZE = 20;

const AgentHistorySidebar: React.FC<AgentHistorySidebarProps> = ({
  open, onClose, onSelectConversation,
}) => {
  const [items,   setItems]   = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [q,       setQ]       = useState('');

  const refresh = useCallback(async (query: string) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await listConversations({
        limit: PAGE_SIZE,
        ...(query.trim() ? { q: query.trim() } : {}),
      });
      setItems(resp.conversations);
    } catch (e) {
      setError('No se pudo cargar el historial.');
      if (typeof console !== 'undefined') console.warn('history list failed', e);
    } finally {
      setLoading(false);
    }
  }, []);

  // Refresh on open + when search query changes (debounced 200ms so a
  // typing user doesn't fire one request per keystroke).
  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => { void refresh(q); }, q ? 200 : 0);
    return () => window.clearTimeout(id);
  }, [open, q, refresh]);

  // Close on Escape — convention of the dock + ConnectionsPanel
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const handleSelect = (id: string) => {
    onSelectConversation?.(id);
    onClose();
  };

  const handlePin = async (id: string) => {
    // Optimistic toggle so the row reorders before the request
    // returns. Revert on error.
    const before = items;
    setItems((cur) => cur.map((c) =>
      c.conversation_id === id ? { ...c, pinned: !c.pinned } : c,
    ));
    try {
      const res = await togglePinConversation(id);
      setItems((cur) => cur.map((c) =>
        c.conversation_id === id ? { ...c, pinned: res.pinned } : c,
      ));
    } catch {
      setItems(before);
    }
  };

  const handleDelete = async (id: string) => {
    const before = items;
    setItems((cur) => cur.filter((c) => c.conversation_id !== id));
    try {
      await deleteConversation(id);
    } catch {
      setItems(before);
    }
  };

  if (!open) return null;

  return (
    <>
      <div className={styles.backdrop} onClick={onClose} aria-hidden="true" />
      <aside
        className={styles.panel}
        role="dialog"
        aria-labelledby="agent-history-title"
      >
        <header className={styles.header}>
          <h2 id="agent-history-title" className={styles.title}>Historial</h2>
          <button
            type="button"
            className={styles.closeBtn}
            onClick={onClose}
            aria-label="Cerrar historial"
          >×</button>
        </header>

        <div className={styles.searchBar}>
          <input
            type="text"
            className={styles.searchInput}
            placeholder="Buscar conversaciones…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Buscar conversaciones"
          />
        </div>

        <div className={styles.list} role="list">
          {loading && items.length === 0 && (
            <div className={styles.loading}>Cargando…</div>
          )}
          {!loading && error && (
            <div className={styles.errorState}>{error}</div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className={styles.empty}>
              {q ? 'No hay conversaciones que coincidan.' : 'No tienes conversaciones todavía.'}
            </div>
          )}
          {items.map((c) => (
            <ConversationItem
              key={c.conversation_id}
              item={c}
              onSelect={() => handleSelect(c.conversation_id)}
              onPin={() => handlePin(c.conversation_id)}
              onDelete={() => handleDelete(c.conversation_id)}
            />
          ))}
        </div>
      </aside>
    </>
  );
};

interface ConversationItemProps {
  item:    ConversationSummary;
  onSelect: () => void;
  onPin:    () => void;
  onDelete: () => void;
}

const ConversationItem: React.FC<ConversationItemProps> = ({
  item, onSelect, onPin, onDelete,
}) => {
  const timeAgo = useMemo(() => _relativeTime(item.last_ts), [item.last_ts]);
  const klass = item.pinned
    ? `${styles.item} ${styles.itemPinned}`
    : styles.item;

  return (
    <div role="listitem" className={klass}>
      <button
        type="button"
        className={styles.itemBody}
        onClick={onSelect}
        aria-label={`Abrir conversación ${item.title ?? item.conversation_id}`}
        style={{ background: 'none', border: 'none', color: 'inherit',
                 textAlign: 'left', font: 'inherit', cursor: 'pointer' }}
      >
        <div
          className={item.title ? styles.itemTitle : `${styles.itemTitle} ${styles.itemTitleEmpty}`}
          title={item.title ?? item.conversation_id}
        >
          {item.title ?? 'Sin título'}
        </div>
        <div className={styles.itemMeta}>
          <span className={styles.surfaceChip}>{item.surface}</span>
          <span>{timeAgo}</span>
          <span>· {item.message_count} msj</span>
        </div>
      </button>
      <div className={styles.itemActions}>
        <button
          type="button"
          className={item.pinned
            ? `${styles.actionBtn} ${styles.actionBtnPinned}`
            : styles.actionBtn}
          onClick={(e) => { e.stopPropagation(); void onPin(); }}
          aria-label={item.pinned ? 'Desfijar' : 'Fijar'}
          title={item.pinned ? 'Desfijar' : 'Fijar'}
        >{item.pinned ? '📌 fijada' : '📌'}</button>
        <button
          type="button"
          className={styles.actionBtn}
          onClick={(e) => { e.stopPropagation(); void onDelete(); }}
          aria-label="Borrar"
          title="Borrar"
        >🗑</button>
      </div>
    </div>
  );
};

/** Spanish relative-time formatter with a minimal closed set of buckets.
 *  Keeps the bundle free of Intl.RelativeTimeFormat negotiation churn
 *  (the polyfilling story in this app's Vite config is "don't"). */
function _relativeTime(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return iso;
    const diffSec = Math.round((Date.now() - then) / 1000);
    if (diffSec < 60)        return 'recién';
    if (diffSec < 60 * 60)   return `hace ${Math.floor(diffSec / 60)} min`;
    if (diffSec < 60 * 60 * 24) return `hace ${Math.floor(diffSec / (60 * 60))} h`;
    const days = Math.floor(diffSec / (60 * 60 * 24));
    if (days < 30)  return `hace ${days} d`;
    if (days < 365) return `hace ${Math.floor(days / 30)} m`;
    return `hace ${Math.floor(days / 365)} a`;
  } catch {
    return iso;
  }
}

export default AgentHistorySidebar;
