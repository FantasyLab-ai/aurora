import { useCallback, useEffect, useState } from 'react';
import { api, categoryEmoji, dollars, type Certificate, type SupplierDashboard } from '../api';
import { Incentive, Page, Stat, TabBar, Toast } from '../components';

const DEMO_SUPPLIER = 'daily-knead';
type Tab = 'today' | 'reports';

const DEFAULT_FORM = {
  title: 'Evening Surplus Box',
  category: 'bakery',
  fmv: '14.30',
  cogs: '5.00',
  price: '4.29',
  listAt: '21',
  safeHours: '12',
};

export function SupplierView() {
  const [tab, setTab] = useState<Tab>('today');
  const [dash, setDash] = useState<SupplierDashboard | null>(null);
  const [cert, setCert] = useState<Certificate | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(DEFAULT_FORM);
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

  if (!dash) return <Page title="Business"><p className="muted">Loading…</p></Page>;

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
      <div className="card">
        {!showForm ? (
          <div className="row">
            <div>
              <strong style={{ fontSize: 13.5 }}>Add another standing schedule</strong>
              <div className="muted">Describe your surplus rhythm once — never think about it again.</div>
            </div>
            <button className="btn small" onClick={() => setShowForm(true)}>＋ New schedule</button>
          </div>
        ) : (
          <>
            <strong style={{ fontSize: 13.5 }}>New standing schedule</strong>
            <div className="formgrid">
              <div className="field" style={{ gridColumn: '1 / -1' }}>
                <label htmlFor="sched-title">What's usually left</label>
                <input id="sched-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="sched-cat">Category</label>
                <select id="sched-cat" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                  <option value="bakery">Bakery</option>
                  <option value="produce">Produce</option>
                  <option value="dairy">Dairy</option>
                  <option value="prepared">Prepared</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="sched-fmv">Retail worth ($)</label>
                <input id="sched-fmv" value={form.fmv} onChange={(e) => setForm({ ...form, fmv: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="sched-cogs">Your cost ($)</label>
                <input id="sched-cogs" value={form.cogs} onChange={(e) => setForm({ ...form, cogs: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="sched-price">Sale price ($)</label>
                <input id="sched-price" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="sched-hour">Lists daily at (UTC)</label>
                <select id="sched-hour" value={form.listAt} onChange={(e) => setForm({ ...form, listAt: e.target.value })}>
                  {Array.from({ length: 24 }, (_, h) => (
                    <option key={h} value={String(h)}>{String(h).padStart(2, '0')}:00</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="sched-safe">Safe for (hours)</label>
                <input id="sched-safe" value={form.safeHours} onChange={(e) => setForm({ ...form, safeHours: e.target.value })} />
              </div>
            </div>
            <div className="row">
              <button className="btn small secondary" onClick={() => setShowForm(false)}>Cancel</button>
              <button
                className="btn small"
                onClick={async () => {
                  try {
                    await api.addSchedule({
                      supplierId: DEMO_SUPPLIER,
                      title: form.title,
                      category: form.category,
                      fmvCents: Math.round(Number(form.fmv) * 100),
                      cogsCents: Math.round(Number(form.cogs) * 100),
                      salePriceCents: Math.round(Number(form.price) * 100),
                      listAtHourUtc: Number(form.listAt),
                      safeForHours: Number(form.safeHours),
                    });
                    setShowForm(false);
                    setForm(DEFAULT_FORM);
                    notify('Schedule live — your surplus now lists itself. That was the last step.');
                    void refresh();
                  } catch (err) {
                    notify(err instanceof Error ? err.message : 'could not create schedule');
                  }
                }}
              >
                Activate — zero labor from here
              </button>
            </div>
          </>
        )}
      </div>
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
      {dash.items.slice(0, 6).map((item) => (
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
          {item.state === 'DELIVERED' && (
            <button
              className="btn small secondary"
              onClick={async () => {
                const result = await api.certificate(item.id);
                setCert(result.certificate);
              }}
            >
              📜 Certificate
            </button>
          )}
        </div>
      ))}
      {cert && (
        <div className="card" style={{ border: '2px solid var(--green-700)' }}>
          <div className="row">
            <strong style={{ fontSize: 14 }}>Liability certificate · {cert.itemSummary.title}</strong>
            <button className="btn small secondary" onClick={() => setCert(null)}>Close</button>
          </div>
          <div style={{ margin: '8px 0' }}>
            <span className={`badge ${cert.overallCompliant ? 'tag' : 'timer'}`}>
              {cert.overallCompliant ? '✓ fully compliant' : '⚠ excursion on record'}
            </span>{' '}
            <span className="badge tag">{cert.coldChainCompliant ? '🧊 cold chain intact' : '🌡 temp excursion'}</span>{' '}
            <span className="badge tag">{cert.safeUntilRespected ? '⏱ delivered in window' : '⏱ window missed'}</span>
          </div>
          <div className="timeline">
            {cert.custodyChain.map((event) => (
              <div key={event.event} className="event">
                <span className="tdot" />
                <span className="when">{new Date(event.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                <span><strong>{event.event.replaceAll('_', ' ').toLowerCase()}</strong> · {event.actor}</span>
              </div>
            ))}
          </div>
          {cert.tempLog.length > 0 && (
            <p className="muted" style={{ margin: '4px 0' }}>
              Temperature log: {cert.tempLog.map((t) => `${t.celsius.toFixed(1)}°C`).join(' → ')}
            </p>
          )}
          <p className="muted" style={{ marginBottom: 0, fontStyle: 'italic' }}>{cert.goodFaithStatement}</p>
        </div>
      )}
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
    <Page
      title="The Daily Knead"
      aside="Business account · Greenpoint"
      tabs={<TabBar
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
    </Page>
  );
}
