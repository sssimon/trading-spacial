import React from 'react';
import type { ObservedOrder } from '../types';
import styles from './ObservedOrders.module.css';
import { formatPrice } from '../utils';

// Chip individual para una orden observada.
const OrderChip: React.FC<{ order: ObservedOrder }> = ({ order: o }) => (
  <span
    key={o.order_id}
    className={`${styles.order} ${o.kind === 'SL' ? styles.orderSl : styles.orderTp} num`}
  >
    {o.kind} {formatPrice(o.price)}
    {o.pct_holding != null && ` (${Math.round(o.pct_holding * 100)}%)`}
  </span>
);

// Lista de órdenes de protección observadas en Binance (v0.3, read-only).
// "SL 50.000 (25%)" por orden; badge "sin stop" si ningún SL protege el hold.
// Órdenes que comparten oco_group se agrupan como una unidad visual (.ocoPair).
export const ObservedOrdersList: React.FC<{ orders: ObservedOrder[] }> = ({ orders }) => {
  const hasSl = orders.some((o) => o.kind === 'SL');

  // Agrupar preservando el orden de llegada (API ya ordena por symbol, kind, qty DESC).
  // Dentro de cada par OCO: SL primero, luego TP.
  type Item =
    | { type: 'pair'; group: number; members: ObservedOrder[] }
    | { type: 'single'; order: ObservedOrder };

  const items: Item[] = [];
  const seenGroups = new Map<number, Item & { type: 'pair' }>();

  for (const o of orders) {
    if (o.oco_group != null) {
      const existing = seenGroups.get(o.oco_group);
      if (existing) {
        existing.members.push(o);
      } else {
        const pair: Item & { type: 'pair' } = {
          type: 'pair',
          group: o.oco_group,
          members: [o],
        };
        seenGroups.set(o.oco_group, pair);
        items.push(pair);
      }
    } else {
      items.push({ type: 'single', order: o });
    }
  }

  // Dentro de cada par: SL primero, luego TP.
  for (const item of items) {
    if (item.type === 'pair') {
      item.members.sort((a, b) => (a.kind === 'SL' ? -1 : b.kind === 'SL' ? 1 : 0));
    }
  }

  return (
    <div className={styles.wrap}>
      {!hasSl && <span className={styles.noStop}>sin stop</span>}
      {items.map((item) => {
        if (item.type === 'pair') {
          return (
            <span
              key={`oco-${item.group}`}
              className={styles.ocoPair}
              data-testid="oco-pair"
            >
              {item.members.map((o) => <OrderChip key={o.order_id} order={o} />)}
            </span>
          );
        }
        return <OrderChip key={item.order.order_id} order={item.order} />;
      })}
    </div>
  );
};
