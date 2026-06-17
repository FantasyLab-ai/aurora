/* =====================================================================
   Aurora Fire Overlay — connects to the relay's SSE stream, renders
   the "Aurora fired" card on every `fire` event, auto-dismisses
   after a severity-tiered duration.

   URL params (append to the OBS browser-source URL):
     ?relay=http://127.0.0.1:7077   (default — where the relay lives)
     ?hold=8000                     (ms to hold the card, default 8000)
     ?corner=tr|tl|br|bl            (default tr — top right)
     ?sound=1|0                     (default 1 — play the cue audio)
     ?demo=1                        (render a static demo card; no SSE)
   ===================================================================== */
(() => {
  const params  = new URLSearchParams(window.location.search);
  const RELAY   = params.get('relay')  || (window.location.origin || 'http://127.0.0.1:7077');
  const HOLD_MS = parseInt(params.get('hold')   || '8000', 10);
  const CORNER  = (params.get('corner') || 'tr').toLowerCase();
  const SOUND   = params.get('sound') !== '0';
  const DEMO    = params.get('demo')  === '1';

  const $       = (sel) => document.querySelector(sel);
  const anchor  = $('#anchor');
  const card    = $('#card');
  const crumb   = $('#statusCrumb');
  const crumbDot   = $('#crumbDot');
  const crumbLabel = $('#crumbLabel');
  const fireSound  = $('#fireSound');

  if (['tr','tl','br','bl'].includes(CORNER)) {
    anchor.classList.remove('anchor-tr', 'anchor-tl', 'anchor-br', 'anchor-bl');
    anchor.classList.add('anchor-' + CORNER);
    anchor.dataset.corner = CORNER;
  }

  // ---------------------------------------------------------------------
  // Card render
  // ---------------------------------------------------------------------

  const refs = {
    sev:        $('#sev'),
    method:     $('#method'),
    contract:   $('#contractName'),
    field:      $('#triggerField'),
    value:      $('#triggerValue'),
    rowCell:    $('#rowCell'),
    row:        $('#row'),
    zCell:      $('#zCell'),
    z:          $('#zScore'),
    fab:        $('#fabricatedChip'),
    run:        $('#runChip'),
    time:       $('#timeChip'),
  };

  let _dismissTimer = null;

  function showCard(env) {
    const sev = String(env.severity || 'info').toLowerCase();
    card.dataset.severity = sev;
    card.dataset.exit = '';
    card.hidden = false;
    crumb.hidden = true;

    refs.sev.textContent      = sev.toUpperCase();
    refs.method.textContent   = env.method || '—';
    refs.contract.textContent = env.contract_name || env.contract_id || '(contract)';
    refs.field.textContent    = String(env.trigger_field ?? '—');
    refs.value.textContent    = formatValue(env.trigger_value);

    if (env.row !== null && env.row !== undefined) {
      refs.row.textContent = String(env.row);
      refs.rowCell.hidden = false;
    } else {
      refs.rowCell.hidden = true;
    }

    if (env.z_score !== null && env.z_score !== undefined) {
      refs.z.textContent = Math.abs(Number(env.z_score)).toFixed(2) + 'σ';
      refs.zCell.hidden = false;
    } else {
      refs.zCell.hidden = true;
    }

    const fab = Number(env.fabricated || 0);
    refs.fab.textContent = `${fab} FABRICATED`;
    refs.fab.dataset.fab = fab === 0 ? 'good' : 'bad';

    refs.run.textContent  = env.bundle_run_id ? truncate(env.bundle_run_id, 26) : 'no-bundle';
    refs.time.textContent = new Date().toLocaleTimeString([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });

    if (SOUND && fireSound) {
      try { fireSound.currentTime = 0; fireSound.play().catch(() => {}); } catch (_) {}
    }

    // Severity-tiered hold. Crit holds longer so it's readable on TikTok.
    const hold = sev === 'crit' ? HOLD_MS + 2000
              : sev === 'warn' ? HOLD_MS
              : HOLD_MS - 1000;

    if (_dismissTimer) clearTimeout(_dismissTimer);
    _dismissTimer = setTimeout(dismissCard, Math.max(2000, hold));

    setCrumb('firing', `fired · ${env.contract_name || env.contract_id || 'contract'}`);
  }

  function dismissCard() {
    card.dataset.exit = '1';
    setTimeout(() => {
      card.hidden = true;
      crumb.hidden = false;
      setCrumb(_lastConn === 'open' ? 'connected' : 'disconnected', _lastConnText);
    }, 240);
  }

  function formatValue(v) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'number') {
      if (Number.isInteger(v)) return String(v);
      return v.toFixed(3);
    }
    return String(v);
  }
  function truncate(s, n) {
    s = String(s);
    return s.length <= n ? s : s.slice(0, n - 1) + '…';
  }

  // ---------------------------------------------------------------------
  // Status crumb
  // ---------------------------------------------------------------------

  let _lastConn = 'disconnected';
  let _lastConnText = 'overlay · disconnected';

  function setCrumb(state, label) {
    _lastConn = state;
    _lastConnText = label || 'overlay';
    crumbDot.classList.remove('crumb-dot-connected', 'crumb-dot-disconnected', 'crumb-dot-firing');
    crumbDot.classList.add('crumb-dot-' + state);
    crumbLabel.textContent = label || 'overlay · ' + state;
  }

  // ---------------------------------------------------------------------
  // SSE wiring (with exponential backoff + visibility pause)
  // ---------------------------------------------------------------------

  let es = null;
  let retries = 0;

  function connect() {
    if (es) return;
    setCrumb('disconnected', 'overlay · connecting…');
    try {
      es = new EventSource(`${RELAY.replace(/\/$/, '')}/aurora/events`);
    } catch (e) {
      scheduleReconnect();
      return;
    }
    es.onopen = () => {
      retries = 0;
      setCrumb('connected', 'overlay · connected');
    };
    es.onerror = () => {
      if (!es || es.readyState === EventSource.CLOSED) {
        disconnect();
        scheduleReconnect();
      } else {
        setCrumb('disconnected', 'overlay · reconnecting…');
      }
    };
    es.addEventListener('fire', (raw) => {
      try {
        const env = JSON.parse(raw.data || '{}');
        showCard(env);
      } catch (e) {
        console.warn('aurora-overlay: bad fire payload', e);
      }
    });
    es.addEventListener('heartbeat', () => { /* keep-alive only */ });
  }
  function disconnect() {
    if (es) { try { es.close(); } catch (_) {} }
    es = null;
  }
  function scheduleReconnect() {
    const wait = Math.min(30_000, 1000 * Math.pow(2, retries++)) + Math.random() * 500;
    setTimeout(connect, wait);
  }

  // Pause when tab hidden so backgrounded OBS sources don't churn.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) disconnect();
    else if (!es) { retries = 0; connect(); }
  });

  // ---------------------------------------------------------------------
  // ?demo=1 — show a static card so the streamer can frame the overlay
  // before going live (with no relay running).
  // ---------------------------------------------------------------------
  if (DEMO) {
    setCrumb('connected', 'overlay · demo mode');
    setTimeout(() => showCard({
      contract_name: 'aurora-alarm',
      severity:      'crit',
      method:        'hampel-z',
      trigger_field: 'findings.crit_count',
      trigger_value: 4,
      row:           1247,
      z_score:       5.73,
      fabricated:    0,
      bundle_run_id: '20260522_134517__curry',
    }), 200);
    return;
  }

  connect();
})();
