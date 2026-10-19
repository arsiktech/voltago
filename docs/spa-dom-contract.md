# Volta SPA — DOM contract cheat-sheet (for QA scripts)

Selector/flow map of the React SPA (frontend/src), updated 2026-10-02 for the
discovery layout and adjustable charging-price filter. If a selector here drifts from the code, the code wins — update
this file in the same change.

## Shell / layout
- App mounts in `#root`. The map is a **backdrop enhancement**: `div[aria-hidden="true"]`
  wrapping the MapLibre canvas (`.maplibregl-canvas`). The list panel is the primary surface.
- Main results panel: `section.discovery-panel`, with an opaque theme surface.
  Mobile: a sheet starting at 42dvh below the map. Desktop (≥768px): a left column, with
  width controlled by `--discovery-width`. `.discovery-map` occupies its own adjacent
  canvas area; camera fitting never includes area hidden under the results.
- Header: `header.app-header`, brand home link `a[aria-label="Volta Go home"]`, decorative
  `img[src="/volta-icon.svg"]` with empty `alt`, and the theme button.
- Mobile list/map switch: `button.mobile-view-toggle`, named `Show full charger list` /
  `Show map and chargers`, sits in the sticky results heading before refresh. A decorative
  grabber (`.sheet-grabber-zone`, `aria-hidden`) at the top of the sheet does the same on
  tap or vertical drag (up for the list, down for the map), and a touch scroll of more than
  48px in the split layout raises the sheet to the list layout. Programmatic scrolls never
  change the layout. The initial layout stays split; switching views must retain query,
  filters and results. Desktop hides both.

## Header (inside the panel)
- The search block (`.discovery-search`) stays pinned. The "Find your next charge."
  headline and intro show only in overview mode and collapse once a place or location
  is chosen.
- `[data-result-scroller]` holds, in order: the sticky `.results-heading` (place label,
  `h1` count, refresh), `.discovery-status` (live status, coverage, guidance), the
  `.results-body` (list or empty/error state). Status notes
  scroll away with the results; the heading stays pinned and shows a hairline
  (`data-scrolled`) once content scrolls under it.
- `h1` — `"<N> public chargers"`, `"<N> free chargers"`, or
  `"<N> chargers ≤ €0.NN/kWh"` in national overview mode; `"<N> nearby"` in live mode.
  The count is in a `span.tabular-nums`.
- Mode label (`[data-search-area]`): `Explore Spain` | `Near you` | `Near <place>`.
- Icon buttons (all `button[aria-label]`): `Use my location`, `Refresh known charger leads` (overview) / `Refresh live data` (nearby).
  Theme controls live in the app header: `Switch to dark mode` / `Switch to light mode`.

## Your EV

- Header `button.ev-header-control`, visible name beginning `Your EV`, opens the
  nonmodal `aside#vehicle-panel[role="dialog"][aria-labelledby="vehicle-panel-title"]`.
  Station search and details remain usable behind the desktop drawer. `Close your EV`
  or Escape closes it and restores focus to the header control.
- Empty state offers `Connect your car` when live linking is configured,
  `Enter manually` and an explicitly labeled `Explore an example`. Missing live
  setup never produces fake connected data; manual entries persist in this browser,
  and an example does not. `Add your EV` converts the example into a blank manual form.
- Manual form labels: `Car name`, `Battery (%)`, `Range (km)`, `Usable battery capacity
  (kWh)`, `Max AC (kW)`, `Max DC (kW)`; connector buttons expose `aria-pressed`.
  `Save readings` returns to the battery view. `Edit readings` and connection actions
  stay pinned while readings scroll. `Remove this EV` removes manual data and photo.
- Native range input `#vehicle-charge-target`, accessible name `Charge target`,
  accepts 50–100 in five-point steps and updates energy immediately. It is a planning
  target, not a vehicle command. The battery bar is a named `role="meter"`.
