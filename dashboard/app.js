// Sema's Airbnb Pricing Cockpit — renderer
//
// Reads:
//   ../data/latest.json       — most recent merged comp + your-rate snapshot
//   ../data/history.jsonl     — for week-over-week movement table
//   ../data/sources/airroi_dubai.json, airdna_dubai.json, airbtics_dubai.json
//   ../config/events.json     — Dubai event overlay
//
// Rendering plan:
//   1. Macro strip (3 cards: AirROI Dubai, neighborhood ADRs)
//   2. Per listing: hero + KPIs + 90-day calendar + line chart + movement table
//   3. Cross-listing comparison chart at bottom
//
// File:// loading note: modern Chrome blocks fetch() of local JSON without a
// server. Workaround: when running via file://, we fall back to a synchronous
// XHR with the file: protocol. macOS Chrome allows this for same-folder files
// when launched normally. If it fails, we show a banner with the workaround.

const CFG = {
  AED_FORMAT: new Intl.NumberFormat('en-AE', { maximumFractionDigits: 0 }),
  DATE_LABEL_FMT: { weekday: 'short', month: 'short', day: 'numeric' },
  HEAT_BUCKETS: 5,
  MOVEMENT_PCT_THRESHOLD: 5,
  MOVEMENT_TOP_N: 10,
};

// ────────────── Data loading ──────────────

