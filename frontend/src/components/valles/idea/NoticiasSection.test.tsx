// NoticiasSection.test.tsx — TDD para la sección de noticias (track 2)
// V1: solo empty-state honesto. Sin noticias fabricadas.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NoticiasSection } from './NoticiasSection';

describe('NoticiasSection — empty-state honesto (track 2)', () => {
  it('renderiza sin explotar', () => {
    expect(() => render(<NoticiasSection symbol="BTCUSDT" />)).not.toThrow();
  });

  it('muestra el heading de la sección', () => {
    render(<NoticiasSection symbol="ADAUSDT" />);
    expect(screen.getByRole('heading', { name: /Lo último que se dijo/i })).toBeTruthy();
  });

  it('muestra el texto de empty-state honesto', () => {
    render(<NoticiasSection symbol="ADAUSDT" />);
    expect(
      screen.getByText(/Todavía no traemos las noticias de esta moneda/i),
    ).toBeTruthy();
  });

  it('NO renderiza noticias falsas ni links inventados', () => {
    render(<NoticiasSection symbol="ADAUSDT" />);
    // No debe haber elementos <a> (que implicarían noticias con URLs)
    const links = document.querySelectorAll('a');
    expect(links).toHaveLength(0);
    // No debe haber lista de artículos
    const lists = document.querySelectorAll('ul, ol');
    expect(lists).toHaveLength(0);
  });

  it('tiene el id de anclaje correcto', () => {
    render(<NoticiasSection symbol="ADAUSDT" />);
    expect(document.getElementById('idea-noticias')).not.toBeNull();
  });
});
