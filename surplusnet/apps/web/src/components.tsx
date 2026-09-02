import type { ReactNode } from 'react';

export function Phone({ title, subtitle, children, nav }: {
  title: ReactNode;
  subtitle?: string;
  children: ReactNode;
  nav?: ReactNode;
}) {
  return (
    <div className="phone">
      <div className="screen">
        <div className="statusbar">
          <span>9:41</span>
          <span>⦿ ᯤ ▮</span>
        </div>
        <div className="appbar">
          <div className="brand">{title}</div>
          {subtitle && <div className="subtitle">{subtitle}</div>}
        </div>
        <div className="content">{children}</div>
        {nav}
      </div>
    </div>
  );
}

export function BottomNav<T extends string>({ tabs, active, onChange }: {
  tabs: Array<{ id: T; label: string; icon: string }>;
  active: T;
  onChange: (tab: T) => void;
}) {
  return (
    <div className="bottomnav">
      {tabs.map((tab) => (
        <button key={tab.id} className={tab.id === active ? 'active' : ''} onClick={() => onChange(tab.id)}>
          <span className="icon">{tab.icon}</span>
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
      <div className="ring" style={{ width: 74, height: 74, background: '#fff' }}>
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