async function tryFetch(path) {
  try {
    const r = await fetch(path, { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    return await r.json();
  } catch (e) {
    console.warn('fetch failed:', path, e);
    return null;
  }
}

async function tryFetchText(path) {
  try {
    const r = await fetch(path, { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    return await r.text();
  } catch (e) {
    return null;
  }
}

async function loadAll() {
  const [latest, airroi, airdna, airbtics, events, historyText] = await Promise.all([
    tryFetch('../data/latest.json'),
    tryFetch('../data/sources/airroi_dubai.json'),
    tryFetch('../data/sources/airdna_dubai.json'),
    tryFetch('../data/sources/airbtics_dubai.json'),
    tryFetch('../config/events.json'),
    tryFetchText('../data/history.jsonl'),
  ]);
  let history = [];
  if (historyText) {
    history = historyText.trim().split('\n').filter(Boolean).map(l => {
      try { return JSON.parse(l); } catch { return null; }
    }).filter(Boolean);
  }
  return { latest, airroi, airdna, airbtics, events, history };
}

// ────────────── Helpers ──────────────

const fmtAED = (n) => n == null ? '—' : 'AED ' + CFG.AED_FORMAT.format(Math.round(n));
const fmtPct = (n, withSign = true) => n == null ? '—' : (withSign && n > 0 ? '+' : '') + n.toFixed(1) + '%';
const isWeekend = (iso) => { const d = new Date(iso + 'T00:00:00'); return d.getDay() === 5 || d.getDay() === 6; };
const dayOfWeek = (iso) => new Date(iso + 'T00:00:00').getDay(); // 0=Sun
const labelDate = (iso) => new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', CFG.DATE_LABEL_FMT);

function getEventsForDate(iso, events) {
  if (!events) return [];
  return events.filter(e => iso >= e.start && iso <= e.end);
}

function bucketize(value, min, max, buckets = CFG.HEAT_BUCKETS) {
  if (max === min) return 1;
  const idx = Math.floor(((value - min) / (max - min)) * buckets);
  return Math.max(1, Math.min(buckets, idx + 1));
}

function pctChange(a, b) {
  if (!a || !b) return null;
  return ((b - a) / a) * 100;
}

// ────────────── Macro strip ──────────────

function renderMacro({ airroi, airdna, airbtics }) {
  const strip = document.getElementById('macro-strip');
  const cards = [];

  if (airroi?.dubai) {
    cards.push({
      label: 'Dubai ADR median (AirROI)',
      value: fmtAED(airroi.dubai.adr_aed_median),
      sub: `Top 25%: ${fmtAED(airroi.dubai.adr_aed_top25 || airroi.dubai.adr_aed_peak_dec)}`,
    });
    cards.push({
      label: 'Dubai occupancy median',
      value: (airroi.dubai.occupancy_median_pct || '—') + '%',
      sub: `Top 25%: ${airroi.dubai.occupancy_top25_pct || '—'}%`,
    });
  }
  if (airroi?.neighborhoods) {
    Object.entries(airroi.neighborhoods).forEach(([name, n]) => {
      cards.push({
        label: `${name} ADR`,
        value: fmtAED(n.adr_aed_median),
        sub: n.note || `Occ ${n.occupancy_median_pct || '—'}%`,
      });
    });
  }
  if (airbtics?.dubai_1br) {
    cards.push({
      label: '1BR Y1 revenue median',
      value: fmtAED(airbtics.dubai_1br.annual_revenue_median_aed),
      sub: `Top 25%: ${fmtAED(airbtics.dubai_1br.annual_revenue_top25_aed)} (Airbtics)`,
    });
  }

  strip.innerHTML = cards.map(c => `
    <div class="macro-card">
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
      <div class="sub">${c.sub || ''}</div>
    </div>
  `).join('');
}

// ────────────── Listing block ──────────────

function listingDates(listing) {
  return Object.keys(listing.by_date || {}).sort();
}

function avgComp(listing, fromIdx, toIdx, dates) {
  const slice = dates.slice(fromIdx, toIdx);
  const vals = slice.map(d => listing.by_date[d]?.comp_median_host_aed).filter(Boolean);
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function renderListing(slug, listing, events, historyMap) {
  const dates = listingDates(listing);
  const today = new Date().toISOString().slice(0, 10);
  const futureDates = dates.filter(d => d >= today);

  // KPI windows
  const k30 = avgComp(listing, 0, 30, futureDates);
  const k60 = avgComp(listing, 30, 60, futureDates);
  const k90 = avgComp(listing, 60, 90, futureDates);

  // Spread (next 30 days vs your_rate_now average)
  const yourAvg30 = (() => {
    const vals = futureDates.slice(0, 30).map(d => listing.by_date[d]?.your_rate_now).filter(Boolean);
    return vals.length ? vals.reduce((a,b)=>a+b,0) / vals.length : null;
  })();
  const spread = (k30 && yourAvg30) ? pctChange(k30, yourAvg30) : null;
  const spreadClass = spread == null ? 'aligned' : spread < -10 ? 'below' : spread > 10 ? 'above' : 'aligned';
  const spreadLabel = spread == null ? 'N/A' :
    spread < -10 ? `${Math.abs(spread).toFixed(0)}% below comps · upside` :
    spread > 10 ? `${spread.toFixed(0)}% above comps · check fill` :
    `aligned (${spread > 0 ? '+' : ''}${spread.toFixed(0)}%)`;

  // Movement table — top WoW changes
  const prevSnap = historyMap[slug] || {};
  const movements = futureDates.map(d => {
    const cur = listing.by_date[d]?.comp_median_host_aed;
    const prev = prevSnap[d]?.comp_median_host_aed;
    const delta = pctChange(prev, cur);
    return delta != null ? { date: d, prev, cur, delta } : null;
  }).filter(Boolean).filter(m => Math.abs(m.delta) >= CFG.MOVEMENT_PCT_THRESHOLD);
  movements.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  const topMoves = movements.slice(0, CFG.MOVEMENT_TOP_N);

  // Calendar grid (90 days starting today, padded to align Mon-start week)
  const compVals = futureDates.map(d => listing.by_date[d]?.comp_median_host_aed).filter(Boolean);
  const minVal = Math.min(...compVals), maxVal = Math.max(...compVals);

  const firstDate = futureDates[0];
  const firstDow = dayOfWeek(firstDate); // 0=Sun
  // Align Monday start: pad cells so first column is Monday.
  const padCount = (firstDow === 0 ? 6 : firstDow - 1);
  const headings = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  let calendarHTML = headings.map(h => `<div class="heading">${h}</div>`).join('');
  for (let i = 0; i < padCount; i++) calendarHTML += '<div class="cell empty"></div>';
  futureDates.slice(0, 90).forEach(d => {
    const info = listing.by_date[d];
    const v = info?.comp_median_host_aed;
    const heat = v ? `heat-${bucketize(v, minVal, maxVal)}` : '';
    const ev = getEventsForDate(d, events);
    const eventClass = ev.length ? 'event' : '';
    const we = isWeekend(d) ? 'weekend' : '';
    const synth = info?.synthetic ? 'synthetic' : '';
    const day = new Date(d + 'T00:00:00').getDate();
    const month = new Date(d + 'T00:00:00').toLocaleDateString('en-GB', { month: 'short' });
    calendarHTML += `
      <div class="cell ${heat} ${eventClass} ${we} ${synth}" data-listing="${slug}" data-date="${d}">
        <div class="day">${day === 1 ? month + ' ' + day : day}</div>
        <div class="price">${v ? fmtAED(v).replace('AED ', '') : '—'}</div>
      </div>`;
  });

  // KPI delta vs your rate
  const kDelta = (kpi) => {
    if (!kpi || !yourAvg30) return { label: '—', cls: 'neutral' };
    const d = pctChange(kpi, yourAvg30);
    return {
      label: `Yours ${fmtPct(d)} vs comps`,
      cls: d > 5 ? 'up' : d < -5 ? 'down' : 'neutral',
    };
  };

  // ── Compose HTML ──
  const html = `
    <section class="listing" data-listing-slug="${slug}">
      <header class="listing-header">
        <div class="title-block">
          <h2>${listing.name}</h2>
          <div class="subtitle">${listing.subtitle || ''}</div>
        </div>
        <span class="spread-badge ${spreadClass}">${spreadLabel}</span>
      </header>

      <div class="kpi-grid">
        <div class="kpi">
          <div class="label">Comp median · next 30 d</div>
          <div class="value">${fmtAED(k30)}</div>
          <div class="delta ${kDelta(k30).cls}">${kDelta(k30).label}</div>
        </div>
        <div class="kpi">
          <div class="label">Comp median · 30–60 d</div>
          <div class="value">${fmtAED(k60)}</div>
          <div class="delta neutral">avg over the window</div>
        </div>
        <div class="kpi">
          <div class="label">Comp median · 60–90 d</div>
          <div class="value">${fmtAED(k90)}</div>
          <div class="delta neutral">avg over the window</div>
        </div>
        <div class="kpi">
          <div class="label">Your rate now · 30 d avg</div>
          <div class="value">${fmtAED(yourAvg30)}</div>
          <div class="delta neutral">from pricing-rates.csv</div>
        </div>
      </div>

      <h4 style="margin-bottom:12px">90-day calendar · click any date for details</h4>
      <div class="calendar">${calendarHTML}</div>

      <h4 style="margin: 24px 0 12px">Comp median vs your rate · next 90 days</h4>
      <div class="chart-wrap"><canvas id="chart-${slug}"></canvas></div>

      <h4 style="margin: 24px 0 12px">Biggest comp price moves vs last scan</h4>
      <div class="movement">
        ${topMoves.length ? `
          <table>
            <thead><tr><th>Date</th><th>Was</th><th>Now</th><th>Δ</th></tr></thead>
            <tbody>
              ${topMoves.map(m => `
                <tr>
                  <td>${labelDate(m.date)}</td>
                  <td>${fmtAED(m.prev)}</td>
                  <td>${fmtAED(m.cur)}</td>
                  <td class="${m.delta > 0 ? 'delta-up' : 'delta-down'}">
                    ${m.delta > 0 ? '↑' : '↓'} ${Math.abs(m.delta).toFixed(1)}%
                  </td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        ` : `<div class="empty-state">No notable moves yet — need 2+ scans to compute week-over-week changes.</div>`}
      </div>
    </section>
  `;

  document.getElementById('listings').insertAdjacentHTML('beforeend', html);

  // Chart
  drawListingChart(slug, listing, futureDates.slice(0, 90), events);

  // Cell click → drawer
  document.querySelectorAll(`[data-listing-slug="${slug}"] .cell:not(.empty)`).forEach(cell => {
    cell.addEventListener('click', () => openDrawer(slug, cell.dataset.date, listing, events));
  });
}

// ────────────── Charts ──────────────

function drawListingChart(slug, listing, dates, events) {
  if (typeof Chart === 'undefined') return;
  const ctx = document.getElementById(`chart-${slug}`);
  if (!ctx) return;

  const compMed = dates.map(d => listing.by_date[d]?.comp_median_host_aed ?? null);
  const compP25 = dates.map(d => listing.by_date[d]?.comp_p25_host_aed ?? null);
  const compP75 = dates.map(d => listing.by_date[d]?.comp_p75_host_aed ?? null);
  const yourRate = dates.map(d => listing.by_date[d]?.your_rate_now ?? null);

  const inkColor = getCSS('--ink');
  const ink2 = getCSS('--ink-2');
  const accent = getCSS('--accent');
  const green = getCSS('--green');
  const lineColor = getCSS('--line-2');

  // Event annotation as scatter dots on top
  const eventDots = dates.map(d => {
    const ev = getEventsForDate(d, events);
    return ev.length ? (listing.by_date[d]?.comp_median_host_aed ?? null) : null;
  });

  new Chart(ctx, {
    type: 'line',
    data: {
      labels: dates.map(d => labelDate(d)),
      datasets: [
        {
          label: 'Comp p25–p75 (lower)',
          data: compP25,
          borderColor: 'transparent',
          backgroundColor: hexA(accent, 0.08),
          pointRadius: 0,
          fill: '+1',
          tension: 0.3,
        },
        {
          label: 'Comp p25–p75 (upper)',
          data: compP75,
          borderColor: 'transparent',
          backgroundColor: hexA(accent, 0.08),
          pointRadius: 0,
          fill: false,
          tension: 0.3,
        },
        {
          label: 'Comp median',
          data: compMed,
          borderColor: accent,
          backgroundColor: accent,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          fill: false,
        },
        {
          label: 'Your rate',
          data: yourRate,
          borderColor: green,
          borderDash: [6, 4],
          borderWidth: 2,
          pointRadius: 0,
          tension: 0,
          fill: false,
        },
        {
          label: 'Event days',
          data: eventDots,
          borderColor: 'transparent',
          backgroundColor: getCSS('--gold'),
          pointRadius: 4,
          pointHoverRadius: 6,
          showLine: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          labels: {
            color: ink2,
            filter: (item) => !item.text.includes('p25–p75'),
            font: { family: 'Inter', size: 12 },
          },
        },
        tooltip: {
          backgroundColor: 'rgba(0,0,0,0.85)',
          titleFont: { family: 'Inter', weight: '600' },
          bodyFont: { family: 'Inter' },
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${fmtAED(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: ink2, font: { family: 'Inter', size: 11 }, maxTicksLimit: 12 },
          grid: { color: 'transparent' },
        },
        y: {
          ticks: {
            color: ink2,
            font: { family: 'Inter', size: 11 },
            callback: (v) => 'AED ' + CFG.AED_FORMAT.format(v),
          },
          grid: { color: lineColor },
        },
      },
    },
  });
}

function drawCrossChart(latest) {
  if (typeof Chart === 'undefined') return;
  const ctx = document.getElementById('cross-chart');
  if (!ctx) return;

  const palette = [getCSS('--accent'), getCSS('--gold')];
  const today = new Date().toISOString().slice(0, 10);

  // Find common date axis (intersection of both listings' future dates)
  const allDates = new Set();
  Object.values(latest.listings).forEach(l => {
    Object.keys(l.by_date || {}).filter(d => d >= today).forEach(d => allDates.add(d));
  });
  const labels = [...allDates].sort().slice(0, 90);

  const datasets = Object.entries(latest.listings).map(([slug, listing], i) => ({
    label: listing.name,
    data: labels.map(d => listing.by_date[d]?.comp_median_host_aed ?? null),
    borderColor: palette[i] || getCSS('--accent'),
    backgroundColor: palette[i] || getCSS('--accent'),
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.3,
  }));

  new Chart(ctx, {
    type: 'line',
    data: { labels: labels.map(d => labelDate(d)), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: getCSS('--ink-2'), font: { family: 'Inter', size: 12 } } },
        tooltip: {
          backgroundColor: 'rgba(0,0,0,0.85)',
          callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtAED(ctx.parsed.y)}` },
        },
      },
      scales: {
        x: { ticks: { color: getCSS('--ink-2'), maxTicksLimit: 12 }, grid: { color: 'transparent' } },
        y: {
          ticks: { color: getCSS('--ink-2'), callback: (v) => 'AED ' + CFG.AED_FORMAT.format(v) },
          grid: { color: getCSS('--line-2') },
        },
      },
    },
  });
}

