// Copilot.test.tsx
// La función canned() fue eliminada cuando se conectó el agente real
// (useAgentStream / surface='valles'). La doctrina anti-veredicto es
// ahora una responsabilidad del backend (system prompt + denylist +
// LLM judge). Las preguntas trampa llegan al agente real y se rechazan
// con un evento SSE `refusal`. Ver doctrine.test.tsx para la prueba
// de integración del bubble vwBubbleRefusal.
import { it } from 'vitest';
it.todo('canned() eliminado — doctrina cubierta en doctrine.test.tsx');
