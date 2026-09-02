import type { ReactNode } from 'react';

/** Standalone page shell: heading + optional tab row. Replaces the old phone frame. */
export function Page({ title, aside, tabs, children }: {
  title: string;
  aside?: ReactNode;
  tabs?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="container">
      <div className="pagehead">
        <h2>{title}</h2>
        {aside && <span className="muted">{aside}</span>}
      </div>
      {tabs}
      {children}
    </main>
  );
}

export function TabBar<T extends string>({ tabs, active, onChange }: {
  tabs: Array<{ id: T; label: string; icon: string }>;
  active: T;
  onChange: (tab: T) => void;
}) {
  return (
    <div className="tabs">
      {tabs.map((tab) => (
        <button key={tab.id} className={tab.id === active ? 'active' : ''} onClick={() => onChange(tab.id)}>
          <span>{tab.icon}</span>
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function Incentive({ headline, children }: { headline: string; children: ReactNode }) {
  return (
    <div className="incentive">
      <div className="headline">{headline}</div>
      {children}
    </div>
  );
}

export function Stat({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div className="stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

export function Ring({ big, small, pct }: { big: ReactNode; small: string; pct: number }) {
  const angle = Math.min(1, Math.max(0, pct)) * 360;
  return (
    <div
      className="ring"
      style={{ background: `conic-gradient(var(--green-700) ${angle}deg, var(--green-100) ${angle}deg)` }}
    >
      <div className="ring" style={{ width: 76, height: 76, background: '#fff' }}>
        <div className="big">{big}</div>
        <div className="small">{small}</div>
      </div>
    </div>
  );
}

export function Tracker({ steps, done }: { steps: string[]; done: number }) {
  return (
    <div className="tracker">
      {steps.map((step, i) => (
        <div key={step} className={`step ${i < done ? 'done' : ''}`}>
          <span className="dot">{i < done ? '✓' : i + 1}</span>
          {step}
        </div>
      ))}
    </div>
  );
}

export function Toast({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="toast">{message}</div>;
}
