import React from 'react';
import type { ObservedOrder } from '../types';
import styles from './ObservedOrders.module.css';

// Lista de órdenes de protección observadas en Binance (v0.3, read-only).
// "SL 50.000 (25%)" por orden; badge "sin stop" si ningún SL protege el hold.
export const ObservedOrdersList: React.FC<{ orders: ObservedOrder[] }> = ({ orders }) => {
  const hasSl = orders.some((o) => o.kind === 'SL');
  return (
    <div className={styles.wrap}>
      {!hasSl && <span className={`${styles.noStop} label`}>sin stop</span>}
      {orders.map((o) => (
        <span
          key={o.order_id}
          className={`${styles.order} ${o.kind === 'SL' ? styles.orderSl : styles.orderTp} num`}
        >
          {o.kind} {o.price.toLocaleString('es-VE')}
          {o.pct_holding != null && ` (${Math.round(o.pct_holding * 100)}%)`}
        </span>
      ))}
    </div>
  );
};
