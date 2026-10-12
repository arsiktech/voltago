# Web discovery design

Updated 2 October 2026 after the owner's annotated dev-browser review.

## Direction

A calm, practical map workspace: warm off-white surroundings, opaque white result
surfaces, dark readable text, and one restrained forest-green accent. Inter stays
local and keeps the existing typography/toolchain. Search leads the panel; charger
name and charging price lead each result. Borders separate content, with elevation
reserved for floating map controls and the filter dialog. Avoid repeated badges,
status-colored price promises, decorative halos, and an ocean-sized initial map.

`frontend/src/index.css` owns the palette and responsive geometry. The map occupies
its own rectangle beside the desktop panel; no JavaScript sidebar-width compensation
is needed. Positron/Dark remain the existing public keyless basemap styles. The light
water and park paint colors use CSS tokens and tolerate absent provider layers.
`lib/basemap.ts` guards the three currently malformed road-shield filters before
MapLibre commits them to workers. Only the exact known numeric comparison is
patched; valid reference lengths and original network/geometry rules remain.
The [MapLibre transform lifecycle](https://maplibre.org/maplibre-gl-js/docs/API/type-aliases/TransformStyleFunction/)
and [short-circuit expression rule](https://maplibre.org/maplibre-style-spec/expressions/#all)
explain why this runs before style loading rather than suppressing runtime warnings.

## Reference decisions

These are adaptations of interaction patterns, not copied assets or interfaces.

- [Airbnb September 2026 update](https://news.airbnb.com/en-au/airbnb-2026-september-update)
  and [Summer 2026 release](https://news.airbnb.com/product-releases/airbnb-2026-summer-release):
  give the search task priority and reduce filter hunting. Volta keeps explicit
  location search and deterministic filters; no AI search functionality is implied.
- [Electroverse map guide](https://electroverse.com/en-GB/community/ev-blogs-and-guides/how-to-use-the-electroverse-map-):
  quick choices near search, with specific constraints in a dedicated filter panel.
- [AllTrails map guide](https://support.alltrails.com/hc/en-gb/articles/360034969432-How-to-use-map-view-to-search-for-trails):
  complementary map and list surfaces. Volta adds a full-list mobile mode while
  retaining its accessible result cards and map selection behavior.
- [Apple materials guidance](https://developer.apple.com/design/human-interface-guidelines/materials):
  restrained material treatment for controls; station evidence remains opaque.
- The owner's Obsidian note **Interface design quality for agent-built apps**
  reinforced structure before styling, removing redundant chips, and checking real
  desktop/mobile renders. No private vault assets or note contents are shipped.

## Interaction ownership

- `App.tsx`: region/search scope, selected result, full-list mode and evidence state.
  Mainland is the initial scope; Canary/Balearic shortcuts set geographic bounds
  independently of WebGL. Only intentional map exploration returns overview scope
  to the visible map bounds. Selecting a region preserves chosen filters.
- `MapView.tsx`: camera and rendering. Early region/location choices survive lazy
  map initialization. ResizeObserver tracks the actual canvas container. Cluster
  counts stay neutral unless a genuinely route-ready station warrants green.
- `FilterRail.tsx`: Free shortcut and adjustable maximum charging price, native
  modal, grouped descriptions, changed-filter summary and reset. The native range
  uses integer cents from 0–100, then a 101 sentinel for No limit. Keyboard Home
  selects Free; End clears the cap. The quick price control describes the selected
  cap to assistive technology. Closing returns focus to the actual opener.
- `types.ts`, `api.ts`, `format.ts`: one `maxPrice` value in €/kWh owns the price
  filter and request/cache identity. Null preserves broad discovery; zero means
  free. Numeric tariffs determine eligibility, including when `is_cheap` disagrees.
  Unknown prices do not satisfy a chosen cap; explicit free evidence may fill a
  missing numeric tariff. Parking and live authority stay separate. `App.tsx`
  filters locally immediately and waits 250ms for price movement to settle before
  changing requests. See the slider browser contract for boundaries and recovery.
- `StationCard.tsx`: place/price hierarchy, one decision pill, plain source metadata,
  critical restrictions, availability, parking, connectors, and source disclosure.
  `aria-current` identifies the selected result. A non-route-ready price/Maps action
  uses neutral styling; a low price alone never looks like availability approval.
- `format.ts`: returning from a live query to overview cannot retain live placeholder
  rows. Only the separate overview inventory may bridge that transition.

The map remains optional. Location permission is never requested automatically;
city search and region selection work without it. Source tier, live availability,
charging cost, parking cost, conflicts and access restrictions remain distinct.

## Verification paths

See `docs/spa-dom-contract.md` for exact selectors and `README.md` for the complete
source-bound receipt workflow. The focused browser checks exercise modal defaults,
keyboard/focus return, mobile List/Map, region fallback without WebGL, delayed map
activation, live-to-overview authority, and the existing failure/recovery journeys.
Run frontend tests, lint/build, the real map runtime checks and the full browser
contracts before merging. Render desktop/mobile and light/dark views; compare the
actual result with the owner's annotated starting screenshots. Manual field and
native VoiceOver release evidence remains separate from this web change.
