import { useCallback, useEffect, useState } from 'react';
import { api, dollars, type ZonesResponse } from '../api';
import { Incentive, Page, Stat } from '../components';

const EVENT_STYLE: Record<string, { icon: string; cls: string }> = {
  DONATION: { icon: '🥬', cls: 'tag' },
  ESCALATED: { icon: '📣', cls: 'timer' },
  ALERT: { icon: '🚨', cls: 'timer' },
  EXPIRED: { icon: '🗑', cls: 'timer' },
};

export function OpsView() {
  const [data, setData] = useState<ZonesResponse | null>(null);
  const [events, setEvents] = useState<Array<{ at: string; kind: string; detail: string }>>([]);

  const refresh = useCallback(async () => {
    const [zonesRes, eventsRes] = await Promise.all([api.zones(), api.opsEvents()]);
    setData(zonesRes);
    setEvents(eventsRes.events);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (!data) return <Page title="Operations"><p className="muted">Loading…</p></Page>;

  return (
    <Page title="Operations" aside="Zone health · network impact · funding">
      <Incentive headline="Launch on density, not hope">
        A zone goes live only when supply, couriers, and hubs hit the playbook — and stays live
        only while the <strong>fill rate holds</strong>. Unfilled rescues are the death spiral;
        here they're impossible to hide.
      </Incentive>

      <div className="section-title">Zones</div>
      {data.zones.map((zone) => (
        <div key={zone.zoneId} className="card">
          <div className="row">
            <strong style={{ textTransform: 'capitalize' }}>{zone.zoneId}</strong>
            <span className={`status-pill status-${zone.status}`}>{zone.status.replaceAll('_', ' ')}</span>
          </div>
          <div className="row muted" style={{ marginTop: 8 }}>
            <span>🏪 {zone.suppliers} suppliers</span>
            <span>🚲 {zone.couriers} couriers</span>
            <span>🧊 {zone.hubs} hubs</span>
          </div>
          <div className="divider" />
          <div className="row muted">
            <span>Fill rate</span>
            <strong>{zone.fillRate !== undefined ? `${Math.round(zone.fillRate * 100)}%` : 'n/a'}</strong>
          </div>
          <div className="progressbar" style={{ marginTop: 6 }}>
            <div style={{ width: `${Math.round((zone.fillRate ?? 0) * 100)}%` }} />
          </div>
          <div className="row muted" style={{ marginTop: 8 }}>
            <span>Median time to rescue</span>
            <strong>{zone.medianMinutesToRescue ?? '—'} min</strong>
          </div>
          {zone.gaps.length > 0 && (
            <p className="muted" style={{ marginBottom: 0 }}>Gaps: {zone.gaps.join(' · ')}</p>
          )}
        </div>
      ))}

      <div className="section-title">Reliability feed · live</div>
      <div className="card">
        {events.length === 0 && (
          <p className="muted" style={{ margin: 0 }}>
            Quiet right now — donations, escalations, and expiries appear here the moment they happen.
          </p>
        )}
        {events.slice(0, 6).map((event, i) => (
          <div key={`${event.at}-${i}`} className="row" style={{ padding: '6px 0' }}>
            <span style={{ fontSize: 16 }}>{EVENT_STYLE[event.kind]?.icon ?? '•'}</span>
            <span style={{ flex: 1, fontSize: 12.5 }}>{event.detail}</span>
            <span className="muted" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {new Date(event.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ))}
      </div>

      <div className="section-title">Network impact (the Impact Ledger)</div>
      <div className="statrow">
        <Stat value={data.networkImpact.mealsRescued} label="meals rescued" />
        <Stat value={`${data.networkImpact.poundsDiverted} lb`} label="diverted from landfill" />
        <Stat value={`${data.networkImpact.co2eKg} kg`} label="CO₂e avoided" />
      </div>

      <div className="section-title">Community Fund</div>
      <div className="card">
        <div className="row muted"><span>Pool (every credit 100% backed)</span><strong>{dollars(data.fund.poolCents)}</strong></div>
        <div className="divider" />
        <div className="row muted"><span>Credits in circulation</span><strong>{dollars(data.fund.outstandingCredits)}</strong></div>
        <div className="divider" />
        <div className="row muted"><span>Headroom for next allocation</span><strong>{dollars(data.fund.headroomCredits)}</strong></div>
      </div>

      <div className="section-title">Sponsor impact meter</div>
      <div className="card">
        <strong style={{ fontSize: 13 }}>Greenpoint Community Bank</strong>
        <div className="row muted" style={{ marginTop: 8 }}><span>Direct grants</span><strong>{dollars(data.sponsor.grantedCents)}</strong></div>
        <div className="divider" />
        <div className="row muted"><span>Sale matching (auto-doubles purchases)</span><strong>{dollars(data.sponsor.matchedCents)}</strong></div>
        <div className="divider" />
        <div className="row muted"><span>Karma subsidy (couriers eat what they rescue)</span><strong>{dollars(data.sponsor.karmaSubsidyCents)}</strong></div>
      </div>
    </Page>
  );
}