- `Change car photo` opens a local file input (JPG/PNG/WebP, up to 1.5 MB).
  Photos remain in this browser; `Remove car photo` restores the labeled illustration.
- Provider telemetry is fetched without storage caching, refreshed at most once per
  minute while visible, and marked stale by its observation deadline. Provider test
  mode is labeled `Simulated readings`. `Refresh car readings` retries an update.
  `Disconnect` removes this browser's server session; OEM grant revocation remains
  in the provider's account settings.
- A station's `Source details for <name>` disclosure includes
  `section.ev-station-estimate` when a vehicle is selected. Estimates use the selected
  target, matched connector, car limits and verified current euro tariff; stale
  readings withhold time/cost. No estimate changes a station's decision pill.

## Search
- Input: `input[inputmode="search"]` with `aria-label="Enter home address or city"`,
  `role="combobox"`, `aria-autocomplete="list"`, `aria-expanded`, `aria-controls` (set to the
  dropdown id only while it is shown), `aria-activedescendant` (set to the active option id
  while an item is highlighted). `placeholder="Search a city or address"`.
- Clear: `button[aria-label="Clear search"]` (only when text present).
- Pressing `/` outside a text field or dialog focuses the search input.
- Geocode dropdown: `ul[role="listbox"][aria-label="Home area suggestions"]` containing
  `li[role="option"][aria-selected]` rows (each with a `MapPin` icon, place name, and label);
  appears while the input is focused, value ≥3 chars, and results/loading/error state exists.
  Non-option status rows (plain `li`, no `role="option"`) render `Searching…`,
  `Place search is unavailable. Try again.`, or `No matching home area found.` Arrow
  Up/Down move `aria-activedescendant` through the options, Enter picks the active (or first)
  result only while suggestions are open, and Escape dismisses without selecting.
  Further typing or Arrow Up/Down reopens suggestions. Picking a row flies the map and enters live mode.
  Changing the query clears prior-query suggestions immediately; pending results cannot
  send Enter to a previous city.

## Filters
- `div[role="group"][aria-label="Filters"]` has two visible quick price controls:
  `button.chip[aria-pressed]` named `Free`, and `button[data-price-filter]` named
  `Charging price` by default. The latter displays `Price` by default or the current
  numeric cap. A positive cap changes its accessible name to
  `Charging price: ≤ €0.NN/kWh`, including the visible text for voice control.
  `Free` is pressed exactly when `maxPrice === 0`; activating it toggles between zero
  and `null` (No limit).
- `All filters` and `Charging price` open the same native modal `dialog` named
  `All filters`. Both expose `aria-haspopup="dialog"`, `aria-expanded`, and `aria-controls`.
  Query controls behind the open dialog are inert.
- The Charging price group contains a labeled native `input[type="range"]` named
  `Maximum charging price`. It uses whole-cent values: `min="0"`, `max="101"`, `step="1"`.
  Values 0–100 map to €0.00–€1.00/kWh; 101 maps to No limit. `aria-valuetext`
  announces the actual charging price, or `No price limit` at the sentinel endpoint.
  Home selects Free; End selects No limit. Arrow keys change the cap by one cent.
  The `No limit` button has `aria-pressed="true"` only when the cap is `null`.
- The dialog has six `button.chip[aria-pressed][aria-labelledby]` boolean options, each
  explained by `aria-describedby`: `Open now`, `Source checked`, `Easy parking`,
  `Tesla OK`, `Fast`, and `Leads (Verify first)`.
  Use role/name locators: textContent also includes the description.
- Defaults: No limit and Leads on. The other five constraints are opt-in. Unknown
  charging prices cannot satisfy an active cap. An explicit reported-free claim can
  satisfy zero when no numeric tariff exists. Numeric tariffs take precedence over
  contradictory free flags. Charging-price choices do not include parking charges.
