// ============================================================
// App.tsx — Main application component
// ============================================================

import React, { useState, useEffect, useCallback } from 'react';
import { getSymbols, getStatus, getSignals, forceScan, getTuneLatest, applyTune, rejectTune } from './api';
import type { SymbolStatus, StatusResponse, Signal, TuneResult } from './types';
import ChartModal from './components/ChartModal';
import ErrorBoundary from './components/ErrorBoundary';
import Header from './components/Header';
import StatusBar from './components/StatusBar';
import SymbolsGrid from './components/SymbolsGrid';
import SignalsTable from './components/SignalsTable';
import ConfigPanel from './components/ConfigPanel';
import PositionsPanel from './components/PositionsPanel';
import TuneReportModal from './components/TuneReportModal';
import NotificationToast from './components/NotificationToast';
import KillSwitchDashboard from './components/KillSwitchDashboard';

type FilterType = 'all' | 'signals';
type MainTab    = 'mercado' | 'posiciones' | 'kill-switch';

const REFRESH_INTERVAL_MS = 30_000;

const App: React.FC = () => {
  const [symbols,     setSymbols]     = useState<SymbolStatus[]>([]);
  const [status,      setStatus]      = useState<StatusResponse | null>(null);
  const [signals,     setSignals]     = useState<Signal[]>([]);
  const [scanning,    setScanning]    = useState(false);
  const [loading,     setLoading]     = useState(true);
  const [filter,      setFilter]      = useState<FilterType>('all');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [error,       setError]       = useState<string | null>(null);
  const [configOpen,  setConfigOpen]  = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState<SymbolStatus | null>(null);
  const [mainTab,     setMainTab]     = useState<MainTab>('mercado');
  const [tuneResult,  setTuneResult]  = useState<TuneResult | null>(null);
  const [tuneModalOpen, setTuneModalOpen] = useState(false);
  // Signal to open as position (passed from SignalsTable → PositionsPanel)
  const [signalForPos, setSignalForPos] = useState<Signal | null>(null);

  // Fetch all data in parallel
  const fetchAll = useCallback(async () => {
    try {
      const [symbolsRes, statusRes, signalsRes, tuneRes] = await Promise.all([
        getSymbols(),
        getStatus(),
        getSignals({ limit: 20, only_signals: false, since_hours: 24 }),
        getTuneLatest().catch(() => null),
      ]);
      setSymbols(symbolsRes.symbols);
      setStatus(statusRes);
      setSignals(signalsRes.signals);
      setTuneResult(tuneRes);
      setLastRefresh(new Date());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Error desconocido');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  useEffect(() => {
    const id = setInterval(fetchAll, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  const handleRefresh = useCallback(async () => {
    setLoading(true);
    await fetchAll();
  }, [fetchAll]);

  const handleScan = useCallback(async () => {
    if (scanning) return;
    setScanning(true);
    try {
      await forceScan();
      await fetchAll();
    } catch (err) {
      console.error('forceScan error:', err);
    } finally {
      setScanning(false);
    }
  }, [scanning, fetchAll]);

  // Open position from a signal (switches to posiciones tab)
  const handleOpenFromSignal = useCallback((signal: Signal) => {
    setSignalForPos(signal);
    setMainTab('posiciones');
  }, []);

  const handleTuneApply = useCallback(async () => {
    await applyTune();
    await fetchAll();
  }, [fetchAll]);

  const handleTuneReject = useCallback(async () => {
    await rejectTune();
    await fetchAll();
  }, [fetchAll]);

  const hasPendingTune = tuneResult?.status === 'pending';

  const scannerRunning = status?.scanner_state?.running ?? false;

  return (
    <div className="app">
      <NotificationToast />
      <Header
        scannerRunning={scannerRunning}
        lastRefresh={lastRefresh}
        scanning={scanning}
        onRefresh={handleRefresh}
        onScan={handleScan}
        onConfigOpen={() => setConfigOpen(true)}
        hasPendingTune={hasPendingTune}
        onTuneOpen={() => setTuneModalOpen(true)}
      />

      <ConfigPanel open={configOpen} onClose={() => setConfigOpen(false)} />

      {tuneModalOpen && tuneResult && (
        <TuneReportModal
          tune={tuneResult}
          onApply={handleTuneApply}
          onReject={handleTuneReject}
          onClose={() => setTuneModalOpen(false)}
        />
      )}

      <ChartModal
        symbol={selectedSymbol}
        onClose={() => setSelectedSymbol(null)}
      />

      {error && (
        <div className="error-banner">
          <span className="error-icon">⚠</span>
          <span className="error-text">Error de conexión: {error}</span>
          <button className="error-dismiss" onClick={() => setError(null)}>✕</button>
        </div>
      )}

      <main className="app-main">
        <StatusBar status={status} />

        {/* ── Main tab bar ────────────────────────────────── */}
        <div className="main-tab-bar">
          <button
            className={`main-tab${mainTab === 'mercado' ? ' main-tab--active' : ''}`}
            onClick={() => setMainTab('mercado')}
          >
            Mercado
          </button>
          <button
            className={`main-tab${mainTab === 'posiciones' ? ' main-tab--active' : ''}`}
            onClick={() => setMainTab('posiciones')}
          >
            Posiciones
          </button>
          <button
            className={`main-tab${mainTab === 'kill-switch' ? ' main-tab--active' : ''}`}
            onClick={() => setMainTab('kill-switch')}
          >
            Kill Switch
          </button>
        </div>

        {/* ── Mercado tab ──────────────────────────────────── */}
        {mainTab === 'mercado' && (
          <>
            <ErrorBoundary fallbackLabel="Error en el grid de simbolos">
              <SymbolsGrid
                symbols={symbols}
                loading={loading}
                filter={filter}
                onFilterChange={setFilter}
                onSymbolClick={setSelectedSymbol}
              />
            </ErrorBoundary>
            <ErrorBoundary fallbackLabel="Error en la tabla de senales">
              <SignalsTable
                signals={signals}
                loading={loading}
                onOpenPosition={handleOpenFromSignal}
              />
            </ErrorBoundary>
          </>
        )}

        {/* ── Posiciones tab ───────────────────────────────── */}
        {mainTab === 'posiciones' && (
          <ErrorBoundary fallbackLabel="Error en el panel de posiciones">
            <PositionsPanel
              symbols={symbols}
              onOpenFromSignal={signalForPos}
              onSignalConsumed={() => setSignalForPos(null)}
            />
          </ErrorBoundary>
        )}

        {/* ── Kill Switch tab ─────────────────────────────────── */}
        {mainTab === 'kill-switch' && (
          <ErrorBoundary fallbackLabel="Error en dashboard de kill switch">
            <KillSwitchDashboard />
          </ErrorBoundary>
        )}
      </main>

      <footer className="app-footer">
        <span>Crypto Scanner V6</span>
        <span className="footer-dot">·</span>
        <span>Top 20 por volumen</span>
        <span className="footer-dot">·</span>
        <span>Actualización automática cada 30s</span>
      </footer>
    </div>
  );
};

export default App;
