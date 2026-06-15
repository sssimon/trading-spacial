// Copilot.test.tsx
// La función canned() fue eliminada cuando se conectó el agente real
// (useAgentStream / surface='valles'). La doctrina anti-veredicto es
// ahora una responsabilidad del backend (system prompt + denylist +
// LLM judge). Ver doctrine.test.tsx para la prueba del bubble refusal.
//
// Este archivo cubre el estado "pensando": Valles buffea la respuesta
// (no streamea), así que el placeholder assistant queda vacío hasta el
// final. NO debe pintarse como burbuja vacía + un loader aparte (doble
// burbuja); debe ser UNA sola burbuja con el loader de tres puntos.
import { render, act } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import { Copilot } from './Copilot';
import type { ChatMsg } from '../../agent/useAgentStream';

// Hook mockeado: turno en vuelo → user + placeholder assistant vacío, loading.
vi.mock('../../agent/useAgentStream', () => {
  const msgs: ChatMsg[] = [
    { role: 'user', text: '¿cuál conviene comprar?' },
    { role: 'assistant', text: '', tool_chips: [] },
  ];
  return {
    useAgentStream: () => ({
      msgs,
      loading: true,
      sendTurn: vi.fn().mockResolvedValue(undefined),
      resetConversation: vi.fn(),
      confirmProposal: vi.fn(),
      loadConversation: vi.fn(),
      streamGreeting: vi.fn(),
      hydrating: false,
    }),
  };
});

it('mientras piensa muestra UN solo loader, no una burbuja vacía + puntos', async () => {
  await act(async () => {
    render(<Copilot onClose={() => {}} />);
  });
  // Exactamente un indicador de "pensando" (role=status).
  expect(document.querySelectorAll('[role="status"]').length).toBe(1);
  // Dos burbujas en total: la del usuario + la del loader. El placeholder
  // assistant vacío NO se pinta (si se pintara, serían tres).
  expect(document.querySelectorAll('[class*="vwBubble"]').length).toBe(2);
});
