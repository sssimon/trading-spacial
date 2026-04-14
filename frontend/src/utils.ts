// ============================================================
// utils.ts — Shared utility functions used across components
// ============================================================

/**
 * Format a timestamp as relative time (e.g. "hace 5m", "hace 2h").
 */
export function timeAgo(ts: string): string {
  const now = Date.now();
  const then = new Date(ts).getTime();
  const diffMs = now - then;

  if (isNaN(diffMs)) return '—';

  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `hace ${diffSec}s`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `hace ${diffMin}m`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `hace ${diffHour}h`;
  const diffDay = Math.floor(diffHour / 24);
  return `hace ${diffDay}d`;
}

/**
 * Format a price with appropriate decimal places based on magnitude.
 *
 * - >= 1000 → 2 decimals  (e.g. "67,432.10")
 * - >= 1    → 4 decimals  (e.g. "1.2345")
 * - < 1     → 5–6 decimals (e.g. "0.00012")
 *
 * Returns '—' for null / undefined values.
 */
export function formatPrice(val: number | null | undefined): string {
  if (val == null) return '—';

  if (val >= 1000) {
    return val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  if (val >= 1) {
    return val.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  }
  return val.toLocaleString('en-US', { minimumFractionDigits: 5, maximumFractionDigits: 6 });
}
