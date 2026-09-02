import { useCallback, useEffect, useState } from 'react';
import { api, dollars, type ZonesResponse } from '../api';
import { Incentive, Phone, Stat } from '../components';

export function OpsView() {
  const [data, setData] = useState<ZonesResponse | null>(null);

  const refresh = useCallback(async () => {
    setData(await api.zones());
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (!data) return <Phone title={<>Surplus<span>Net</span> Ops</>}><p className="muted">Loading…</p></Phone>;

  return (
    <Phone title={<>Surplus<span>Net</span> Ops</>} subtitle="Zone health · network impact · funding">
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
    </Phone>
  );
}
