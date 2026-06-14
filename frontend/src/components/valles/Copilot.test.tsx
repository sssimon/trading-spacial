// Copilot.test.tsx
import { it, expect } from 'vitest';
import { canned } from './Copilot';

it.each(['¿en cuál entro?', '¿vale la pena ADA?', '¿qué harías tú?', 'should I buy', '¿cuál compro?', '¿cuánto pongo?'])(
  'rechaza intent de decisión/sizing: "%s"', (q) => {
    expect(canned(q).refusal).toBe(true);
  });

it('responde con hecho (sin refusal) a "¿qué es en valle?"', () => {
  expect(canned('¿qué es en valle?').refusal).toBeFalsy();
});
