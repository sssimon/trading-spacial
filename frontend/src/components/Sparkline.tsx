import React from 'react';

interface SparklineProps {
  outcomes: Array<'W' | 'L' | null>;
}

const Sparkline: React.FC<SparklineProps> = ({ outcomes }) => {
  const wins = outcomes.filter(o => o === 'W').length;
  const losses = outcomes.filter(o => o === 'L').length;
  const empty = outcomes.filter(o => o === null).length;
  const ariaLabel = `Últimos ${outcomes.length} trades: ${wins} wins, ${losses} losses, ${empty} sin datos`;

  return (
    <div className="ks-sparkline" role="img" aria-label={ariaLabel}>
      {outcomes.map((outcome, i) => {
        const cls = outcome === 'W'
          ? 'ks-spark-cell ks-spark-win'
          : outcome === 'L'
          ? 'ks-spark-cell ks-spark-loss'
          : 'ks-spark-cell ks-spark-empty';
        return <span key={i} className={cls} />;
      })}
    </div>
  );
};

export default Sparkline;
