const BASE = 'https://www.franimo.nl';
// The static export ships packed JSON files instead of the local API.
const STATIC = !!window.FRANIMO_STATIC;
const $ = s => document.querySelector(s);
const el = (t, c) => { const e = document.createElement(t); if (c) e.className = c; return e; };

let ALL = [], META = {}, VIEW = 'table', map = null, layer = null, PHOTOS = null;

function unpack(p) {
  if (!p.cols) return p;                       // already plain (local API)
  const { cols, dict, rows, imgPrefixes, urlPrefix } = p;
  const today = Date.now();
  return rows.map(vals => {
    const r = {};
    cols.forEach((c, i) => {
      const v = vals[i];
      r[c] = (dict[c] && typeof v === 'number') ? dict[c][v] : v;
    });
    if (Array.isArray(r.thumb)) r.thumb = imgPrefixes[r.thumb[0]] + r.thumb[1];
    if (r.url && urlPrefix && !/^https?:\/\//.test(r.url)) r.url = urlPrefix + r.url;
    if (!r.source) r.source = 'franimo';
    if (!r.currency) r.currency = 'EUR';
    // Derived fields are computed here rather than shipped, and days_known
    // stays correct as the export ages.
    r.eur_m2 = r.living_m2 > 0 ? r.price / r.living_m2 : null;
    r.eur_m2_land = r.land_m2 > 0 ? r.price / r.land_m2 : null;
    const first = r.first_price;
    r.price_drop = (r.old_price && r.price && r.old_price > r.price) ? r.old_price - r.price
                 : (first && r.price && first > r.price) ? first - r.price : null;
    r.days_known = r.first_seen
      ? Math.floor((today - Date.parse(r.first_seen)) / 86400000) : null;
    return r;
  });
}
let sort = { key: 'eur_m2', dir: 1 };
let limit = 300;            // rows painted at once; the rest is one click away

const eur = n => n == null ? '' : '€' + n.toLocaleString('nl-NL');
const num = n => n == null ? '' : Math.round(n).toLocaleString('nl-NL');
const img = u => !u ? '' : (u.startsWith('http') ? u : BASE + u);
const median = a => { const s = a.filter(x => x != null).sort((x, y) => x - y);
  return s.length ? s[Math.floor(s.length / 2)] : null; };

// Portal keys stay stable in the data; the UI shows a short name. Unknown
// keys (a future adapter, a typo) get underscores turned into words.
const SOURCE_NAMES = {
  franimo: 'Franimo',
  ok_bulgaria: 'OK Bulgaria',
  akiyaportal: 'Akiya Portal',
  holprop: 'Holprop',
  abruzzopropertyitaly: 'Abruzzo Property Italy',
  abruzzoruralproperty: 'Abruzzo Rural',
  centrarium: 'Centrarium',
  mubawab: 'Mubawab',
  homege: 'home.ge',
  bulgarianproperties: 'Bulgarian Properties',
  domaza: 'Domaza',
  lefigaro: 'Le Figaro',
  greenacres: 'Green-Acres',
};
const SOURCE_ORDER = Object.keys(SOURCE_NAMES);

function prettySource(key) {
  if (!key || key === '—') return key || '';
  if (SOURCE_NAMES[key]) return SOURCE_NAMES[key];
  return String(key).replace(/[_-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// Country is optional and only shown when the listing already has it
// (a `country` column, or raw_fields.country on a full drawer payload).
function countryOf(r) {
  if (r.country) return r.country;
  const raw = r.raw_fields;
  return (raw && raw.country) || '';
}

function locHint(r) {
  return r.dept_nl || r.region || countryOf(r) || '';
}

function sourceBadge(r) {
  if (!r.source) return '';
  return `<span class="src" title="${r.source}">${prettySource(r.source)}</span>`;
}

// ---------- filter state ----------
const chosen = { source: new Set(), searches: new Set(), type: new Set(),
                 region: new Set(), dept_nl: new Set(), energy_label: new Set() };
// `searches` is comma-joined (a listing can sit in several areas), so it needs
// set-intersection rather than the plain equality the other facets use.
const MULTI = new Set(['searches']);
const values = (r, k) => MULTI.has(k) ? String(r[k] || '').split(',').filter(Boolean)
                                      : [r[k] || '—'];

function passes(r, skip) {
  if (terms.length) {
    // Built once per row and cached: rebuilding it per keystroke across 15k
    // listings was the whole cost of typing in the search box.
    if (r._hay === undefined) {
        r._hay = [r.place, r.type, r.dept_nl, r.region, r.description, r.features,
                  r.agent, r.reference, r.snippet, r.source, prettySource(r.source),
                  countryOf(r)].filter(Boolean).join(' ').toLowerCase();
    }
    if (!terms.every(w => r._hay.includes(w))) return false;
  }
  const n = id => { const v = parseFloat($(id).value); return isNaN(v) ? null : v; };
  const pmin = n('#pmin'), pmax = n('#pmax'), em2 = n('#em2max'),
        bed = n('#bedmin'), liv = n('#livmin'), land = n('#landmin');
  if (pmin != null && (r.price == null || r.price < pmin)) return false;
  if (pmax != null && (r.price == null || r.price > pmax)) return false;
  if (em2 != null && (r.eur_m2 == null || r.eur_m2 > em2)) return false;
  if (bed != null && (r.bedrooms == null || r.bedrooms < bed)) return false;
  if (liv != null && (r.living_m2 == null || r.living_m2 < liv)) return false;
  if (land != null && (r.land_m2 == null || r.land_m2 < land)) return false;
  if ($('#onlydrop').checked && !r.price_drop) return false;
  if ($('#onlynew').checked && !(r.days_known <= (parseInt($('#newdays').value) || 7))) return false;
  if ($('#hidegone').checked && r.gone_at) return false;
  if ($('#needm2').checked && !r.living_m2) return false;
  for (const k of Object.keys(chosen)) {
    if (k === skip) continue;                      // facet counts ignore their own filter
    if (chosen[k].size && !values(r, k).some(v => chosen[k].has(v))) return false;
  }
  return true;
}

// Filter state is read from the DOM once per render, not once per row: the
// predicate runs ~6x over every listing (the list plus each facet's counts),
// so anything done per row has to be pure arithmetic on cached values.
let F = null;

function readFilters() {
  const n = id => { const v = parseFloat($(id).value); return isNaN(v) ? null : v; };
  const terms = $('#q').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  F = {
    terms,
    pmin: n('#pmin'), pmax: n('#pmax'), em2: n('#em2max'),
    bed: n('#bedmin'), liv: n('#livmin'), land: n('#landmin'),
    onlydrop: $('#onlydrop').checked,
    onlynew: $('#onlynew').checked,
    newdays: parseInt($('#newdays').value) || 7,
    hidegone: $('#hidegone').checked,
    needm2: $('#needm2').checked,
  };
  // One text pass per render instead of one per row per facet.
  if (terms.length) {
    for (const r of ALL) {
      if (r._hay === undefined) {
        r._hay = [r.place, r.type, r.dept_nl, r.region, r.description, r.features,
                  r.agent, r.reference, r.snippet, r.source, prettySource(r.source),
                  countryOf(r)].filter(Boolean).join(' ').toLowerCase();
      }
      r._text = terms.every(w => r._hay.includes(w));
    }
  }
}

function passes(r, skip) {
  if (F.terms.length && !r._text) return false;
  if (F.pmin != null && (r.price == null || r.price < F.pmin)) return false;
  if (F.pmax != null && (r.price == null || r.price > F.pmax)) return false;
  if (F.em2 != null && (r.eur_m2 == null || r.eur_m2 > F.em2)) return false;
  if (F.bed != null && (r.bedrooms == null || r.bedrooms < F.bed)) return false;
  if (F.liv != null && (r.living_m2 == null || r.living_m2 < F.liv)) return false;
  if (F.land != null && (r.land_m2 == null || r.land_m2 < F.land)) return false;
  if (F.onlydrop && !r.price_drop) return false;
  if (F.onlynew && !(r.days_known <= F.newdays)) return false;
  if (F.hidegone && r.gone_at) return false;
  if (F.needm2 && !r.living_m2) return false;
  for (const k of Object.keys(chosen)) {
    if (k === skip) continue;                      // facet counts ignore their own filter
    if (chosen[k].size && !values(r, k).some(v => chosen[k].has(v))) return false;
  }
  return true;
}

const filtered = () => ALL.filter(r => passes(r));

// ---------- facets ----------
function facet(key, title, host, limit, labelOf) {
  const pool = ALL.filter(r => passes(r, key));
  const counts = new Map();
  pool.forEach(r => values(r, key).forEach(v => counts.set(v, (counts.get(v) || 0) + 1)));
  let items = [...counts].sort((a, b) => b[1] - a[1]);
  if (key === 'source') {
    items.sort((a, b) => {
      const ia = SOURCE_ORDER.indexOf(a[0]), ib = SOURCE_ORDER.indexOf(b[0]);
      const da = ia === -1 ? 999 : ia, db = ib === -1 ? 999 : ib;
      if (da !== db) return da - db;
      return prettySource(a[0]).localeCompare(prettySource(b[0]), 'nl');
    });
  }
  host.innerHTML = '';
  const h = el('h4');
  h.textContent = items.length > 1 ? `${title} · ${items.length}` : title;
  host.append(h);
  const show = host.dataset.expanded === '1' ? items.length : (limit || 8);
  items.slice(0, show).forEach(([v, c]) => {
    const l = el('label'), cb = el('input');
    cb.type = 'checkbox'; cb.checked = chosen[key].has(v);
    cb.onchange = () => { cb.checked ? chosen[key].add(v) : chosen[key].delete(v); render(); };
    const label = labelOf ? labelOf(v) : v;
    const s = el('span'); s.textContent = label;
    if (labelOf && label !== v) s.title = v;
    const b = el('b'); b.textContent = c;
    l.append(cb, s, b); host.append(l);
  });
  if (items.length > show) {
    const m = el('button', 'more'); m.textContent = `+ ${items.length - show} meer`;
    m.onclick = () => { host.dataset.expanded = '1'; render(); };
    host.append(m);
  }
}

// ---------- table ----------
const COLS = [
  { k: 'thumb', t: '', cls: 'thumb', cell: r => r.thumb ? `<img loading="lazy" src="${img(r.thumb)}">` : '' },
  { k: 'type', t: 'type', cell: r => `${r.type || ''} ${sourceBadge(r)}` },
  { k: 'place', t: 'plaats', cls: 'place', cell: r => `${r.place || ''} <span class="tag">${locHint(r)}</span>` },
  { k: 'price', t: 'prijs', cls: 'num', cell: r => eur(r.price) +
      (r.price_drop ? ` <span class="drop">▼${num(r.price_drop)}</span>` : '') },
  { k: 'eur_m2', t: '€/m²', cls: 'num', cell: r => num(r.eur_m2) },
  { k: 'living_m2', t: 'woning', cls: 'num', cell: r => num(r.living_m2) },
  { k: 'land_m2', t: 'terrein', cls: 'num', cell: r => num(r.land_m2) },
  { k: 'eur_m2_land', t: '€/m² grond', cls: 'num', cell: r => r.eur_m2_land ? r.eur_m2_land.toFixed(1) : '' },
  { k: 'bedrooms', t: 'slaapk', cls: 'num' },
  { k: 'rooms', t: 'kamers', cls: 'num' },
  { k: 'energy_label', t: 'epc' },
  { k: 'days_known', t: 'gezien', cls: 'num', cell: r =>
      r.days_known <= 7 ? `<span class="new">${r.days_known}d</span>` : `${r.days_known}d` },
];

function renderTable(rows) {
  const host = $('#table-view');
  const shown = rows.slice(0, limit);

  const head = COLS.map(c => {
    const arrow = sort.key === c.k ? (sort.dir > 0 ? ' ▲' : ' ▼') : '';
    return `<th class="${sort.key === c.k ? 'sorted' : ''}" data-k="${c.k}">${c.t}${arrow}</th>`;
  }).join('');

  // One string, one parse. Assigning innerHTML per cell across 300 rows x 12
  // columns was costing more than the filtering itself.
  const body = shown.map(r => '<tr' + (r.gone_at ? ' class="gone"' : '') +
    ` data-id="${r.id}">` + COLS.map(c => {
      const v = c.cell ? c.cell(r) : (r[c.k] ?? '');
      return `<td class="${c.cls || ''}">${v == null ? '' : v}</td>`;
    }).join('') + '</tr>').join('');

  host.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;

  host.querySelector('thead').onclick = e => {
    const th = e.target.closest('th');
    if (!th) return;
    const k = th.dataset.k;
    if (sort.key === k) sort.dir *= -1; else { sort.key = k; sort.dir = 1; }
    render();
  };
  host.querySelector('tbody').onclick = e => {
    const tr = e.target.closest('tr');
    if (tr) openDrawer(ALL.find(r => r.id === +tr.dataset.id));
  };
  host.append(moreButton(rows.length));
}

function moreButton(total) {
  const wrap = el('div', 'more-wrap');
  if (total <= limit) {
    wrap.textContent = `${total} rijen`;
    return wrap;
  }
  const b = el('button', 'reset');
  b.textContent = `toon nog ${Math.min(500, total - limit)} van ${total - limit} resterend`;
  b.onclick = () => { limit += 500; render(); };
  wrap.append(b);
  return wrap;
}

function renderGrid(rows) {
  const host = $('#grid-view');
  host.innerHTML = '';
  const g = el('div', 'cards');
  g.innerHTML = rows.slice(0, limit).map(r =>
    `<div class="card${r.gone_at ? ' gone' : ''}" data-id="${r.id}">
      ${r.thumb ? `<img loading="lazy" src="${img(r.thumb)}">` : ''}
      <div class="c"><h3>${r.type || ''} ${r.place || ''}</h3>
      <div class="meta"><span class="price">${eur(r.price)}</span>
        ${sourceBadge(r)}
        ${r.eur_m2 ? `<span>${num(r.eur_m2)} €/m²</span>` : ''}
        ${r.living_m2 ? `<span>${num(r.living_m2)} m²</span>` : ''}
        ${r.land_m2 ? `<span>${num(r.land_m2)} m² grond</span>` : ''}
        ${locHint(r) ? `<span class="geo">${locHint(r)}</span>` : ''}</div></div>
    </div>`).join('');
  g.onclick = e => {
    const c = e.target.closest('.card');
    if (c) openDrawer(ALL.find(r => r.id === +c.dataset.id));
  };
  host.append(g);
  host.append(moreButton(rows.length));
}

function renderMap(rows) {
  if (!map) {
    map = L.map('map', { preferCanvas: true }).setView([46.8, 2.6], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      { attribution: '© OpenStreetMap', maxZoom: 18 }).addTo(map);
  }
  if (layer) layer.remove();
  layer = L.layerGroup().addTo(map);
  const pts = [];
  rows.forEach(r => {
    if (r.lat == null || r.lon == null) return;
    pts.push([r.lat, r.lon]);
    const m = L.circleMarker([r.lat, r.lon], {
      radius: 6, weight: 1, color: '#fff', fillColor: r.price_drop ? '#6fb07a' : '#d98b3a',
      fillOpacity: .9,
    }).addTo(layer);
    m.bindPopup(`<div class="pop">
      ${r.thumb ? `<img src="${img(r.thumb)}" alt="">` : ''}
      <b>${r.type || ''} ${r.place || ''}</b>
      <span class="sub">${sourceBadge(r)}${locHint(r) ? ' · ' + locHint(r) : ''}</span>
      <span class="p">${eur(r.price)}${r.eur_m2 ? ` · ${num(r.eur_m2)} €/m²` : ''}</span>
      ${r.living_m2 || r.land_m2 ? `<span class="sub">${r.living_m2 ? r.living_m2 + ' m² woning' : ''}${r.living_m2 && r.land_m2 ? ' · ' : ''}${r.land_m2 ? num(r.land_m2) + ' m² terrein' : ''}</span>` : ''}
      <a href="#" onclick="window.__open(${r.id});return false">details →</a>
    </div>`, { minWidth: 210 });
  });
  if (pts.length) map.fitBounds(pts, { padding: [30, 30], maxZoom: 10 });
  setTimeout(() => map.invalidateSize(), 50);
}

// ---------- locator map ----------

// "Ardeche" vs "Ardèche", "Paris (Seine)" vs "Paris": match on a folded name.
const fold = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .replace(/\s*\(.*\)\s*/g, '').trim().toLowerCase();

let DEPT_BY_NAME = null;
function deptPath(name) {
  if (!window.FRANCE) return null;
  if (!DEPT_BY_NAME) {
    DEPT_BY_NAME = {};
    for (const [n, d] of Object.entries(FRANCE.depts)) DEPT_BY_NAME[fold(n)] = d;
  }
  return DEPT_BY_NAME[fold(name)] || null;
}

function locator(r) {
  if (!window.FRANCE) return '';
  const { w, h, proj, depts } = FRANCE;
  const all = Object.values(depts).join(' ');
  const mine = deptPath(r.dept_fr) || deptPath(r.dept_nl);

  let dot = '';
  const inFrame = r.lat != null && r.lon != null
    && r.lon * proj.kx >= proj.minx && r.lon * proj.kx <= proj.maxx
    && r.lat >= proj.miny && r.lat <= proj.maxy;
  if (inFrame) {
    const x = (r.lon * proj.kx - proj.minx) / (proj.maxx - proj.minx) * w;
    const y = (proj.maxy - r.lat) / (proj.maxy - proj.miny) * h;
    dot = `<circle class="pin-halo" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="26"/>
           <circle class="pin" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="11"/>`;
  }

  return `<figure class="locator">
    <svg viewBox="0 0 ${w} ${h}" role="img"
         aria-label="ligging in Frankrijk: ${r.place || ''} ${r.dept_fr || ''}">
      <path class="land" d="${all}"/>
      ${mine ? `<path class="dept" d="${mine}"/>` : ''}
      ${dot}
    </svg>
    <figcaption>${r.dept_fr || r.dept_nl || ''}${inFrame ? '' :
      ' — buiten het Franse vasteland'}</figcaption>
  </figure>`;
}

// ---------- drawer ----------
window.__open = id => openDrawer(ALL.find(r => r.id === id));

async function ensurePhotos(r) {
  if (r.photos !== undefined) return;
  if (!STATIC) {
    r.photos = null;
    try { Object.assign(r, await fetch('/api/listing/' + r.id).then(x => x.json())); }
    catch (e) { /* drawer still works without photos */ }
    return;
  }
  if (!PHOTOS) {
    try {
      const p = await fetch('data/photos.json').then(x => x.json());
      PHOTOS = {};
      for (const [id, [head, names]] of Object.entries(p.photos)) {
        // head === null means the listing's photos span several directories,
        // so the names are already whole URLs.
        PHOTOS[id] = head === null ? names
          : names.map(n => p.prefixes[head] + (n[0] === '=' ? n.slice(1) : n + '.jpg'));
      }
    } catch (e) { PHOTOS = {}; }
  }
  r.photos = PHOTOS[r.id] || [];
}

async function openDrawer(r) {
  if (!r) return;
  await ensurePhotos(r);
  const fig = (v, label) => v == null ? '' :
    `<div class="fig"><b>${v}</b><span>${label}</span></div>`;
  $('#drawer-body').innerHTML = `
    <h2>${r.type || ''} ${r.place || ''}</h2>
    <div class="sub">${[
        sourceBadge(r),
        countryOf(r),
        r.dept_nl,
        r.region && r.region !== r.dept_nl && r.region !== countryOf(r) ? r.region : '',
        r.reference ? 'ref ' + r.reference : '',
        '#' + r.id,
        r.gone_at ? `<b>niet meer op ${prettySource(r.source) || 'de bron'}</b>` : '',
      ].filter(Boolean).join(' · ')}</div>
    <div class="figs">
      ${fig(eur(r.price), 'prijs')}
      ${fig(r.eur_m2 ? num(r.eur_m2) : null, '€/m²')}
      ${fig(r.living_m2 ? num(r.living_m2) : null, 'woning m²')}
      ${fig(r.land_m2 ? num(r.land_m2) : null, 'terrein m²')}
      ${fig(r.bedrooms, 'slaapkamers')}
      ${fig(r.rooms, 'kamers')}
      ${fig(r.energy_label, 'epc')}
      ${fig(r.days_known != null ? r.days_known + 'd' : null, 'in db')}
    </div>
    ${r.price_drop ? `<p class="drop">prijs verlaagd met ${eur(r.price_drop)}
       ${r.first_price ? `(was ${eur(r.old_price || r.first_price)})` : ''}</p>` : ''}
    ${locator(r)}
    <a class="open" href="${r.url}" target="_blank" rel="noopener">open op ${prettySource(r.source) || 'bron'} ↗</a>
    ${r.features ? `<div class="feat">${r.features}</div>` : ''}
    <div class="desc">${(r.description || r.snippet || '').replace(/</g, '&lt;')}</div>
    <div class="shots">${(r.photos || []).map(p =>
      `<a href="${img(p)}" target="_blank"><img loading="lazy" src="${img(p)}"></a>`).join('')}</div>
    ${r.agent ? `<p class="hint">aanbieder: ${r.agent}${r.agent_name ? ' — ' + r.agent_name : ''}<br>
      ${r.agent_address || ''}</p>` : ''}`;
  $('#drawer').classList.add('open');
}

// ---------- render ----------
function render() {
  readFilters();
  const rows = filtered();
  const key = sort.key;
  rows.sort((a, b) => {
    let x = a[key], y = b[key];
    const xn = x == null || x === '', yn = y == null || y === '';
    if (xn && yn) return 0;
    if (xn) return 1;                       // blanks always last
    if (yn) return -1;
    if (typeof x === 'string') return sort.dir * x.localeCompare(y, 'nl');
    return sort.dir * (x - y);
  });

  const mp = median(rows.map(r => r.price)), mm = median(rows.map(r => r.eur_m2));
  const nodetail = rows.filter(r => !r.detail_fetched).length;
  $('#summary').innerHTML = `<b>${rows.length}</b> van ${ALL.length} woningen ·
    mediaan <b>${eur(mp)}</b>${mm ? ` · <b>${num(mm)}</b> €/m²` : ''} ·
    <b>${rows.filter(r => r.price_drop).length}</b> verlaagd` +
    (nodetail ? ` · <span title="detailpagina nog niet opgehaald">${nodetail} zonder m²</span>` : '');

  facet('source', 'bron', $('#facet-source'), 20, prettySource);
  facet('searches', 'gebied', $('#facet-area'));
  facet('type', 'type', $('#facet-type'));
  facet('region', 'regio', $('#facet-region'));
  facet('dept_nl', 'departement', $('#facet-dept'));
  facet('energy_label', 'energielabel', $('#facet-energy'), 8);

  if (VIEW === 'table') renderTable(rows);
  if (VIEW === 'grid') renderGrid(rows);
  if (VIEW === 'map') renderMap(rows);
}

// ---------- wiring ----------
let typing = null;
document.querySelectorAll('#filters input').forEach(i =>
  i.addEventListener('input', () => {
    limit = 300;
    if (i.id === 'q') {
      clearTimeout(typing);
      typing = setTimeout(render, 140);
    } else {
      render();
    }
  }));
document.querySelectorAll('.views button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.views button').forEach(x => x.classList.toggle('on', x === b));
  VIEW = b.dataset.view;
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('on', v.id === VIEW + '-view'));
  render();
});
$('#reset').onclick = () => {
  document.querySelectorAll('#filters input').forEach(i => {
    if (i.type === 'checkbox') i.checked = (i.id === 'hidegone');
    else if (i.id !== 'newdays') i.value = '';
  });
  Object.values(chosen).forEach(s => s.clear());
  document.querySelectorAll('.facet').forEach(f => f.dataset.expanded = '0');
  render();
};
$('#drawer-close').onclick = () => $('#drawer').classList.remove('open');

const syncHeaderHeight = () => document.documentElement.style.setProperty(
  '--header-h', document.querySelector('header').offsetHeight + 'px');
syncHeaderHeight();
addEventListener('resize', syncHeaderHeight);

const closeFilters = () => {
  document.body.classList.remove('filters-open');
  $('#backdrop').classList.remove('on');
};
$('#filters-toggle').onclick = () => {
  const open = document.body.classList.toggle('filters-open');
  $('#backdrop').classList.toggle('on', open);
};
$('#backdrop').onclick = closeFilters;
// On a phone the cards read far better than a 12-column table.
if (window.matchMedia('(max-width: 820px)').matches) {
  VIEW = 'grid';
  document.querySelectorAll('.views button').forEach(b =>
    b.classList.toggle('on', b.dataset.view === 'grid'));
  document.querySelectorAll('.view').forEach(v =>
    v.classList.toggle('on', v.id === 'grid-view'));
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { $('#drawer').classList.remove('open'); closeFilters(); }
});

Promise.all([
  fetch(STATIC ? 'data/listings.json' : '/api/listings').then(r => r.json()),
  fetch(STATIC ? 'data/meta.json' : '/api/meta').then(r => r.json()),
]).then(([rows, m]) => {
  ALL = unpack(rows); META = m;
  ALL.forEach(r => { if (!r.source) r.source = 'franimo'; if (!r.currency) r.currency = 'EUR'; });
  const last = m.runs && m.runs[0];
  $('#runinfo').textContent = last
    ? `laatste run ${last.finished_at?.slice(0, 16).replace('T', ' ')} — ${last.seen} gevonden, ${last.new} nieuw, ${last.gone} verdwenen`
    : '';
  render();
});