- Groups: `Charging price`, `The charger`, `Evidence & parking`. Client results and
  price labels update immediately. API requests debounce price movement for 250ms;
  `max_price` carries the selected euros to two decimals. No limit preserves the
  existing broad `max_price=5.0` API envelope. Query cache entries retain the numeric cap.
- Opening from Charging price focuses the range. `Done`, `Close filters`, or Escape
  closes the modal and returns focus to the actual opener. Tab and Shift+Tab stay
  inside the open dialog.
- Changed boolean constraints are counted on All filters, including Leads switched off.
  A numeric price cap counts as one changed filter. `Clear all filters` restores
  No limit and all six boolean defaults; it is disabled inside the dialog when unchanged.
- `scripts/qa_browser_harness.js` provides real `openFilters`, `openPriceFilters`,
  `setMaxPrice`, `closeFilters`, and `setFilter` interactions. `setMaxPrice` uses native
  keyboard events in integer cents, not synthetic application state changes.
- Boolean options retain size/position when toggled, have 44px minimum height, and support
  Space/Enter with retained focus. `scripts/qa_liquid_glass_ui.js` verifies modal focus,
  reduced-motion/transparency and forced-color fallbacks at mobile and desktop sizes.
  `scripts/qa_price_slider.js` verifies numeric caps, keyboard endpoints, separate query
  requests, cached-query revisits, unknown prices, honest stale rows, reset and both openers.

## Status row / banners
- Live status: `span.live-dot` + text `Live · updated …` / `Refreshing…`; count badge
  `.badge` with `"N live · M leads"`.
- CoverageBanner: `div[role="status"]` titled `Limited live coverage` or `Showing cached data`
  — rendered ONLY when coverage is degraded or the main cache is stale.
- Driver guidance: `[data-driver-guidance][role="status"]` explains the next action.
  `Live check running` is a preview; `N Go now options nearby` and `Open closest in Maps`
  require currently route-ready rows. Guidance changes immediately with its evidence.
- The compact trust badge and driver guidance replace duplicate counter tiles and the
  former timed route-ready toast/confetti. No separate readiness state or timer exists.
- Coverage warnings use complete, bounded driver-facing copy. Raw transport diagnostics
  are not rendered, and a generic no-live hint does not duplicate a coverage warning.

## Station list (virtualized)
- Scroll container: `[data-result-scroller]` inside the panel (the dialog scrolls separately). Items are absolutely
  positioned (translateY) — **only on-screen cards exist in the DOM** (@tanstack/react-virtual).
- Card wrapper: `[data-station="<id>"]` → `article.card`.
- Card select target: `div[role="button"][tabindex="0"]` (Enter/Space selects); the selected
  target has `aria-current="true"`. Filtering it away clears selection even if recovery
  later restores the same row.
- Badge row: `.pill` decision pill (`pill-go|pill-check|pill-verify`; label `Go now` /
  `Check first` / `Verify first` / `Cheap later` / `Check parking`…; `title` = decision reason),
  `.badge` tier (`Live`/`Official`/`Directory`/`Community`), optional `N sources`,
  tariff-window chip (clock, e.g. `Now Free 22:00–08:00`), `Sources disagree`,
  `Customers only`/`Restricted`, `Paid parking`/`Limited parking`.
- Title: `h3` (station name); city/address `p` below.
- Price: large `span` `Free` or `€0.NN` (+ `/kWh` unit span when paid).
- Availability row: exactly one visible `[data-availability="live"]` or
  `[data-availability="not-live"]` per card.
  Read its individual facts; whole-card textContent joins adjacent layout blocks without
  spaces. LIVE cards contain spans `N free`, `M in use`, `K out`, `T ports`, `up to X kW`.
  Leads show `Not live` (+ `T ports` when known). Unknown live: `Live · status unknown`.
- Route facts: `Locate to see ETA` (no coords) or `X km · Y min drive`; parking label; age `N min ago`.
  Located cards also show the matching standalone distance in a paragraph below the
  station name/address; this inline fact replaces the former distance badge.
