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

import React, { useCallback, useEffect, useState } from 'react';

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

  // Per-item snapshot + functional setState so concurrent rapid clicks
  // on different items don't clobber each other. PR #435 review issue
  // #1: a list-wide `before = items` snapshot would have the third
  // click's catch restore items A + B (already-successful deletes)
  // alongside item C (the failure). Snapshotting only the target item
  // avoids that.
  const handlePin = async (id: string) => {
    const target = items.find((c) => c.conversation_id === id);
    if (!target) return;
    const prevPinned = target.pinned;
    setItems((cur) => cur.map((c) =>
      c.conversation_id === id ? { ...c, pinned: !c.pinned } : c,
    ));
    try {
      const res = await togglePinConversation(id);
      setItems((cur) => cur.map((c) =>
        c.conversation_id === id ? { ...c, pinned: res.pinned } : c,
      ));
    } catch {
      setItems((cur) => cur.map((c) =>
        c.conversation_id === id ? { ...c, pinned: prevPinned } : c,
      ));
    }
  };

  const handleDelete = async (id: string) => {
    const idx = items.findIndex((c) => c.conversation_id === id);
    if (idx < 0) return;
    const removed = items[idx];
    setItems((cur) => cur.filter((c) => c.conversation_id !== id));
    try {
      await deleteConversation(id);
    } catch {
      setItems((cur) => {
        if (cur.some((c) => c.conversation_id === id)) return cur;
        const next = [...cur];
        next.splice(Math.min(idx, next.length), 0, removed);
        return next;
      });
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
              selectable={!!onSelectConversation}
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
  item:       ConversationSummary;
  onSelect:   () => void;
  onPin:      () => void;
  onDelete:   () => void;
  selectable: boolean;
}

const ConversationItem: React.FC<ConversationItemProps> = ({
  item, onSelect, onPin, onDelete, selectable,
}) => {
  // _relativeTime is intentionally NOT memoized (PR #435 review
  // issue #2): it reads Date.now() internally, so memoizing on
  // last_ts alone would freeze "hace 5 min" forever as wall-clock
  // advances. Function is a few subtractions + branches — cheaper
  // than the memo bookkeeping.
  const timeAgo = _relativeTime(item.last_ts);
  const klass = item.pinned
    ? `${styles.item} ${styles.itemPinned}`
    : styles.item;

  const bodyContent = (
    <>
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
    </>
  );

  return (
    <div role="listitem" className={klass}>
      {selectable ? (
        <button
          type="button"
          className={styles.itemBody}
          onClick={onSelect}
          aria-label={`Abrir conversación ${item.title ?? item.conversation_id}`}
          style={{ background: 'none', border: 'none', color: 'inherit',
                   textAlign: 'left', font: 'inherit', cursor: 'pointer' }}
        >{bodyContent}</button>
      ) : (
        // PR #435 review issue #6: until H.5 wires the rehydration,
        // the parent passes no onSelectConversation. Rendering the
        // body as a plain div avoids the "I clicked but nothing
        // happened" UX while still letting pin/delete work.
        <div className={styles.itemBody}>{bodyContent}</div>
      )}
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