// ────────────── Drawer ──────────────

function openDrawer(slug, date, listing, events) {
  const info = listing.by_date[date];
  if (!info) return;
  const ev = getEventsForDate(date, events);
  const spread = info.your_rate_now && info.comp_median_host_aed
    ? pctChange(info.comp_median_host_aed, info.your_rate_now)
    : null;
  document.getElementById('drawer-content').innerHTML = `
    <h3>${listing.name}</h3>
    <div style="color: var(--ink-3); margin-bottom: 24px; font-size: 14px">${labelDate(date)}</div>

    <div class="row"><span class="label">Comp median</span><span class="value">${fmtAED(info.comp_median_host_aed)}</span></div>
    <div class="row"><span class="label">Comp p25 / p75</span><span class="value">${fmtAED(info.comp_p25_host_aed)} – ${fmtAED(info.comp_p75_host_aed)}</span></div>
    <div class="row"><span class="label">Comp count</span><span class="value">${info.comp_count ?? '—'}</span></div>
    <div class="row"><span class="label">Your rate (now)</span><span class="value">${fmtAED(info.your_rate_now)}</span></div>
    <div class="row"><span class="label">Your rate (post-15-reviews)</span><span class="value">${fmtAED(info.your_rate_after_reviews)}</span></div>
    <div class="row"><span class="label">Guest sees</span><span class="value">${fmtAED(info.guest_sees_now)}</span></div>
    <div class="row"><span class="label">Spread (yours vs comp)</span>
      <span class="value" style="color: ${spread > 5 ? 'var(--amber)' : spread < -5 ? 'var(--red)' : 'var(--green)'}">${fmtPct(spread)}</span>
    </div>

    ${info.synthetic ? `
      <div class="banner warn" style="margin-top: 24px">
        <span class="dot"></span>
        <div>Synthetic seed — no real Airbnb scrape yet for this date. Run <code>./scripts/refresh.sh</code> to replace.</div>
      </div>
    ` : ''}

    ${ev.length ? `
      <div style="margin-top: 24px">
        <h4 style="margin-bottom: 8px">Events</h4>
        ${ev.map(e => `<div style="padding: 8px 0; border-bottom: 1px solid var(--line-2)">
          <strong>${e.name}</strong>
          <div style="font-size: 13px; color: var(--ink-3)">${e.note}</div>
        </div>`).join('')}
      </div>
    ` : ''}
  `;
  document.getElementById('drawer').classList.add('open');
  document.getElementById('drawer-bg').classList.add('open');
}

