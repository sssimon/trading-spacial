import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SymbolCard from './SymbolCard';
import type { SymbolStatus } from '../types';

function makeSymbol(overrides: Partial<SymbolStatus> = {}): SymbolStatus {
  return {
    symbol: 'BTCUSDT',
    estado: 'ok',
    price: 50_000,
    lrc_pct: 20,
    score: 6,
    señal: false,
    gatillo: true,
    ts: '2026-04-22T12:00:00Z',
    ...overrides,
  };
}

describe('SymbolCard', () => {
  it('renders the base/quote symbol split', () => {
    render(<SymbolCard symbol={makeSymbol()} />);
    expect(screen.getByText('BTC')).toBeInTheDocument();
    expect(screen.getByText('/USDT')).toBeInTheDocument();
  });

  it('renders numeric score and LRC%', () => {
    render(<SymbolCard symbol={makeSymbol({ score: 7, lrc_pct: 15 })} />);
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getByText('15.0%')).toBeInTheDocument();
  });

  it('shows LONG badge when señal is true and direction is LONG', () => {
    render(<SymbolCard symbol={makeSymbol({ señal: true, direction: 'LONG' })} />);
    expect(screen.getByText('LONG')).toBeInTheDocument();
  });

  it('shows SHORT badge when señal is true and direction is SHORT', () => {
    render(<SymbolCard symbol={makeSymbol({ señal: true, direction: 'SHORT' })} />);
    expect(screen.getByText('SHORT')).toBeInTheDocument();
  });

  it('shows SETUP badge when not a signal but gatillo is true', () => {
    render(<SymbolCard symbol={makeSymbol({ señal: false, gatillo: true })} />);
    expect(screen.getByText('SETUP')).toBeInTheDocument();
  });

  it('shows em-dash placeholders for null price/score/lrc', () => {
    render(<SymbolCard symbol={makeSymbol({ price: null, score: null, lrc_pct: null })} />);
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });

  it('invokes onClick when the card is clicked', () => {
    const onClick = vi.fn();
    render(<SymbolCard symbol={makeSymbol()} onClick={onClick} />);
    fireEvent.click(screen.getByTitle('Ver gráfico'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
