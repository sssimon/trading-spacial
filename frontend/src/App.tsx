// ============================================================
// App.tsx — Main application component
// ============================================================

import React, { useState, useEffect, useCallback } from 'react';
import { getSymbols, getStatus, getSignals, forceScan } from './api';
import type { SymbolStatus, StatusResponse, Signal } from './types';
import ChartModal from './components/ChartModal';
import Header from './components/Header';
import StatusBar from './components/StatusBar';
import SymbolsGrid from './components/SymbolsGrid';
import SignalsTable from './components/SignalsTable';
import ConfigPanel from './components/ConfigPanel';
import PositionsPanel from './components/PositionsPanel';

type FilterType = 'all' | 'signals';
type MainTab    = 'mercado' | 'posiciones';

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
  // Signal to open as position (passed from SignalsTable → PositionsPanel)
  const [signalForPos, setSignalForPos] = useState<Signal | null>(null);

  // Fetch all data in parallel
  const fetchAll = useCallback(async () => {
    try {
      const [symbolsRes, statusRes, signalsRes] = await Promise.all([
        getSymbols(),
        getStatus(),
        getSignals({ limit: 20, only_signals: false, since_hours: 24 }),
      ]);
      setSymbols(symbolsRes.symbols);
      setStatus(statusRes);
      setSignals(signalsRes.signals);
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

  const scannerRunning = status?.scanner_state?.running ?? false;

  return (
    <div className="app">
      <Header
        scannerRunning={scannerRunning}
        lastRefresh={lastRefresh}
        scanning={scanning}
        onRefresh={handleRefresh}
        onScan={handleScan}
        onConfigOpen={() => setConfigOpen(true)}
      />

      <ConfigPanel open={configOpen} onClose={() => setConfigOpen(false)} />

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
        </div>

        {/* ── Mercado tab ──────────────────────────────────── */}
        {mainTab === 'mercado' && (
          <>
            <SymbolsGrid
              symbols={symbols}
              loading={loading}
              filter={filter}
              onFilterChange={setFilter}
              onSymbolClick={setSelectedSymbol}
            />
            <SignalsTable
              signals={signals}
              loading={loading}
              onOpenPosition={handleOpenFromSignal}
            />
          </>
        )}

        {/* ── Posiciones tab ───────────────────────────────── */}
        {mainTab === 'posiciones' && (
          <PositionsPanel
            symbols={symbols}
            onOpenFromSignal={signalForPos}
            onSignalConsumed={() => setSignalForPos(null)}
          />
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
