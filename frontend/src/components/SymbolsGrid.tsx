// ============================================================
// SymbolsGrid.tsx — Responsive grid of symbol cards with filters
// ============================================================

import React from 'react';
import type { SymbolStatus } from '../types';
import SymbolCard from './SymbolCard';

type FilterType = 'all' | 'signals';

interface SymbolsGridProps {
  symbols: SymbolStatus[];
  loading: boolean;
  filter: FilterType;
  onFilterChange: (filter: FilterType) => void;
  onSymbolClick?: (symbol: SymbolStatus) => void;
}

const SkeletonCard: React.FC = () => (
  <div className="skeleton-card">
    <div className="skeleton-line skeleton-line--title" />
    <div className="skeleton-line skeleton-line--price" />
    <div className="skeleton-line skeleton-line--bar" />
    <div className="skeleton-line skeleton-line--bar" />
    <div className="skeleton-line skeleton-line--footer" />
  </div>
);

const SymbolsGrid: React.FC<SymbolsGridProps> = ({
  symbols,
  loading,
  filter,
  onFilterChange,
  onSymbolClick,
}) => {
  const signalCount = symbols.filter((s) => s.señal).length;

  const displayed =
    filter === 'signals' ? symbols.filter((s) => s.señal) : symbols;

  return (
    <section className="symbols-section">
      {/* Filter tabs */}
      <div className="section-header">
        <h2 className="section-title">Mercado</h2>
        <div className="filter-tabs">
          <button
            className={`filter-tab${filter === 'all' ? ' filter-tab--active' : ''}`}
            onClick={() => onFilterChange('all')}
          >
            Todos
            <span className="tab-count">{symbols.length}</span>
          </button>
          <button
            className={`filter-tab${filter === 'signals' ? ' filter-tab--active' : ''}`}
            onClick={() => onFilterChange('signals')}
          >
            Señales
            <span className={`tab-count${signalCount > 0 ? ' tab-count--active' : ''}`}>
              {signalCount}
            </span>
          </button>
        </div>
      </div>

      {/* Grid */}
      {loading ? (
        <div className="symbols-grid">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : displayed.length === 0 ? (
        <div className="empty-state">
          {filter === 'signals' ? (
            <>
              <div className="empty-icon">🔍</div>
              <div className="empty-title">Sin señales activas</div>
              <div className="empty-subtitle">
                El scanner está monitoreando {symbols.length} pares en busca de oportunidades
              </div>
            </>
          ) : (
            <>
              <div className="empty-icon">📡</div>
              <div className="empty-title">Sin datos</div>
              <div className="empty-subtitle">
                Esperando datos del scanner…
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="symbols-grid">
          {displayed.map((sym) => (
            <SymbolCard
              key={sym.symbol}
              symbol={sym}
              onClick={onSymbolClick ? () => onSymbolClick(sym) : undefined}
            />
          ))}
        </div>
      )}
    </section>
  );
};

export default SymbolsGrid;
