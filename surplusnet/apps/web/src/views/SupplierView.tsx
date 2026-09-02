import { useCallback, useEffect, useState } from 'react';
import { api, categoryEmoji, dollars, type SupplierDashboard } from '../api';
import { BottomNav, Incentive, Phone, Stat, Toast } from '../components';

const DEMO_SUPPLIER = 'daily-knead';
type Tab = 'today' | 'reports';

export function SupplierView() {
  const [tab, setTab] = useState<Tab>('today');
  const [dash, setDash] = useState<SupplierDashboard | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const notify = (message: string) => {
    setToast(message);
    setTimeout(() => setToast(null), 3200);
  };

  const refresh = useCallback(async () => {
    setDash(await api.supplierDashboard(DEMO_SUPPLIER));
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 10_000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (!dash) return <Phone title={<>Surplus<span>Net</span> Business</>}><p className="muted">Loading…</p></Phone>;

  const today = (
    <>
      <Incentive headline="Zero labor. Three numbers your CFO loves.">
        Donating here is <strong>more automatic than the dumpster</strong>: your standing schedule
        lists tonight's box for you, the tax math is instant, and every handoff ships with a
        <strong> liability certificate</strong> (Bill Emerson Act — zero US lawsuits, ever).
      </Incentive>
      <div className="statrow">
        <Stat value={dollars(dash.report.taxDeductionCents)} label="tax deduction · this month" />
        <Stat value={dollars(dash.report.avoidedDisposalCents)} label="hauling fees avoided" />
        <Stat value={`${dash.report.co2eKg} kg`} label="CO₂e for your ESG report" />
      </div>
      <div className="section-title">Standing schedule</div>
      {dash.schedules.map((s) => (
        <div key={s.scheduleId} className="card row">
          <span style={{ fontSize: 24 }}>⏰</span>
          <div style={{ flex: 1 }}>
            <strong style={{ fontSize: 13 }}>{s.title}</strong>
            <div className="muted">auto-lists daily at close · {s.paused ? 'paused' : 'active'}</div>
          </div>
          <button
            className="btn small secondary"
            onClick={async () => {
              await api.skipToday(s.scheduleId);
              notify('Tonight skipped — schedule untouched. Nothing else to do.');
            }}
          >
            Nothing left today
          </button>
        </div>
      ))}
      <div className="section-title">Recent listings</div>
      {dash.items.slice(0, 5).map((item) => (
        <div key={item.id} className="card row" style={{ padding: '10px 14px' }}>
          <span style={{ fontSize: 20 }}>{categoryEmoji[item.category] ?? '🍽'}</span>
          <div style={{ flex: 1 }}>
            <strong style={{ fontSize: 13 }}>{item.title}</strong>
            <div className="muted">
              {item.state === 'SALES_PHASE' ? `selling · ${item.minutesLeftInSale}m to donation pool`
                : item.state === 'DONATION_PHASE' ? 'in community pool'
                : item.state.toLowerCase()}
            </div>
          </div>
          <span className="badge tag">+{dollars(item.taxDeductionCents)} deduction</span>
        </div>
      ))}
    </>
  );

  const reports = (
    <>
      <Incentive headline="Audit-ready. Regulator-ready. Board-ready.">
        One tap exports your <strong>SB 1383-style recovery filing</strong>, the IRS 170(e)(3)
        deduction ledger (hash-chained, tamper-evident), and Scope-3 CO₂e — compliance as a
        by-product of doing the right thing.
      </Incentive>
      <div className="card">
        <div className="section-title" style={{ margin: '0 0 10px' }}>This month's recovery record</div>
        <div className="row muted"><span>Donations completed</span><strong>{dash.report.itemCount}</strong></div>
        <div className="divider" />
        <div className="row muted"><span>Pounds recovered</span><strong>{dash.report.poundsRecovered} lb</strong></div>
        <div className="divider" />
        <div className="row muted"><span>Meals rescued</span><strong>{dash.report.mealsRescued}</strong></div>
        <div className="divider" />
        <div className="row muted"><span>Receiving entities</span><strong style={{ textAlign: 'right', fontSize: 11 }}>{dash.report.destinations.join(', ') || '—'}</strong></div>
        <div className="divider" />
        <div className="row muted"><span>Enhanced tax deduction</span><strong>{dollars(dash.report.taxDeductionCents)}</strong></div>
        <button className="btn" style={{ marginTop: 12 }} onClick={() => notify('Audit-ready PDF queued for your accountant.')}>
          Export audit-ready PDF
        </button>
      </div>
      <div className="card">
        <div className="section-title" style={{ margin: '0 0 8px' }}>Liability shield</div>
        <p className="muted" style={{ marginTop: 0 }}>
          Every completed donation carries a certificate: custody chain, timestamped cold-chain log,
          courier certification, and the Bill Emerson good-faith statement. Documented better than
          your own kitchen logs.
        </p>
        <span className="badge tag">🧊 cold chain verified</span>{' '}
        <span className="badge tag">📜 custody documented</span>{' '}
        <span className="badge tag">⚖️ Emerson Act</span>
      </div>
    </>
  );

  return (
    <Phone
      title={<>Surplus<span>Net</span> Business</>}
      subtitle="The Daily Knead · Greenpoint"
      nav={<BottomNav
        tabs={[
          { id: 'today', label: 'Today', icon: '🏪' },
          { id: 'reports', label: 'Reports', icon: '📊' },
        ]}
        active={tab}
        onChange={setTab}
      />}
    >
      {tab === 'today' && today}
      {tab === 'reports' && reports}
      <Toast message={toast} />
    </Phone>
  );
}
