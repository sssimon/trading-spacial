// recap.test.ts
import { it, expect } from 'vitest';
import { vidaRecap, dossierRecap } from './recap';

it('vidaRecap distingue viva / muy quieta / sin dato', () => {
  expect(vidaRecap({ symbol: 'X', estado: 'ok', candidata: true } as never)).toBe('Viva y tranquila');
  expect(vidaRecap({ symbol: 'X', estado: 'ok', candidata: false } as never)).toBe('Muy quieta');
  expect(vidaRecap(null)).toBe('—');
});

it('dossierRecap: rastreable / opaco / sin dato', () => {
  expect(dossierRecap({ estado_general: 'rastreable' } as never)).toBe('Se sabe quién');
  expect(dossierRecap({ estado_general: 'opaco' } as never)).toBe('Sin rastro público');
  expect(dossierRecap(null)).toBe('—');
});
