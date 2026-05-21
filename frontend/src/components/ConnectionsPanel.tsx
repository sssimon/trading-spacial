// ============================================================
// ConnectionsPanel.tsx — per-user Telegram bot config slide-out.
// Anchored to UserMenu → 'Conexiones' item.
// Spec: docs/superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md
// ============================================================

import React, { useEffect, useState } from 'react';
import styles from './ConnectionsPanel.module.css';
import type { NotifyChannels } from '../types';
import { getPreferences, putPreferences } from '../api';

interface ConnectionsPanelProps {
  open:    boolean;
  onClose: () => void;
}

const ConnectionsPanel: React.FC<ConnectionsPanelProps> = ({ open, onClose }) => {
  const [botToken, setBotToken]   = useState('');
  const [chatId,   setChatId]     = useState('');
  const [tokenIsMasked, setTokenIsMasked] = useState(false);
  const [loading,  setLoading]    = useState(true);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    getPreferences()
      .then((prefs) => {
        const nc = (prefs.notify_channels ?? {}) as NotifyChannels;
        setBotToken(nc.telegram_bot_token ?? '');
        setChatId(nc.telegram_chat_id ?? '');
        setTokenIsMasked((nc.telegram_bot_token ?? '').includes('****'));
      })
      .finally(() => setLoading(false));
  }, [open]);

  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await putPreferences({
        notify_channels: {
          telegram_bot_token: botToken,
          telegram_chat_id:   chatId,
        },
      });
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <>
      <div className={styles.backdrop} onClick={onClose} aria-hidden="true" />
      <aside className={styles.panel} role="dialog" aria-labelledby="connections-title">
        <header className={styles.header}>
          <h2 id="connections-title" className={styles.title}>Conexiones</h2>
          <button className={styles.closeBtn} onClick={onClose} aria-label="Cerrar">×</button>
        </header>
        <div className={styles.body}>
          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>Telegram</h3>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="bot-token">Bot Token</label>
              <input
                id="bot-token"
                className={styles.input}
                type="password"
                value={botToken}
                onChange={(e) => { setBotToken(e.target.value); setTokenIsMasked(false); }}
                placeholder="123456789:ABCdef..."
                disabled={loading}
              />
              {tokenIsMasked && (
                <div className={styles.hint}>
                  Token guardado · pegá uno nuevo para reemplazar
                </div>
              )}
            </div>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="chat-id">Chat ID</label>
              <input
                id="chat-id"
                className={styles.input}
                type="text"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
                placeholder="123456789"
                disabled={loading}
              />
            </div>
            <div className={styles.actions}>
              <button
                className={`${styles.btn} ${styles.btnPrimary}`}
                onClick={handleSave}
                disabled={saving || loading}
              >
                {saving ? 'Guardando...' : 'Guardar'}
              </button>
            </div>
          </section>
        </div>
      </aside>
    </>
  );
};

export default ConnectionsPanel;
