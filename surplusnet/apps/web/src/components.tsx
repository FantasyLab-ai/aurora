import type { ReactNode } from 'react';
import type { RoutePoint } from './api';

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

/**
 * Route drawing: real geometry (OSRM) or an estimate, scaled into an SVG.
 * No tile server needed, so it renders identically in the hosted app and
 * the self-contained demo.
 */
export function RouteMap({ points, fromEmoji = '🏪', toEmoji = '🧊' }: {
  points: RoutePoint[] | null;
  fromEmoji?: string;
  toEmoji?: string;
}) {
  if (!points || points.length < 2) {
    return <div className="map" />;
  }
  const W = 100;
  const H = 56;
  const PAD = 10;
  const lats = points.map((p) => p.latitude);
  const lngs = points.map((p) => p.longitude);
  const minLat = Math.min(...lats);
  const minLng = Math.min(...lngs);
  const spanLat = Math.max(Math.max(...lats) - minLat, 0.0005);
  const spanLng = Math.max(Math.max(...lngs) - minLng, 0.0005);
  const x = (lng: number) => PAD + ((lng - minLng) / spanLng) * (W - 2 * PAD);
  const y = (lat: number) => H - PAD - ((lat - minLat) / spanLat) * (H - 2 * PAD);
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.longitude).toFixed(1)},${y(p.latitude).toFixed(1)}`).join(' ');
  const first = points[0]!;
  const last = points[points.length - 1]!;

  return (
    <div className="map">
      <svg viewBox={`0 0 ${W} ${H}`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
        <path d={path} fill="none" stroke="var(--green-700)" strokeWidth="2" strokeDasharray="4 2.5" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={x(first.longitude)} cy={y(first.latitude)} r="2.4" fill="var(--amber)" />
        <circle cx={x(last.longitude)} cy={y(last.latitude)} r="2.4" fill="var(--green-800)" />
        <text x={x(first.longitude)} y={y(first.latitude) - 4} fontSize="8" textAnchor="middle">{fromEmoji}</text>
        <text x={x(last.longitude)} y={y(last.latitude) - 4} fontSize="8" textAnchor="middle">{toEmoji}</text>
      </svg>
    </div>
  );
}
