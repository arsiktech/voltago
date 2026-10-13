'use strict';

const numberFormat = new Intl.NumberFormat('en');
const countryNames = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['en'], { type: 'region' }) : null;
const state = { payload: null, sources: [], countries: [], request: null };
const byId = id => document.getElementById(id);
const count = value => Number.isFinite(value) && value >= 0 ? numberFormat.format(value) : 'Not reported';
const text = value => typeof value === 'string' ? value : '';
const list = value => Array.isArray(value) ? value : [];
const entries = value => value && typeof value === 'object' && !Array.isArray(value) ? Object.entries(value) : [];
const statuses = {
  not_run: 'Not run', complete: 'Collected', completed: 'Collected', success: 'Collected', ok: 'Collected',
  partial: 'Partly collected', failed: 'Collection failed', error: 'Collection failed',
  needs_credentials: 'Needs API key', deferred: 'Deferred', skipped: 'Skipped', cached: 'Cached snapshot',
  'navigation-linkout': 'Navigation link', 'covered-via-primary': 'Other-source coverage',
  'official-api-needed': 'Official API needed', 'link-only-rights-blocked': 'Link only · permission needed',
  'removed-rights-blocked': 'Excluded · permission needed', 'implemented-direct-public-page': 'Public-page collector',
  'empty': 'No cached response', 'fresh': 'Fresh cached response', 'stale': 'Stale cached response',
};
const statusLabel = value => statuses[value] || text(value).replaceAll('_', ' ').replaceAll('-', ' ') || 'Not reported';

function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content !== undefined) node.textContent = content;
  if (className) node.className = className;
  return node;
}
function cell(content, { heading = false, numeric = false } = {}) {
  const node = element(heading ? 'th' : 'td', typeof content === 'string' ? content : undefined, numeric ? 'number' : undefined);
  if (heading) node.scope = 'row';
  if (content instanceof Node) node.append(content);
  return node;
}
function row(cells) { const node = element('tr'); node.append(...cells); return node; }
function badge(value) { return element('span', statusLabel(value), 'pill'); }
function description(main, detail) {
  const node = element('div'); node.append(element('span', main));
  if (detail) node.append(element('p', detail));
  return node;
}
function table(id, rows, columns, emptyMessage) {
  const body = byId(id); body.replaceChildren(...rows);
  if (!rows.length) { const note = cell(emptyMessage); note.colSpan = columns; note.className = 'empty'; body.append(row([note])); }
}
function sourceLink(name, rawUrl) {
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== 'https:' || url.username || url.password) return element('span', name);
    const link = element('a', name); link.href = url.href; link.rel = 'noopener noreferrer'; return link;
  } catch { return element('span', name); }
}
function countryName(code) {
  if (!/^[A-Z]{2}$/.test(code)) return code === 'UNKNOWN' ? 'Country not recorded' : code;
  try { return countryNames?.of(code) || code; } catch { return code; }
}
function timestamp(value) {
  const date = new Date(value);
  return value && !Number.isNaN(date.valueOf()) ? date.toLocaleString('en', { dateStyle: 'medium', timeStyle: 'short' }) : 'Not reported';
}
function filterTables() {
  const sourceQuery = byId('source-filter').value.trim().toLocaleLowerCase();
  const sourceRows = state.sources.filter(([name]) => name.toLocaleLowerCase().includes(sourceQuery));
  table('loaded-sources', sourceRows.map(([name, values]) => row([cell(name, { heading: true }), cell(count(values.total), { numeric: true }), cell(count(values.map_eligible), { numeric: true })])), 3, state.sources.length ? 'No sources match this search.' : 'The service has not reported a source breakdown.');
  byId('source-results').textContent = `${count(sourceRows.length)} of ${count(state.sources.length)} reported sources`;
  const query = byId('country-filter').value.trim().toLocaleLowerCase();
  const countries = state.countries.filter(([code]) => `${code} ${countryName(code)}`.toLocaleLowerCase().includes(query));
  table('countries', countries.map(([code, value]) => row([cell(countryName(code), { heading: true }), cell(code), cell(count(value), { numeric: true })])), 3, state.countries.length ? 'No countries match this search.' : 'The service has not reported country counts.');
  byId('country-results').textContent = `${count(countries.length)} of ${count(state.countries.length)} country entries`;
}