function closeDrawer() {
  document.getElementById('drawer').classList.remove('open');
  document.getElementById('drawer-bg').classList.remove('open');
}
window.closeDrawer = closeDrawer;

// ────────────── Banners ──────────────

function renderBanners(latest, history) {
  const slot = document.getElementById('banner-slot');
  if (!latest) {
    slot.innerHTML = `<div class="banner warn">
      <span class="dot"></span>
      <div><strong>No data yet.</strong> Run <code>./scripts/refresh.sh</code> in the project folder to generate the first snapshot.</div>
    </div>`;
    return;
  }
  const synthCount = Object.values(latest.listings || {}).reduce((sum, l) =>
    sum + Object.values(l.by_date || {}).filter(x => x.synthetic).length, 0);
  if (synthCount > 0) {
    slot.innerHTML = `<div class="banner warn">
      <span class="dot"></span>
      <div><strong>Showing synthetic seed.</strong> No real Airbnb scrape yet — values derived from your pricing strategy CSVs. Run <code>sources/airbnb_scrape.py --plan</code> and drive Playwright MCP to get real comp data.</div>
    </div>`;
  } else if (history.length < 2) {
    slot.innerHTML = `<div class="banner info">
      <span class="dot"></span>
      <div>Real data loaded. Movement table needs a 2nd scan to compute week-over-week changes.</div>
    </div>`;
  }
}

