# SuC Tracker Map Reference

Date observed: 2026-08-20

Source: [suc-tracker.eu](https://suc-tracker.eu/)

Status: product reference only. This document records observed behavior and
Volta-specific conclusions. It does not approve implementation, data sourcing,
or reuse of SuC Tracker code or visual assets.

## Executive conclusion

SuC Tracker's strongest pattern is semantic zoom. The map does not only make
the same marker larger. It changes the question each marker answers:

- overview: "what does it cost at the selected time?"
- regional: "what price levels occur during this day?"
- local: "when does each price apply?"
- selected station: "what is the exact tariff, evidence age, capacity, and
  official handoff?"

Volta should retain this progressive disclosure idea, but not copy the exact
marker system. SuC Tracker can focus almost entirely on tariff comparison
inside one network. Volta must keep price, live availability, decision level,
access, parking, and source confidence separate. A visually cheap price must
never imply `Go now`.

The useful direction for Volta is:

1. keep clusters at national and regional scales;
2. reveal a verified current-price pill at city scale;
3. mark a verified variable tariff with a small clock cue;
4. show the full time schedule only for a selected station or a deliberately
   opened tariff preview;
5. preserve the list and station sheet as the complete evidence surface.

## Evidence and method

The findings below combine:

- rendered inspection of the live desktop map;
- interaction with search, filters, price modes, markers, and station sheets;
- rendered DOM inspection at the three zoom tiers;
- a 390 x 844 responsive-layout inspection;
- inspection of the deployed map bundle
  `assets/MapPage-By3Mf6L-.js` to confirm zoom thresholds and fallback behavior;
- comparison with Volta's current web and native map implementations.

The site and its deployed bundle can change. Treat the exact thresholds and
examples as a dated observation, not a permanent external contract.

## Exact semantic zoom model

SuC Tracker uses three explicit tiers:

| Map zoom | Internal tier | Marker answers | Rendering strategy |
|---|---|---|---|
| `< 8` | `overview` | Price for the active price mode | One batched canvas overlay with custom hit testing |
| `8` to `< 9.5` | `medium` | Distinct price levels for the chosen day | Individual DOM/MapLibre markers |
| `>= 9.5` | `detailed` | Full 24-hour tariff shape and transition times | Individual DOM/MapLibre clock markers |

Crossing a tier boundary clears the old marker representation and rebuilds the
new one. Moving or zooming inside one tier redraws the current representation.
This avoids mixing two information densities during a zoom gesture.

### Tier 1: overview, zoom below 8

Observed behavior:

- Each station is a compact circular price marker, about 37 x 37 CSS pixels.
- The number is the active `Price view` value: selected time, daily minimum, or
  daily maximum.
- The border color represents the price under the active color-scale mode.
- A variable tariff uses a dashed border; a flat tariff uses a solid border.
- `Delta` marks a recently changed price when that filter is active.
- `!` marks stale data or a failed latest poll.
- Selection adds a separate outline and a small scale increase.
- Markers are painted in one canvas rather than creating hundreds of DOM
  elements.
- The app implements its own canvas hit testing so the markers still support
  hover and click.
- Hover opens a compact station preview with a miniature daily tariff chart.

What works:

- The number remains legible at continental scale.
- Canvas rendering prevents a large DOM-marker cost.
- Flat, variable, changed, and warning states remain distinguishable without
  opening a card.

What does not transfer cleanly to Volta:

- SuC Tracker draws every station instead of clustering. At Europe scale the
  markers overlap heavily and obscure geography.
- A price-first map is appropriate for a mostly homogeneous charging network,
  but it would hide Volta's larger differences in evidence and route readiness.
- Canvas markers are not exposed as individual controls in the accessibility
  tree at this tier.

### Tier 2: medium, zoom 8 to below 9.5

Observed behavior:

- Markers switch from the shared canvas to individual button elements.
- A flat tariff remains a single price.
- A variable tariff becomes a horizontal segmented pill.
- Each segment shows a distinct price level for the selected weekday.
- The price applying at the selected time receives the active treatment.
- Each segment carries its own price color.
- The marker does not yet show transition times; it communicates the day's
  range and which level is active.

Live example captured at zoom 8.35:

- Laucha displayed `32 | 40 | 43`.
- `40` was active at the captured time.
- Nearby flat-tariff stations displayed one value such as `41` or `43`.

Why this tier is effective:

- It reveals tariff variability without forcing the user to decode a chart.
- It keeps the map comparable: one glance shows flat versus variable pricing.
- Individual button markers restore keyboard and semantic interaction.

Volta implication:

- A small `clock` or `variable` cue could be useful at city scale.
- Showing every price level is too strong for Volta until the API can prove that
  all levels share the same connector, access, membership, and claim contract.
- The main label must remain the verified price for the selected time, not the
  cheapest price found anywhere in the schedule.

### Tier 3: detailed, zoom 9.5 and above

Observed behavior:

- A variable tariff becomes a radial 24-hour clock.
- Colored arcs show the chronological tariff windows.
- Prices appear inside the wheel.
- The active price receives a filled chip.
- A radial hand shows the selected time.
- Quarter-day ticks provide orientation.
- Exact tariff-change times appear around the wheel.
- Marker size grows when more periods or transition labels need space.
- A flat tariff uses a much simpler single-color ring and one price.

Live example captured at zoom 10.50 for Laucha:

- `00:00-08:00`: 32 ct/kWh
- `08:00-12:00`: 40 ct/kWh
- `12:00-18:00`: 43 ct/kWh
- `18:00-22:00`: 40 ct/kWh
- `22:00-24:00`: 32 ct/kWh
- The active captured price was 40 ct/kWh.
- The adjacent Erfurt flat-tariff marker showed one 43 ct/kWh ring.

Why it is memorable:

- Space represents time, so the user sees cheap and expensive windows without
  reading a table.
- The selected-time hand connects the global time control to every station.
- The same tariff grammar appears again in the selected-station chart.

Why Volta should use it sparingly:

- A detailed selected marker occupied roughly 106 x 106 CSS pixels in the
  captured view and can hide nearby roads and stations.
- Rendering a clock for every visible station is too dense for a 390px driving
  surface.
- Volta's core decision includes availability, route, parking, and source
  confidence; a large tariff wheel can dominate the wrong variable.

Recommended Volta adaptation:

- Keep full tariff geometry in the selected-station sheet.
- If a map treatment is tested, show it only on the selected station or a very
  small capped set of top candidates.
- Keep unselected stations as compact price pills with a clock cue.

## Global tariff controls

The map has a persistent tariff-preview control above the map:

- weekday buttons for Monday through Sunday;
- a 00-23 hour slider;
- selected-time readout;
- `Now`, which returns the preview to live clock time.

Changing the weekday or time updates all markers, the legend, filters, and the
selected station. This makes time a map-wide comparison dimension rather than
a fact buried inside one card.

Important Volta boundary:

- A future tariff preview must never imply future connector availability.
- The control should be named `Tariff preview` or equivalent.
- Live availability remains `now` even when price is previewed for another time.
- `Go now` must not follow the preview clock into the future.

## Price-view and color-scale controls

`Price view` supports three marker values:

- selected time;
- daily minimum;
- daily maximum.

It also supports four color modes:

- absolute: the same price always uses the same color;
- relative to the current viewport;
- relative within each country;
- manual minimum and maximum.

The captured default was relative to the current viewport. The legend explains
that lower visible prices map toward teal and higher visible prices toward red.
Its endpoints change with viewport and filters; examples observed during the
review included `14-88`, `20-55`, and `32-43` ct/kWh.

What works:

- The user can choose comparison semantics instead of guessing what color
  means.
- The legend always publishes its numeric range.
- Country-relative mode avoids comparing raw prices across markets without
  context.

Risk for Volta:

- A viewport-relative color changes when the user pans or zooms. The same
  station can look better or worse without its price changing.
- Relative color can make a price above Volta's EUR 0.25 ceiling look
  attractive merely because nearby stations cost more.
- Volta already uses color for `go`, `check`, and `verify`; reusing the same
  channel for relative price would conflate cost with route readiness.

Recommendation:

- Keep Volta's absolute free / `<= EUR 0.25` policy as the primary price rule.
- Keep decision color separate from price text.
- If a price heat scale is ever added, make it optional, publish the range, and
  never let it replace decision or provenance signals.

## Filters

Observed filters:

- country multi-select with `Show none` and reset;
- maximum price at selected time;
- maximum daily minimum;
- maximum daily maximum;
- highlight price changes in the last 7, 14, or 30 days;
- include or exclude stations without a comparable Tesla tariff.

Strong pattern:

- The filter names distinguish current-time price from daily min/max. They do
  not collapse three different questions into one generic `Price` control.

Volta adaptation:

- Preserve the existing free-first / `<= EUR 0.25` quick choice.
- Treat daily min/max and change history as planning tools, not primary driving
  controls.
- Do not expose a daily minimum until conditions and connector eligibility are
  normalized; otherwise `minimum` can advertise a price the driver cannot use.

## Search, selection, and URL state

Observed behavior:

- Search accepts station, city, address, or Tesla station ID.
- Selecting a result opens the station and focuses the map at at least zoom
  10.5.
- Desktop focus offsets the station left to leave room for the side sheet.
- Mobile focus uses an upward offset for the bottom sheet.
- Selecting a station updates the URL to `/station/{id}`.
- The sheet exposes a full permalink plus share and copy actions.

This is worth adopting as interaction grammar:

- a station selection should be deep-linkable;
- map focus should reserve space for the detail surface;
- opening or closing details should not erase the user's chosen tariff time;
- camera changes from selection must remain distinct from user pan/zoom intent.

## Selected-station sheet

The sheet continues the map's price grammar and adds evidence:

- station ID, name, and full address;
- selected-time price;
- daily minimum and maximum;
- stall count and maximum power;
- `Flat tariff` or `Variable tariff`;
- visual 24-hour price chart;
- text price schedule as a non-visual alternative;
- `Last checked`;
- `Last successful update` as a separate timestamp;
- permalink, share, and copy;
- official Tesla handoff;
- incorrect-data report action.

Two details are especially strong:

1. `Last checked` and `Last successful update` are separate. A failed poll does
   not silently make old data look current.
2. The visual tariff has a text schedule. The chart is not the only source of
   exact times and prices.

Volta should preserve those principles while adding its own required evidence:

- charge claim and parking claim remain separate;
- live status timestamp remains separate from price timestamp;
- tariff conditions remain visible;
- source tier and corroboration remain visible;
- official/source/map handoffs stay explicit;
- community evidence may annotate or downgrade but never create live authority.

## Warnings and change history

Observed marker signals:

- dashed border: variable tariff;
- `Delta`: recently changed price;
- `!`: stale lifecycle or failed latest price poll;
- gray: no comparable tariff or exchange rate;
- selected outline: active station.

The recent-change filter is a useful planning feature because it draws
attention to tariff volatility rather than treating every stored price as
equally stable.

Volta adaptation requires more explicit wording. A small symbol can direct
attention, but the selected card must say what changed, when it changed, and
which claim/source it affects.

## Responsive and accessibility findings

Positive findings:

- Weekdays form a named radio group.
- Time is exposed as a slider with a selected-time status.
- Medium and detailed markers are buttons with station-specific accessible
  labels.
- The selected station is exposed as a complementary region.
- The tariff chart has a text schedule alternative.
- The sheet keeps the map visible and changes its placement by breakpoint.

390 x 844 findings:

- The header occupied about 224px vertically.
- The tariff control wrapped into a 374 x 98px surface.
- The selected-station sheet became a full-width bottom sheet about 456px
  tall in the captured state.
- Price-view and filter controls collapsed to glyph buttons.
- In the captured accessibility tree those compact buttons exposed only `◐`
  and `≡` glyph content, not descriptive accessible names.

Do not copy these weaknesses:

- Persistent global tariff controls plus an open station sheet leave little
  map area on a phone.
- Large detailed clocks can cover the remaining route context.
- Compact icon controls must retain explicit accessible names.
- Low-zoom canvas markers need an equivalent list/summary surface because they
  are not individual accessible controls.

Volta should keep its map/list duality, 44pt targets, existing focus contracts,
and native VoiceOver completion gate.

## Current Volta baseline

### Web

Current `frontend/src/components/MapView.tsx` already implements a simpler
semantic zoom:

- GeoJSON clustering with radius 56 and cluster max zoom 13;
- cluster count plus route-ready/free tone;
- unclustered decision-colored dots;
- price pills from zoom 8.5;
- MapLibre collision handling for price pills;
- priority for live, cheaper, and `go` rows;
- separate selection ring;
- map pins stricter than list rows;
- buffered viewport fetch and `Search this area` behavior.

Current strengths to retain:

- national density stays readable through clustering;
- price labels do not all overlap;
- cheaper data never outranks the live/lead honesty boundary;
- user pan/zoom intent is protected from 60-second refresh recentering;
- the app still works as a list if tiles fail.

Current gap relative to the reference:

- map price labels expose `Free` or `price_min`, while time-window meaning lives
  mainly in card chips and decision text;
- the map does not reveal flat versus variable pricing;
- there is no map-wide tariff-preview time;
- zoom does not progressively reveal a verified time schedule.

Do not reinterpret `price_min` as `price at selected time`. A temporal map needs
an explicit derived field and validity contract.

### Native iOS

Current `ios/Volta/Volta/RootView.swift` uses:

- clusters only when more than 60 in-view rows appear at a regional/national
  span;
- price pills at city scale;
- grid thinning because SwiftUI Map annotations lack MapLibre collision logic;
- live-first and free-first selection when a grid cell has multiple rows;
- a hard cap of 90 pills;
- accessible cluster summaries and station values;
- selection haptic only on explicit pin/cluster taps.

Current strengths to retain:

- clusters remain a regional overview tool;
- city zoom prioritizes the driver's decision surface;
- map density is bounded;
- accessibility summaries reflect route-ready and free-lead distinctions;
- pan, zoom, and locate stay quiet.

Any tariff preview should live primarily in the persistent sheet. A detailed
clock on every SwiftUI annotation would work against the existing declutter
strategy.

## Proposed Volta semantic zoom

This is a design direction, not an implementation plan.

| Volta state | Primary marker | Optional tariff disclosure | Honesty rule |
|---|---|---|---|
| Spain/national overview | Existing cluster count and route-ready/free tone | None | Never average incompatible prices or claims |
| Regional / committed bbox | Clusters plus strict station dots | Small variable-tariff cue on verified rows | No price if location/price contract is not verified |
| City / decision view | Collision-managed current-price pills | Clock cue and `cheap later` state | Price must be valid for the selected time and conditions |
| Selected station | Selected pill/ring plus card or sheet | Full daily schedule and min/max | Text schedule, source time, conditions, and conflict state required |

Key decision:

- Do not put SuC Tracker's full clock on every station by default.
- Prototype a selected-station-only clock first.
- Keep decision color on a separate dot/bar/ring from tariff color.

## Required data contract before implementation

A trustworthy tariff preview needs more than `price_min`:

- station timezone;
- weekday and minute selection;
- connector-specific pricing windows;
- effective start/end dates or an explicit current-validity result;
- eligible connector, power, membership, and access conditions;
- currency and conversion timestamp when conversion is used;
- price-source timestamp;
- live-status timestamp kept independent;
- explicit no-price/unknown state;
- conflict state when public sources disagree;
- daily min/max computed only across compatible, usable tariff windows.

Fail closed:

- unknown timezone: no selected-time price;
- expired or stale pricing window: no current-price claim;
- incompatible connector conditions: do not merge price levels;
- subscription-only tariff: label the condition before comparison;
- future tariff preview: never predict availability;
- directory/community evidence: never set live verification;
- parking charge: never merge into the charging-price scale.

## Performance model worth borrowing

The reference changes rendering technology with information density:

- low zoom: one canvas and custom hit testing;
- medium/detailed zoom: bounded DOM markers in and just outside the viewport;
- selected station: richer DOM/SVG treatment;
- tier changes: rebuild once, then redraw cheaply inside the tier.

Volta already has a sound alternative on web: GeoJSON clustering plus generated
canvas images inside MapLibre. A future prototype should extend that system
before introducing a second full marker engine.

Recommended performance constraints:

- preserve clusters at national/regional zoom;
- cap rich tariff markers;
- keep collision handling and live/cheap/go priority;
- do not rebuild all markers on every minute tick;
- update the selected marker and visible candidates only;
- preserve reduced motion;
- preserve list-only recovery when the map or tiles fail.

## Acceptance criteria for any future prototype

Data honesty:

- a selected-time marker never displays an invalid, expired, or incompatible
  tariff;
- changing tariff preview time does not change live availability;
- `Go now` remains based on real current live data;
- price and parking provenance remain separate;
- stale and conflict states remain visible;
- leads never gain live or route-ready styling.

Semantic zoom:

- every tier has one clear question;
- tier transitions do not flicker or leave stale markers;
- selection and preview time survive zoom transitions;
- user pan/zoom is never overwritten by refresh;
- national density remains lower than the SuC Tracker reference.

Accessibility:

- every visible interactive marker has a useful accessible name and value;
- canvas-only overview has an equivalent list or summary;
- the clock has an exact text schedule;
- color is never the only distinction;
- keyboard focus does not enter decorative scroll/map controls;
- 390px layout keeps map, primary action, and close/recovery controls usable;
- native VoiceOver validation remains manual `TC-056` evidence.

QA and resilience:

- test below, at, and above every zoom threshold;
- test flat, variable, unknown, stale, conflicting, and conditional tariffs;
- test `Now`, another weekday, and a future time;
- test locale, timezone boundary, currency, and midnight wrap;
- test dark/light contrast and reduced motion;
- test tile-blocked list recovery;
- keep physical tariff/parking/route validation under `TC-051`.

## Recommended next step when implementation is authorized

Run a narrow prototype with mocked, verified time-of-use rows:

1. Add an explicit selected-time price projection to the frontend model without
   changing production ranking or decisions.
2. Add a small variable-tariff cue to existing city price pills.
3. Add a selected-station schedule to the web card and native sheet.
4. Test a selected-only radial marker behind a local feature flag.
5. Compare it against a simpler linear 24-hour strip on 390px.
6. Promote only the smallest treatment that improves route decisions and keeps
   the full honesty contract visible.

No public-release status, completion audit, or manual gate changes follow from
this reference review.
