// ============================================================
// ConnectionsPanel.tsx — per-user Telegram bot config slide-out.
// Anchored to UserMenu → 'Conexiones' item.
// Spec: docs/superpowers/specs/es/2026-05-21-telegram-per-user-config-pre-reg.md
// ============================================================

import React, { useEffect, useState } from 'react';
import styles from './ConnectionsPanel.module.css';
import type { NotifyChannels, TestDeliveryResponse } from '../types';
import { getPreferences, putPreferences, testPreferencesDelivery } from '../api';

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
        setDirty(false);
        setTestResult(null);
      })
      .finally(() => setLoading(false));
  }, [open]);

  const [saving,     setSaving]     = useState(false);
  const [dirty,      setDirty]      = useState(false);
  const [testResult, setTestResult] = useState<TestDeliveryResponse | null>(null);
  const [testing,    setTesting]    = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await putPreferences({
        notify_channels: {
          telegram_bot_token: botToken,
          telegram_chat_id:   chatId,
        },
      });
      setDirty(false);
      setTestResult(null);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testPreferencesDelivery();
      setTestResult(res);
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    setSaving(true);
    try {
      // Backend PUT /preferences treats notify_channels=null as "preserve
      // existing" (PATCH-like semantics, see db/user_preferences.py:67-69).
      // To CLEAR the credentials we must send an empty object — DB updates
      // the JSON column to "{}", which dispatch_per_user reads as no
      // overlay (notify_channels or {} → {}). Safe with global telegram_*
      // values absent from config.json.
      await putPreferences({ notify_channels: {} });
      setBotToken('');
      setChatId('');
      setTokenIsMasked(false);
      setTestResult(null);
      setDirty(false);
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
                autoComplete="new-password"
                value={botToken}
                onChange={(e) => { setBotToken(e.target.value); setTokenIsMasked(false); setDirty(true); }}
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
                autoComplete="off"
                value={chatId}
                onChange={(e) => { setChatId(e.target.value); setDirty(true); }}
                placeholder="123456789"
                disabled={loading}
              />
            </div>
            <div className={styles.actions}>
              <button
                className={`${styles.btn} ${styles.btnPrimary}`}
                onClick={handleSave}
                disabled={saving || loading || !dirty}
              >
                {saving ? 'Guardando...' : 'Guardar'}
              </button>
              <button
                className={styles.btn}
                onClick={handleTest}
                disabled={testing || saving || dirty}
              >
                {testing ? 'Enviando...' : 'Probar envío'}
              </button>
              <button
                className={styles.btn}
                onClick={handleDelete}
                disabled={saving}
              >
                Eliminar credenciales
              </button>
            </div>
            {testResult && (
              <div className={styles.testResult}>
                {testResult.ok && (
                  <span className={styles.testOk}>✓ Mensaje enviado a tu Telegram.</span>
                )}
                {!testResult.ok && testResult.reason === 'no_telegram_configured' && (
                  <span className={styles.testErr}>Configurá tu token y chat ID primero, después Guardar y volvé a probar.</span>
                )}
                {!testResult.ok && testResult.reason === null && testResult.receipts[0] && (
                  <span className={styles.testErr}>✗ {testResult.receipts[0].error}</span>
                )}
              </div>
            )}
          </section>
        </div>
      </aside>
    </>
  );
};

export default ConnectionsPanel;