function render(payload) {
  if (!payload || typeof payload !== 'object' || !payload.source_health?.intelligence) throw new Error('Source inventory is not available from this service.');
  const health = payload.source_health;
  const intelligence = health.intelligence;
  const global = health.global_coverage || {};
  state.payload = payload;
  state.sources = entries(intelligence.by_source).filter(([, value]) => value && typeof value === 'object').sort((a, b) => (b[1].total || 0) - (a[1].total || 0));
  const runtimeCountries = entries(intelligence.by_country);
  const manifestCountries = entries(global.stats?.countries);
  state.countries = (manifestCountries.length ? manifestCountries : runtimeCountries).map(([code, value]) => [code.toUpperCase(), value]).sort((a, b) => (b[1] || 0) - (a[1] || 0));
  byId('record-count').textContent = count(intelligence.total_records);
  byId('country-count').textContent = state.countries.length ? count(state.countries.filter(([code, value]) => /^[A-Z]{2}$/.test(code) && value > 0).length) : 'Not reported';
  byId('map-count').textContent = count(intelligence.map_eligible_records);
  byId('dataset-date').textContent = `Dataset updated: ${timestamp(intelligence.generated_at)} · Inventory checked: ${timestamp(health.generated_at)}. Counts describe this service’s current data.`;
  if (manifestCountries.length) byId('country-explanation').textContent = 'Raw record counts from the loaded dataset’s collection manifest. These can differ from the deduplicated source inventory and do not measure completeness or unique physical sites.';
  else byId('country-explanation').textContent = 'Counts come from the loaded source inventory, not a worldwide completeness estimate.';

  const adapters = list(payload.global_adapters).filter(adapter => adapter && typeof adapter === 'object');
  table('adapters', adapters.map(adapter => row([
    cell(description(text(adapter.name) || text(adapter.id), text(adapter.scope)), { heading: true }),
    cell(description(statusLabel(adapter.status), adapter.implemented === true ? 'Adapter implemented' : 'Implementation not reported')),
    cell(count(adapter.records_added_by_global_adapter), { numeric: true }),
    cell(description(text(adapter.method), text(adapter.credential_requirement))),
  ])), 4, 'This service has not published a worldwide adapter inventory.');
  const manifests = (list(global.source_manifests).length ? global.source_manifests : list(payload.source_manifest)).filter(manifest => manifest && typeof manifest === 'object');
  byId('manifest-details').hidden = !manifests.length;
  table('manifests', manifests.map(manifest => row([
    cell(description(text(manifest.source), text(manifest.scope)), { heading: true }), cell(badge(manifest.status)),
    cell(count(manifest.accepted), { numeric: true }),
    cell(list(manifest.errors).map(error => typeof error === 'string' ? error : text(error?.message)).filter(Boolean).join(' · ') || 'No collection error reported'),
  ])), 4, 'No collection manifest reported.');
  const geography = entries(health.live_geography);
  table('live-sources', geography.map(([key, area]) => {
    const cached = health.live_cache?.[key] || {};
    const cacheStatus = cached.status || (cached.has_payload === false ? 'empty' : cached.stale === true ? 'stale' : cached.fresh === true ? 'fresh' : 'Not reported');
    return row([cell(key === 'mapareve' ? 'MapaREVE' : key === 'gaia' ? 'Gaia' : key, { heading: true }), cell(text(area)), cell(description(statusLabel(cacheStatus), cached.has_payload === true ? 'A cached response exists; individual station freshness still applies.' : 'No cached payload reported. This page does not fetch live stations.'))]);
  }), 3, 'This service has not reported live-source geography. Worldwide live coverage is not confirmed.');
  table('networks', list(payload.requested_source_strategy?.requested_apps).filter(app => app && typeof app === 'object').map(app => row([
    cell(sourceLink(text(app.name), app.source_url), { heading: true }), cell(badge(app.status)), cell(description(text(app.method), text(app.risk))),
  ])), 3, 'This service has not published network and app integration status.');
  filterTables();
  byId('inventory').hidden = false;
}

async function refresh() {
  state.request?.abort();
  const controller = new AbortController(); state.request = controller;
  const timeout = setTimeout(() => controller.abort(), 120000);
  byId('refresh').disabled = true;
  byId('error').hidden = true;
  byId('load-status').textContent = state.payload ? 'Refreshing the current inventory…' : 'Loading the current source inventory… Larger catalogues can take a moment.';
  try {
    const response = await fetch('/api/sources', { signal: controller.signal, cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error('Source inventory request failed.');
    render(await response.json());
    byId('load-status').textContent = 'Loaded from this Volta service. Collection results and live coverage are reported separately.';
  } catch {
    const note = state.payload ? 'Refresh failed. The tables below show the previous response from this visit; they may be out of date.' : 'Current coverage could not be loaded. No deployment counts or connection statuses are being assumed.';
    byId('error').textContent = `${note} Try Refresh data or inspect the source data link below.`;
    byId('error').hidden = false;
    byId('load-status').textContent = state.payload ? 'Previously loaded snapshot · refresh unavailable' : 'Coverage unavailable';
  } finally {
    clearTimeout(timeout); byId('refresh').disabled = false;
  }
}
byId('refresh').addEventListener('click', refresh);
byId('source-filter').addEventListener('input', filterTables);
byId('country-filter').addEventListener('input', filterTables);
refresh();
