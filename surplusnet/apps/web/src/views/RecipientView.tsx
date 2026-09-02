import { useCallback, useEffect, useState } from 'react';
import { api, categoryEmoji, dollars, type Balances, type Delivery, type FeedItem, type FundState } from '../api';
import { Incentive, Page, Ring, TabBar, Toast, Tracker } from '../components';

const DEMO_USER = 'demo-recipient';
type Tab = 'explore' | 'claimed' | 'wallet' | 'profile';

const DIETS = ['All', 'vegan', 'vegetarian', 'gluten-free'];

export function RecipientView() {
  const [tab, setTab] = useState<Tab>('explore');
  const [items, setItems] = useState<FeedItem[]>([]);
  const [fund, setFund] = useState<FundState | null>(null);
  const [balances, setBalances] = useState<Balances | null>(null);
  const [claims, setClaims] = useState<Array<FeedItem & { delivery: Delivery | null }>>([]);
  const [selected, setSelected] = useState<FeedItem | null>(null);
  const [diet, setDiet] = useState('All');
  const [toast, setToast] = useState<string | null>(null);

  const notify = (message: string) => {
    setToast(message);
    setTimeout(() => setToast(null), 3200);
  };

  const refresh = useCallback(async () => {
    const [feed, wallet, claimed] = await Promise.all([
      api.feed(DEMO_USER),
      api.wallet(DEMO_USER),
      api.claims(DEMO_USER),
    ]);
    setItems(feed.items);
    setFund(feed.fund);
    setBalances(wallet.balances);
    setClaims(claimed.claims);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 8000);
    return () => clearInterval(timer);
  }, [refresh]);

  const buy = async (item: FeedItem) => {
    const credits = Math.min(balances?.communityCreditBalance ?? 0, item.priceCents);
    const cash = item.priceCents - credits;
    try {
      const result = await api.purchase({
        itemId: item.id,
        recipientId: DEMO_USER,
        cashCents: cash,
        communityCredits: credits,
      });
      setBalances(result.wallet);
      notify(
        `Claimed! ${dollars(result.receipt.fundContributionCents)} of your purchase just funded free meals for neighbors.`,
      );
      setSelected(null);
      void refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'purchase failed');
    }
  };

  const claimFree = async (item: FeedItem) => {
    try {
      await api.claimDonation(item.id, DEMO_USER);
      notify('Reserved for you at no cost — same checkout, same dignity, always.');
      setSelected(null);
      void refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'claim failed');
    }
  };

  const filtered = items.filter((i) => diet === 'All' || i.dietaryTags.includes(diet));

  const explore = (
    <>
      <Incentive headline="Premium surplus, up to 70% off">
        Every cash purchase sends <strong>20% into the Community Fund</strong> — your dinner
        literally funds a neighbor's. Fund pool: <strong>{fund ? dollars(fund.poolCents) : '…'}</strong>
      </Incentive>
      <div className="filterchips">
        {DIETS.map((d) => (
          <button key={d} className={d === diet ? 'active' : ''} onClick={() => setDiet(d)}>
            {d === 'All' ? '⚲ All' : d}
          </button>
        ))}
      </div>
      <div className="grid2">
        {filtered.map((item) => (
          <div key={item.id} className="card itemcard" onClick={() => setSelected(item)}>
            <div className="art">
              {categoryEmoji[item.category] ?? '🍽'}
              {item.state === 'SALES_PHASE' ? (
                <span className="badge price">Available now · {dollars(item.priceCents)}</span>
              ) : (
                <span className="badge free">Community · Free</span>
              )}
            </div>
            <div className="body">
              <h4>{item.title}</h4>
              <div className="supplier">{item.supplierId}</div>
              <div style={{ marginTop: 5 }}>
                {item.dietaryTags.slice(0, 2).map((t) => (
                  <span key={t} className="badge tag">{t}</span>
                ))}
              </div>
              <div className="countdown">
                {item.state === 'SALES_PHASE'
                  ? `→ free pool in ${item.minutesLeftInSale} min`
                  : `claim within ${item.minutesUntilUnsafe} min`}
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  );

  const detail = selected && (
    <>
      <button className="btn secondary small" onClick={() => setSelected(null)}>← Back to Blind Box</button>
      <div className="card" style={{ marginTop: 12 }}>
        <div className="art" style={{ height: 110, fontSize: 56, background: 'var(--green-100)', borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {categoryEmoji[selected.category] ?? '🍽'}
        </div>
        <h3 style={{ margin: '10px 0 2px' }}>{selected.title}</h3>
        <div className="muted">{selected.supplierId} · worth {dollars(selected.fmvCents)}</div>
        <div style={{ margin: '8px 0' }}>
          {selected.dietaryTags.map((t) => <span key={t} className="badge tag">{t}</span>)}
        </div>
        <div className="divider" />
        <div className="row muted">
          <span>🍽 {selected.impact.mealsRescued} meals rescued</span>
          <span>🌍 {(selected.impact.co2eGrams / 1000).toFixed(1)} kg CO₂ prevented</span>
        </div>
        <div className="divider" />
        {selected.state === 'SALES_PHASE' ? (
          <>
            <div className="row" style={{ marginBottom: 10 }}>
              <strong style={{ fontSize: 20 }}>{dollars(selected.priceCents)}</strong>
              <span className="badge timer">rolls to the free pool in {selected.minutesLeftInSale} min</span>
            </div>
            <button className="btn" onClick={() => void buy(selected)}>
              Claim now — credits & cash spend the same
            </button>
            <p className="muted" style={{ textAlign: 'center', marginTop: 8 }}>
              Your community credits are applied first, automatically.
            </p>
          </>
        ) : (
          <button className="btn" onClick={() => void claimFree(selected)}>Claim free — no stigma, ever</button>
        )}
      </div>
    </>
  );

  const claimed = (
    <>
      <Incentive headline="Your Surplus Claims">
        Track every rescue from kitchen to locker — cold-chain verified the whole way.
      </Incentive>
      {claims.length === 0 && <p className="muted">Nothing claimed yet — check the Explore tab.</p>}
      {claims.map((claim) => {
        const done = claim.state === 'DELIVERED' ? 4 : claim.delivery?.state === 'PICKED_UP' ? 2 : claim.delivery ? 2 : 1;
        return (
          <div key={claim.id} className="card">
            <div className="map">
              <span className="pin" style={{ left: '24%', top: '34%' }}>🏪</span>
              <span className="pin" style={{ left: '72%', top: '72%' }}>🧊</span>
              <div className="route" />
            </div>
            <div className="row">
              <h4 style={{ margin: 0 }}>{claim.title}</h4>
              <span className="badge tag">{claim.delivery?.dropoffName?.split('·')[0] ?? 'Direct pickup'}</span>
            </div>
            <Tracker steps={['Claimed', 'Courier en route', 'Ready for pickup', 'Delivered']} done={done} />
            {claim.state === 'DELIVERED' ? (
              <button className="btn">Unlock Locker A1 via Bluetooth</button>
            ) : (
              <button className="btn secondary" disabled>
                {claim.delivery ? 'Courier on the way…' : 'Matching a courier…'}
              </button>
            )}
          </div>
        );
      })}
    </>
  );

  const wallet = balances && (
    <>
      <div className="card" style={{ background: 'var(--green-900)', color: '#fff' }}>
        <div className="row">
          <div>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Community Credits</div>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{dollars(balances.communityCreditBalance)}</div>
          </div>
          <div>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Cash</div>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{dollars(balances.cashBalanceCents)}</div>
          </div>
          <div>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Karma</div>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{balances.karmaCreditBalance} KC</div>
          </div>
        </div>
      </div>
      <Incentive headline="One wallet, total dignity">
        Credits, cash, and karma spend <strong>identically</strong> at checkout — nobody, including
        the supplier, can tell which you used.
      </Incentive>
      <div className="section-title">Your contribution</div>
      <div className="card row">
        <Ring big={claims.filter((c) => c.state === 'DELIVERED' || c.state === 'CLAIMED').reduce((s, c) => s + c.impact.mealsRescued, 0).toFixed(0)} small="meals saved" pct={0.72} />
        <div style={{ flex: 1 }}>
          <div className="row muted"><span>CO₂ prevented</span><strong>{(claims.reduce((s, c) => s + c.impact.co2eGrams, 0) / 1000).toFixed(1)} kg</strong></div>
          <div className="divider" />
          <div className="row muted"><span>Pounds diverted</span><strong>{claims.reduce((s, c) => s + c.impact.poundsDiverted, 0).toFixed(1)} lb</strong></div>
          <div className="divider" />
          <div className="row muted"><span>Neighbors fed by your purchases</span><strong>💚</strong></div>
        </div>
      </div>
      <div className="section-title">Recent activity</div>
      {claims.slice(0, 4).map((c) => (
        <div key={c.id} className="card row" style={{ padding: '10px 14px' }}>
          <span style={{ fontSize: 20 }}>{categoryEmoji[c.category] ?? '🍽'}</span>
          <div style={{ flex: 1 }}>
            <strong style={{ fontSize: 13 }}>{c.title}</strong>
            <div className="muted">{c.state === 'DELIVERED' ? 'Delivered' : 'In progress'}</div>
          </div>
          <span className="badge tag">{c.priceCents === 0 ? 'free' : dollars(c.priceCents)}</span>
        </div>
      ))}
    </>
  );

  const profile = (
    <>
      <div className="card row">
        <div style={{ width: 52, height: 52, borderRadius: '50%', background: 'var(--green-100)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26 }}>🙂</div>
        <div style={{ flex: 1 }}>
          <strong>Jamie Neighbor</strong>
          <div className="muted">Greenpoint · member since Sept 2026</div>
        </div>
      </div>
      <Incentive headline="Give a neighbor a seat at the table">
        Invite a friend — when they claim their first box, <strong>you earn $3 in community
        credits</strong> (funded by the pool, never printed from thin air).
      </Incentive>
      <div className="card">
        <div className="section-title" style={{ margin: '0 0 8px' }}>Dietary profile</div>
        <span className="badge tag">no exclusions</span>
        <p className="muted" style={{ marginBottom: 0 }}>
          Set restrictions and your feed only ever shows boxes you can actually eat.
        </p>
      </div>
      <div className="card">
        <div className="section-title" style={{ margin: '0 0 8px' }}>Become a courier too</div>
        <p className="muted">
          Rescue a box on your walk home → earn karma → spend it on food, coffee, or transit.
          Most of our top couriers started as recipients.
        </p>
        <button className="btn secondary">Start rescuing (10-min certification)</button>
      </div>
    </>
  );

  return (
    <Page
      title={tab === 'explore' ? 'Blind Box' : tab === 'claimed' ? 'Your Surplus Claims' : tab === 'wallet' ? 'My Impact' : 'Profile'}
      aside={tab === 'explore' ? `${filtered.length} boxes near you` : undefined}
      tabs={<TabBar
        tabs={[
          { id: 'explore', label: 'Explore', icon: '🧭' },
          { id: 'claimed', label: 'Claimed', icon: '🛍' },
          { id: 'wallet', label: 'Wallet', icon: '👛' },
          { id: 'profile', label: 'Profile', icon: '👤' },
        ]}
        active={tab}
        onChange={(t) => { setTab(t); setSelected(null); }}
      />}
    >
      {tab === 'explore' && (selected ? detail : explore)}
      {tab === 'claimed' && claimed}
      {tab === 'wallet' && wallet}
      {tab === 'profile' && profile}
      <Toast message={toast} />
    </Page>
  );
}