- Connector chips: `.badge` rows `"<label> · <kW> kW"` + `+N more` when >6.
- Actions: `a` `Open in Maps` (Google/Apple maps href), `a` `Source` (provider page), both
  `target="_blank" rel="noopener noreferrer"`; `button` `Details` with `aria-expanded`.
- Details (expanded): the public source photo when one exists (it is no longer on the
  collapsed card), then `Why “<label>”:` decision reason; `Charging claim` / `Parking claim`
  sections with external `view source` links; `Corroboration: N independent sources…`;
  `Public sources (N)` link list.
- Empty state: `.card` with `No chargers match these filters` or `Nothing in view here` +
  decorative `.empty-state-visual` SVG + recovery buttons `Show all leads`, `Broaden price`,
  (`Clear search` when a query is set).

## Footer
- `button[aria-controls="data-honesty-panel"]` `Data honesty`, with `aria-expanded`. It is
  pinned below the result scroller, never after the last card: focusing an item at the end
  of the virtual list would scroll past every result.

## Map extras

- Light basemap uses public OpenFreeMap Positron with explicit soft water/park colors;
  dark uses its dark style.
  Source-backed unverified clusters use neutral color; bright availability signals require
  route-ready live evidence.
- `Search this area` floating `button` appears after map drift / zoom-in on overview.
- The map is a visual enhancement; selecting a pin never hides the primary panel. If a
  selected discovery point falls outside the panel viewport, its selected card is inserted
  at the top of the accessible list.
- Map points are stricter than list rows: live rows whose location is not explicitly
  rejected; route-checked leads; exact public directory station pages; and exact OSM
  node/way/relation objects with finite coordinates can be shown. Directory/OSM points
  stay `Not live`; generic community and unlocated leads do not get pins.
- A price pill (including `Free`) requires `verification.price_verified === true`. An
  exact source-backed location with an unverified price remains a plain dot, not a price
  claim. Cached/stale popup cards say `Cached · not live` and expose a source check rather
  than an `Open in Maps` action.
- Cluster badges and price pills are canvas images (NOT DOM) — do not assert them via DOM.

## Flows for tests
- **Default open = Mainland inventory and map camera** (leads only, no geolocation prompt).
  `nav[aria-label="Explore a region"]` provides `Mainland`, `Canary Islands`, `Balearic Islands`
  pressed-state buttons. Each fits that region without forcing a transcontinental initial zoom.
  Mainland excludes rows inside the Balearic bounds despite their overlapping rectangles;
  that exclusion ends when a manual pan takes ownership of the visible-area scope.
  Region bounds scope the inventory immediately, before map activation and even when WebGL
  is unavailable. Switching back from a live query discards its availability authority
  immediately while the new overview request is pending; Open now stays fail-closed. An intentional map movement then scopes results to the visible area.
- **Enter live mode**: `context.grantPermissions(['geolocation'])` +
  `context.setGeolocation({latitude: 39.47, longitude: -0.377})`, click `Use my location`;
  or pick a geocode dropdown row. Live fetch then carries `lat/lon` → cards gain `km · min drive`.
  A later city, region, or intentional map choice invalidates pending location callbacks.
  When location requests overlap, only the latest request can update the origin or permission state.
  Delayed map reports cannot supersede a newer location choice. Pending or denied location
  keeps the previous geographic scope until a successful result selects the new origin.
- **API**: same endpoints as before (`/api/chargers`, `overview=true`, `/api/geocode`);
  simulate failures with Playwright `page.route()` interception.
- **Basemap styles, fonts, and tiles** come from `tiles.openfreemap.org`; MapLibre itself
  is bundled same-origin. "CDN blocked" includes blocking the style request; the panel/list
  must stay fully usable. Semantic tests use a local empty style via the shared harness;
  the dedicated outage test blocks the real provider.
