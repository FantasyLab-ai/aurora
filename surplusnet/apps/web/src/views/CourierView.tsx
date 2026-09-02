import { useCallback, useEffect, useState } from 'react';
import { api, categoryEmoji, type CourierProfile, type Delivery, type FeedItem, type Offer } from '../api';
import { BottomNav, Incentive, Phone, Stat, Toast, Tracker } from '../components';

const DEMO_COURIER = 'demo-courier';
type Tab = 'offers' | 'rescue' | 'karma';

const badgeLabel: Record<string, string> = {
  'first-rescue': '⭐ First Rescue',
  'block-hero': '🏅 Block Hero',
  'neighborhood-legend': '🏆 Neighborhood Legend',
  'city-champion': '👑 City Champion',
  'cert:food-handler-101': '🎓 Certified Food Handler',
  'cert:cold-chain': '🧊 Cold-Chain Certified',
};

export function CourierView() {
  const [tab, setTab] = useState<Tab>('offers');
  const [offers, setOffers] = useState<Offer[]>([]);
  const [active, setActive] = useState<{ delivery: Delivery; item: FeedItem } | null>(null);
  const [profile, setProfile] = useState<CourierProfile | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const notify = (message: string) => {
    setToast(message);
    setTimeout(() => setToast(null), 3500);
  };

  const refresh = useCallback(async () => {
    const [offersRes, activeRes, profileRes] = await Promise.all([
      api.offers(DEMO_COURIER),
      api.active(DEMO_COURIER),
      api.courierProfile(DEMO_COURIER),
    ]);
    setOffers(offersRes.offers);
    setActive(activeRes.delivery && activeRes.item ? { delivery: activeRes.delivery, item: activeRes.item } : null);
    setProfile(profileRes);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 8000);
    return () => clearInterval(timer);
  }, [refresh]);

  const accept = async (offer: Offer) => {
    try {
      await api.accept({ itemId: offer.item.id, courierId: DEMO_COURIER, karma: offer.quote.karma, hubId: offer.hub.hubId });
      notify(`Rescue accepted — ${offer.quote.karma} KC locked in. Surge can't drop mid-run.`);
      setTab('rescue');
      void refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'accept failed');
    }
  };

  const advance = async (action: 'pickup' | 'temp' | 'dropoff') => {
    if (!active) return;
    try {
      if (action === 'pickup') await api.pickup(active.delivery.id);
      if (action === 'temp') await api.temp(active.delivery.id, 3.4);
      if (action === 'dropoff') {
        const result = await api.dropoff(active.delivery.id);
        notify(`Drop verified! +${active.delivery.karmaOnCompletion} KC minted · streak extended · volunteer minutes logged.`);
        void result;
      }
      void refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : 'action failed');
    }
  };

  const offersTab = (
    <>
      <Incentive headline="Rainy night = surge karma">
        Weather is <strong>{offers[0]?.weather ?? '…'}</strong> and safety windows are closing —
        rescues right now pay up to <strong>3× karma</strong>. Karma buys coffee, transit… and food.
      </Incentive>
      {offers.length === 0 && <p className="muted">No open rescues in your zone — check back soon.</p>}
      {offers.map((offer) => (
        <div key={offer.item.id} className="card">
          <div className="row">
            <span style={{ fontSize: 30 }}>{categoryEmoji[offer.item.category] ?? '🍽'}</span>
            <div style={{ flex: 1 }}>
              <strong style={{ fontSize: 14 }}>{offer.item.title}</strong>
              <div className="muted">{offer.item.supplierId} → {offer.hub.name.split('·')[1] ?? offer.hub.name}</div>
            </div>
            <span className="badge surge">+{offer.quote.karma} KC</span>
          </div>
          <div className="reason-chips">
            <span className="badge tag">{(offer.distanceMeters / 1609.344).toFixed(1)} mi</span>
            <span className="badge tag">safe for {offer.item.minutesUntilUnsafe} min</span>
            {offer.quote.reasons.map((r) => (
              <span key={r} className="badge timer">⚡ {r}</span>
            ))}
          </div>
          <div style={{ marginTop: 10 }}>
            <button className="btn" onClick={() => void accept(offer)} disabled={active !== null}>
              Accept rescue · {offer.quote.karma} KC ({offer.quote.multiplier}× surge)
            </button>
          </div>
        </div>
      ))}
    </>
  );

  const rescueTab = active ? (
    <>
      <div className="card">
        <div className="map">
          <span className="pin" style={{ left: '24%', top: '34%' }}>🥐</span>
          <span className="pin" style={{ left: '72%', top: '72%' }}>🧊</span>
          <div className="route" />
        </div>
        <div className="row">
          <h4 style={{ margin: 0 }}>{active.item.title}</h4>
          <span className="badge surge">+{active.delivery.karmaOnCompletion} KC on drop</span>
        </div>
        <div className="muted" style={{ marginTop: 4 }}>Drop at: {active.delivery.dropoffName}</div>
        <Tracker
          steps={['Accepted', 'Picked up', 'Temp verified', 'Dropped off']}
          done={active.delivery.state === 'ACCEPTED' ? 1 : active.delivery.tempReadings.length > 0 ? 3 : 2}
        />
        {active.delivery.state === 'ACCEPTED' && (
          <button className="btn" onClick={() => void advance('pickup')}>Confirm pickup at back door</button>
        )}
        {active.delivery.state === 'PICKED_UP' && active.delivery.tempReadings.length === 0 && (
          <button className="btn" onClick={() => void advance('temp')}>Log bin temperature (3.4 °C)</button>
        )}
        {active.delivery.state === 'PICKED_UP' && active.delivery.tempReadings.length > 0 && (
          <button className="btn" onClick={() => void advance('dropoff')}>Drop off & mint karma</button>
        )}
        <p className="muted" style={{ textAlign: 'center', marginTop: 8 }}>
          {active.delivery.coldChainCompliant ? '🧊 Cold chain intact — your handoff is certificate-grade' : '⚠️ Temp excursion flagged'}
        </p>
      </div>
    </>
  ) : (
    <p className="muted">No active rescue — accept one from Offers.</p>
  );

  const karmaTab = profile && (
    <>
      <div className="card" style={{ background: 'var(--green-900)', color: '#fff' }}>
        <div className="row">
          <div>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Karma Credits</div>
            <div style={{ fontSize: 30, fontWeight: 800 }}>{profile.balances.karmaCreditBalance} KC</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Streak</div>
            <div style={{ fontSize: 22, fontWeight: 800 }}>🔥 {profile.engagement?.currentStreakDays ?? 0} days</div>
          </div>
        </div>
      </div>
      <Incentive headline="Karma is real local value">
        Spend it at partner cafés and transit, <strong>or on surplus food itself</strong> —
        sponsors back every credit in cash, so merchants and suppliers are always made whole.
        Your rescues also log <strong>verified volunteer hours</strong> with your employer.
      </Incentive>
      <div className="statrow">
        <Stat value={profile.engagement?.totalDeliveries ?? 0} label="lifetime rescues" />
        <Stat value={profile.engagement?.longestStreakDays ?? 0} label="best streak" />
        <Stat value={profile.team?.name.split(' ')[0] ?? '—'} label="team" />
      </div>
      <div className="section-title">Badges</div>
      <div className="card">
        {(profile.engagement?.badges ?? []).map((b) => (
          <span key={b} className="badge gold" style={{ marginRight: 6, marginBottom: 4, display: 'inline-block' }}>
            {badgeLabel[b] ?? b}
          </span>
        ))}
      </div>
      <div className="section-title">Redeem karma</div>
      {profile.perks.map((perk) => (
        <div key={perk.perkId} className="card row" style={{ padding: '10px 14px' }}>
          <div style={{ flex: 1 }}>
            <strong style={{ fontSize: 13 }}>{perk.title}</strong>
            <div className="muted">{perk.inventory} left</div>
          </div>
          <button
            className="btn small"
            disabled={profile.balances.karmaCreditBalance < perk.costKarma}
            onClick={async () => {
              try {
                await api.redeemPerk(DEMO_COURIER, perk.perkId);
                notify(`Voucher issued for “${perk.title}” — show it at the counter.`);
                void refresh();
              } catch (err) {
                notify(err instanceof Error ? err.message : 'redeem failed');
              }
            }}
          >
            {perk.costKarma} KC
          </button>
        </div>
      ))}
      <div className="section-title">Team leaderboard · this month</div>
      {profile.teamLeaderboard.map((team) => (
        <div key={team.rank} className="card row" style={{ padding: '10px 14px' }}>
          <strong style={{ width: 22 }}>{team.rank}</strong>
          <div style={{ flex: 1 }}>
            <strong style={{ fontSize: 13 }}>{team.name}</strong>
            <div className="muted">{team.activeMembers} active members</div>
          </div>
          <span className="badge tag">{team.rescues} rescues</span>
        </div>
      ))}
    </>
  );

  return (
    <Phone
      title={<>Surplus<span>Net</span> Courier</>}
      subtitle={tab === 'offers' ? 'Open rescues nearby' : tab === 'rescue' ? 'Active rescue' : 'Karma & status'}
      nav={<BottomNav
        tabs={[
          { id: 'offers', label: 'Offers', icon: '📍' },
          { id: 'rescue', label: 'Rescue', icon: '🚲' },
          { id: 'karma', label: 'Karma', icon: '✨' },
        ]}
        active={tab}
        onChange={setTab}
      />}
    >
      {tab === 'offers' && offersTab}
      {tab === 'rescue' && rescueTab}
      {tab === 'karma' && karmaTab}
      <Toast message={toast} />
    </Phone>
  );
}