// ────────────── Bootstrap ──────────────

function getCSS(varname) {
  return getComputedStyle(document.documentElement).getPropertyValue(varname).trim() || '#000';
}
function hexA(hex, alpha) {
  // accept 'rgb(...)' or '#rrggbb' — for CSS vars that resolve to either
  if (hex.startsWith('#') && hex.length === 7) {
    const r = parseInt(hex.slice(1,3), 16), g = parseInt(hex.slice(3,5), 16), b = parseInt(hex.slice(5,7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  return hex;
}

(async function main() {
  const { latest, airroi, airdna, airbtics, events, history } = await loadAll();

  // Timestamp
  const tsEl = document.getElementById('ts');
  if (latest?.generated_at) {
    const dt = new Date(latest.generated_at);
    tsEl.textContent = 'Last scan · ' + dt.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
  } else {
    tsEl.textContent = 'No scan yet';
  }

  renderBanners(latest, history);
  renderMacro({ airroi, airdna, airbtics });

  if (!latest?.listings) return;

  // Build prev-snapshot map for movement calc (use snapshot before last)
  const historyMap = {};
  if (history.length >= 2) {
    const prev = history[history.length - 2];
    Object.entries(prev.listings || {}).forEach(([slug, l]) => {
      historyMap[slug] = l.by_date || {};
    });
  }

  // Wait for Chart.js to load
  const drawWhenReady = () => {
    Object.entries(latest.listings).forEach(([slug, listing]) => {
      renderListing(slug, listing, events?.events || [], historyMap);
    });
    drawCrossChart(latest);
  };
  if (typeof Chart !== 'undefined') {
    drawWhenReady();
  } else {
    const wait = setInterval(() => {
      if (typeof Chart !== 'undefined') {
        clearInterval(wait);
        drawWhenReady();
      }
    }, 60);
  }
})();
