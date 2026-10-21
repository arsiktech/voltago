# Volta Go design system

The canonical guide for building Volta Go UI on web (`frontend/src/`) and iOS
(`ios/Volta/`). Read it before adding or restyling a screen, component, map mark,
animation, or user-facing string. When code and this file disagree, fix whichever is
wrong in the same change.

**Direction: "Green means go."** Volta Go is a neutral ink interface. Actions,
selection and focus use ink; green is reserved for the Go-now evidence state, so the
one saturated color on screen always means "a charger is free right now". The owner
chose this direction on 4 October 2026 over three alternatives (keeping the old forest
green, a warm paper-map look, and a dark-first night look).

Code owns the values. This file names them and explains when to use them:

| Concern | Web source of truth | iOS source of truth |
| --- | --- | --- |
| Color, radius, font tokens | `frontend/src/index.css` (`:root`, `[data-theme='dark']`, `@theme inline`) | `ios/Volta/Volta/Theme.swift`, `Assets.xcassets/AccentColor` |
| Motion | `frontend/src/lib/motion.ts` | `VoltaMotion` and `VoltaPressButtonStyle` in `Theme.swift` |
| Decision labels and levels | `DECISION_META`, `decisionLevel`, `isRouteReady` in `frontend/src/lib/format.ts` | `DecisionLevel` and `isRouteReady` in `Models.swift` |
| DOM/accessibility contract | [spa-dom-contract.md](spa-dom-contract.md) | `tests/test_ios_design_contract.py` |

Dated design passes ([visual system](visual-design/volta-visual-system.md),
[Liquid Glass adaptation](visual-design/liquid-glass-adaptation-2026-09-05.md),
[web discovery redesign](web-discovery-design.md)) record why earlier choices were
made. Use them for history and evidence, and this file for the current rules.

## 1. Principles

1. **Honesty before delight.** The interface never makes a station look more usable
   than its evidence. Stale or unverified data never looks route-ready.
2. **Green means go.** Green is a promise that a charger is available now. Buttons,
   selected filters, focus rings and decoration never use it.
3. **Answer the driver's question first.** Order of attention: can I charge here now,
   what does charging cost, what does parking cost, where is it. Free first, cheap next.
4. **Fewest taps wins.** Every extra tap, dialog or screen has to earn its place. See
   section 9.
5. **A calm instrument.** Neutral surfaces, opaque content, borders before shadows. No
   decorative blobs, gradient washes, confetti or idle animation.
6. **The list works without the map.** Map tiles, WebGL and location permission are
   optional; search, region choice and the result list stand alone.
7. **Accessible by construction.** 44pt targets, visible boundaries, color never the
   only cue, and a reduced-motion and reduced-transparency path for every effect.

## 2. Evidence semantics

Every station resolves to one decision level, with a fixed label, color and icon on
both platforms. Do not invent new levels or reuse these colors for other meanings.

| Level | Default label | Token (web / iOS) | Web icon (lucide) | Style | Meaning |
| --- | --- | --- | --- | --- | --- |
| `go` | Go now | `--go` / `Theme.go` | `Check` | **Solid** green, `--go-contrast` text | Live, fresh, verified, a connector is free. Only `isRouteReady()` rows. |
| `check` | Check first | `--check` / `Theme.check` | `Clock` | Tinted | Live source, but availability or freshness needs a look. |
| `verify` | Verify first | `--verify` / `Theme.verify` | `ShieldAlert` | Tinted | Lead or unverified source; confirm before driving. |
| `lead` | Price lead | `--muted` / secondary | `MapPin` | Tinted neutral | Known place without a free/cheap price: "Paid" when the source publishes a price above €0.25/kWh, "Price lead" when it publishes none. Ranked after every free/cheap option. |
| `avoid` | Avoid | `--danger` / `Theme.danger` | `Ban` | Tinted | Known blocker (closed, incompatible connector, access restriction). A price alone is never a blocker. |

- Go now is the only solid evidence fill. Every other state is a 12–16% tint with a
  full-strength label, so the eye finds the usable charger first.
- Map pins and clusters are green only when they contain a route-ready station;
  otherwise neutral or `verify`. A low price alone never turns anything green.
- One decision pill per station. Collapsed web cards show at most one additional caution:
  the most important issue plus a count of the rest. Never hide access restrictions,
  source disagreements or outages without that indicator. Its accessible name lists
  all cautions. Full conditions stay in Details, even if deferred evidence fails.
- Keep source tier and observation age as plain text. Source counts, tariff schedules
  and additional directory/network prices belong in Details, not another badge row.
- Charging and parking are separate facts with their own labels and provenance.
- Unknown is shown as unknown. An unknown price never satisfies a price filter and is
  never rendered as zero or free.
- Community input can annotate or downgrade a row, never upgrade it to `go`.
- Source and freshness stay visible in plain words ("Official data · updated 3 h
  ago"), with detail behind a disclosure.
- Published tariffs remain visible as `Source price` until verified, including hourly
  rates and conditional text. Charging-price verification, price filters and live
  availability keep their separate authority rules.
- Source action buttons name the linked provider (`Open Canarias te recarga`) and
  open that exact charger page. Generic provider homepages and map overviews stay
  broader references in Details; they never stand in for an exact charger action.
- The only celebration is the quiet iOS route-ready confirmation when a real live `go`
  row appears; never for directory, stale, manual or lead rows.

## 3. Color

Use semantic tokens, never raw hex in components. Tailwind utilities map to tokens
(`bg-surface`, `text-muted`, `bg-brand`, `text-go`) and flip with the theme. The map
canvas and native Swift drawing are the only places that repeat hex values; keep them
in step with the tokens below.

| Role | Web token | Light | Dark | iOS token |
| --- | --- | --- | --- | --- |
| Page ground | `--bg` | `#F5F6F4` | `#0B0F0D` | `Theme.canvas` |
| Content surface | `--surface` | `#FFFFFF` | `#141A17` | `Theme.surface` |
| Fills (chips, hover, nested) | `--surface-2`, `--surface-3` | `#EEF0EE`, `#E6E9E6` | `#1B221F`, `#222A26` | `Theme.raisedSurface` |
| Hairlines | `--border`, `--border-strong` | ink 10%, 18% | white 8%, 16% | `Theme.hairline` |
| Text | `--fg`, `--fg-soft`, `--muted`, `--faint` | `#0E1512`, `#2A332F`, `#5A6560`, `#8B948F` | `#EDF1EE`, `#CDD5D0`, `#9AA59F`, `#6F7A74` | `Theme.ink`, `.secondary` |
| Action fill + label | `--brand` + `--brand-contrast` | `#0E1512` + white | `#EDF1EE` + `#0B0F0D` | `Theme.action` + white |
| Action text/icons | `--brand-strong` | `#2A332F` | `#FFFFFF` | `Theme.actionInk` |
| Focus ring | `--ring` | ink 70% | `#EDF1EE` 75% | system |
| Logo mark | `--mark` | `#0D7D40` | `#21D277` | app icon |
| Evidence | `--go`, `--check`, `--verify`, `--danger` | `#0D7D40`, `#986200`, `#426AC3`, `#D83A40` | `#21D277`, `#F0B429`, `#7AA2F7`, `#FF6B70` | `Theme.go/check/verify/danger` |

- `--brand` keeps its name for compatibility but is ink, not green. `--accent` is a
  neutral grey kept for the empty-state illustration.
- The logo period (`.brand-period`, the discovery headline period) and the app icon are
  the only green outside evidence states.
- iOS dark mode uses `#646F6A` for `Theme.action` and `AccentColor`, not the light ink
  the web uses, because system prominent buttons keep white labels: white is 5.2:1 on
  it and its edge is 3.7:1 against the dark canvas.
- `--faint` is for decoration and disabled states only, never for information.
- Tinted decision and caution text blends its evidence color with 16% foreground ink
  so the small labels retain at least 4.5:1 contrast against their tinted fill.
- Check every new surface in both themes.

## 4. Typography

- **Web:** Manrope Variable (`@fontsource-variable/manrope`, bundled locally, with
  Latin and Cyrillic). Headings 700; the discovery title 34px/750; card titles 16/600;
  prices 20/600; body 13–14/400; meta 12/600; pills 12/680; badges 11/600; uppercase
  labels 10–11/500 at 0.08em. `index.css` never uses negative letter-spacing (a
  text-spacing accessibility guard in `tests/test_accessibility_spa.py` checks it).
- **iOS:** San Francisco through Dynamic Type text styles (`.headline`,
  `.subheadline`, `.caption`), with `.rounded` design for prices on the map. Prefer
  text styles over fixed sizes so layouts grow with accessibility sizes.
- Numbers use tabular figures (`tabular-nums` on web) wherever they update in place
  or line up in a list.
- Nothing a driver must read is smaller than 11px on web or `.caption2` on iOS.

## 5. Shape, spacing, and layout

- **Radius:** pills, chips and full buttons fully rounded; badges ~9px; controls
  ~14px (`--radius-xl`); cards and sheets ~18px (`--radius-2xl`), panels 24px. An
  outer container's radius is larger than what it contains. Use `.continuous` corners
  on iOS.
- **Spacing:** 4px base. Panel padding 24px (20px tablet, 16px mobile); page gutter
  `--page-gutter` 24/16px; result list 16px side padding; 8px between sibling
  controls, 12px between groups.
- **Web layout:** header `--header-height` (76px, 60px mobile); a results panel of
  `--discovery-width` (432px, 380px tablet) beside an independent map rectangle. Below
  768px the map is a strip above a bottom sheet, with a full-list mode. Geometry lives
  in CSS variables; do not compensate layout in JavaScript.
- **Results panel structure (web):** search and quick filters stay pinned at the top;
  everything below is one scroller. The result count and refresh are a sticky heading,
  while status notes and banners scroll with the cards. The Data honesty footer stays
  pinned below the list. Give
  the space to results, not fixed chrome. The marketing headline shows only before the
  driver has chosen a place.
- **Mobile sheet (web):** a List/Map button in the pinned results heading switches
  between split and list layouts. The initial sheet starts at 58dvh, capped to leave
  300px for search and controls, so Spain fills more of the visible map. Choosing a city
  raises the list. Dragging the grabber does the same, and a finger
  scroll down the results raises the sheet, as a native sheet would.
- **iOS layout:** one full-screen map and one persistent native sheet, like Apple Maps.
  Detents: collapsed (exactly the search row), medium and large; accessibility text sizes
  use medium and large. Search, Settings and the filter chips stay pinned at the top of the
  sheet; the results heading, status notes and cards are one scroller below them. Choosing a
  pin or a card opens the detail inside the sheet (raising a collapsed sheet to medium, or to
  large at accessibility sizes). The map frames its content above the medium sheet, and the
  floating chrome (brand lockup, Find my location, Search this area) caps its text size at
  `accessibility1`. Nearby pins merge into counted clusters in a ~72pt screen grid.

## 6. Materials and elevation

- **Content is opaque.** Station cards, sheets, prices, evidence and the results panel
  never sit on glass.
- **Glass is for small floating map controls only:** web `.glass`/`.glass-strong`
  (brand lockup, area search, region switcher); iOS system Liquid Glass via
  `glassEffect` in a `GlassEffectContainer` for map buttons.
- **Fallbacks are required:** solid `--surface`/`Theme.surface` when reduced
  transparency, increased contrast or forced colors is on, and when `backdrop-filter`
  or the iOS API is unavailable.
- **Elevation:** borders separate content. `--shadow-sm` for cards, `--shadow-md` for
  floating controls, `--shadow-lg` only for dialogs and popovers.

## 7. Motion and micro-interactions

Motion explains what changed. It never decorates, and it never delays the driver.

| Moment | Timing | Preset (web / iOS) |
| --- | --- | --- |
| Press | 120ms ease-out, scale 0.96 (0.98 for large surfaces) | `pressSpring` / `VoltaMotion.press` |
| State change (selection, color, fade) | 180–200ms | `quickEase` / `VoltaMotion.state` |
| Spatial (sheet, panel, disclosure) | ~280ms spring, no bounce | `smoothSpring`, `softSpring` / `VoltaMotion.spatial` |
| Value change (price, count) | 180ms vertical swap | `countSwap`, `liveMetricSwap` |
| List entrance | 22ms stagger, capped at 8 rows | `staggerList` |

Rules:

- **Every control answers the press immediately:** a scale and opacity change on
  press, then a color change for the new state. Use one press mechanism per control:
  framer `whileTap` or CSS `:active`, never both, because they compound.
- **No hover lifts.** Hover changes color, border or fill only, and only for fine
  pointers. Elements never jump on hover.
- **Keyboard is instant.** Focus and Space/Enter feedback apply without waiting for an
  animation.
- **Stable geometry.** Toggling a control never moves it or its neighbours: selection
  checkmarks have a reserved slot, and counts swap in place.
- **Disclosures** rotate their chevron 180° and animate height; reduced motion fades.
- **Loading:** show a skeleton within 300ms, keep the previous results visible while
  refreshing, and never blank a list the driver is reading.
- **Status signals settle.** The live dot pulses three times on arrival, then rests.
  No infinite loops except the platform-standard user-location ring.
- **Reduced motion** removes movement and keeps opacity and color feedback
  (`useReducedMotion()`, `accessibilityReduceMotion`).
- **iOS haptics are sparing:** selection feedback on pin or cluster tap and on filter
  changes. Pan, zoom and locate stay silent.

## 8. Controls, sizing, and visibility

**Sizing**

- Every interactive target is at least 44×44pt/px, including chips, pills used as
  buttons, icon buttons, map controls and the search clear button.
- **Controls side by side share a height.** A row of buttons, chips or segments uses
  one height (44px by default) whatever their labels.
- **Peer actions share a width.** Two or more equal-weight actions in a row (for
  example "Open in Maps" and "Start page") split the row evenly with `flex-1`. A
  utility control in the same row (for example "Details") may be narrower but keeps
  the same height.
- Icon-only buttons are square 44×44 and always have an accessible name.

**Hierarchy**

- At most one primary action per card or view: ink fill with the contrast label.
- Secondary actions: surface fill with a visible 1px border. Tertiary actions:
  underlined text, still 44px tall.
- On a station card, the primary action follows the evidence: "Open in Maps" for a
  route-ready row, the source or provider page otherwise.
- Labels are verbs, three words or fewer, in sentence case ("Search this area",
  "Try again").

**Visibility**

- Text contrast is at least 4.5:1, and 3:1 for 24px+ text.
- Every button has a visible boundary (fill or border) of at least 3:1 against its
  background; no borderless "ghost" primary buttons.
- The focus ring is a 2px `--ring` outline with a 2px offset, visible on every
  surface.
- Selection uses a non-color cue too: a checkmark, `aria-pressed`, `aria-current`, or
  a VoiceOver trait.
- Tailwind utilities beat component classes in `index.css`. A control that adds
  `bg-*` or `text-*` utilities must also style its selected state with utilities
  (`aria-pressed:bg-brand aria-pressed:text-brand-contrast`). Otherwise the selected
  label can end up the same color as its background.
- Disabled controls say why nearby, or are hidden when they can never apply.

## 9. Fewest taps

Simplicity is measured in taps and decisions. Before adding a screen, dialog or step,
find a way to remove it.

- **Useful before any input.** The app requests location on opening and shows nearby
  chargers once a fix arrives. Mainland inventory remains usable while location is
  pending, unavailable or declined. A newer city, region, station or map choice wins
  over a delayed automatic fix. The fixed cached catalogue remains available when a
  nearby request fails; device-location and viewport queries are never stored offline.
  No sign-in or onboarding is needed.
- **One tap for the common filters.** Free and maximum price sit next to search; the
  full filter sheet holds the rest. A changed-filter summary and one-tap reset sit in
  view.
- **Instant feedback, bounded requests.** Local filtering applies immediately; network
  requests wait for input to settle (250ms for the price slider).
- **The next step is one tap away.** A route-ready card opens navigation in one tap;
  the source page is one tap for everything else.
- **Every empty or error state offers its fix as a button** ("Search wider area",
  "Show all leads", "Broaden price", "Try again"), never only a sentence.
- **No confirmation dialogs for reversible actions.** Make the change and let the
  driver undo it.
- **Keep the driver's place.** Region changes keep chosen filters; closing a dialog
  returns focus to its opener; the selected station stays selected across refreshes.
- **Never ask for what the app can infer,** and never ask twice.

## 10. Components

Extend these before creating new ones.

| Component | Web | iOS | Notes |
| --- | --- | --- | --- |
| Decision pill | `DecisionPill.tsx`, `.pill-*` | decision views in `StationViews.swift` | Icon + label, one per station; Go now solid |
| Badge | `Badge.tsx` (`default`, `brand`, `warn`, `danger`) | inline capsule labels | Facts, not decisions |
| Station card | `StationCard.tsx` | `StationCard` in `StationViews.swift` | Name, charging price, decision, one caution, availability, power and distance. Web groups identical connector types and power with published quantities; full ports, parking, conditions, provenance and photos stay in Details |
| Provenance | `SourceProvenance.tsx` | detail trust rows | Plain source + freshness; combined for VoiceOver |
| Filters | `FilterRail.tsx`, `.filter-chip` | `DiscoveryFilters.swift` | Free shortcut, price slider (0–100 cents, then No limit), native modal/sheet, reset |
| Coverage and guidance | `CoverageBanner.tsx`, `FlowHint.tsx` | sheet banners | Bounded copy with a one-tap action, no transport diagnostics; coverage is one compact line ("Title. Message") |
| Icon button | `.icon-btn` (44px) | `VoltaPressButtonStyle` | Accessible name required |
| Action button | `bg-brand` / bordered utility buttons | `VoltaActionButtonStyle` in `StationActions.swift` | Ink primary, bordered secondary; peers share the row |
| Wrapping row | `flex-wrap` | `FlowLayout` in `Theme.swift` | Badges and facts wrap at any width or text size |
| Empty state | `VisualMoment.tsx` | `VisualMomentViews.swift` | Empty-state illustration |
| AI agent connection | `AgentConnection.tsx`, pinned beside Data honesty | `AIConnectionView.swift`, pinned discovery action | One tap copies the shared setup prompt. `Copy AI prompt` / `Paste into your AI chat` becomes `Prompt copied`; no setup modal. Copying does not establish a connection. An inline selectable prompt supports manual copying if clipboard access fails. |
| Owned EV | `VehiclePanel.tsx`, `VehicleEditor.tsx` | native vehicle garage | Opaque neutral drawer/sheet, car photo or labeled vehicle illustration, battery, energy, range, source age and planning target |

Icons: `lucide-react` on web, SF Symbols on iOS, stroke 2–2.6. Do not add another
icon set or use emoji.

### Owned vehicles

Vehicle imagery is functional: use the owner's chosen photo, or a clearly labeled
vehicle illustration. A generic render never claims to depict the connected trim.
Battery bars, charge targets and connection controls use neutral ink; green remains
exclusive to station evidence. Display manual, example, simulated, fresh provider and
stale provider readings distinctly. Source observation time, rather than fetch time,
determines whether telemetry is current. Unknown vehicle facts stay unknown.

The web drawer keeps actions pinned and its readings independently scrollable;
Escape closes it and returns focus to Your EV. It is nonmodal so station discovery
remains usable. Native uses a sheet and Dynamic Type. Both expose the owner's target
as a planning control only: it never sends a charging command to the vehicle.

Station details may show energy to that target, a theoretical minimum time using a
matching connector and the car's known power limit, and estimated euro charging cost
from a verified current per-kWh tariff. Cost assumes 10% charging losses and excludes
parking and session fees. Unknown compatibility or limits stay explicit; stale car
readings cannot produce a current time or cost estimate. Vehicle estimates never
upgrade station availability, evidence or route readiness.

## 11. Map

- Basemap: OpenFreeMap Positron (light) / Dark, with visible attribution. Light water
  and park paint come from `--map-water` and `--map-park`, desaturated so green pins
  stand out.
- Pins and clusters: neutral or `verify` by default; `go` only when route-ready. The
  selected station ring is ink, not green. Prices on the map require explicit price
  verification. Pin labels show only a price and optional short power hint.
  Never truncate connector or operator prose into a pin.
- Region shortcuts appear near Spain. Outside that area, offer one `Spain` shortcut
  back to mainland discovery without changing catalogue geography.
- Opening the app requests location once; a newer explicit discovery choice wins.
- If tiles or WebGL fail, the list, search and region shortcuts keep working.

## 12. Copy

- Plain, short, driver-facing sentences. Say what to do ("Check the charger screen
  before plugging in"), not how the backend works.
- Use the decision labels from section 2 verbatim; a more specific backend
  `decision.label` wins.
- Prices as `€0.35/kWh` via the helpers in `format.ts`; always label whether a figure
  is charging or parking.
- Label card tariffs `Charging price`, not `Charging`. Unknown prices say `Unknown`.
- Use short non-live copy, such as `Saved chargers · not live`. Keep stale and non-live
  boundaries explicit rather than repeating an explanation on every list.
- English UI copy uses no em dashes.
- Never claim availability, live status, or a price without evidence.
- iOS strings live in `Localizable.xcstrings` (English, Spanish, Russian); new static
  strings go there (a contract test checks this).
- Product name in UI is **Volta Go**; `Volta` stays the code and bundle identifier.

## 13. Before you ship UI

1. Use existing tokens, presets and components: no raw hex in components, no new
   timing constants, no green outside evidence and the logo mark.
2. Check section 2: could any state now look more trustworthy than its evidence?
3. Check section 8: 44px targets, equal heights in a row, equal widths for peer
   actions, one primary action, visible boundaries, focus ring.
4. Check section 9: did this add a tap, a dialog or a screen? Remove it if you can.
5. Render light and dark, desktop (1440px) and mobile (390px) for web; check the
   affected native screen at default and large Dynamic Type.
6. Run the focused checks from [README.md](../README.md): `npm --prefix frontend run test`
   and lint for web, `python3 -m unittest tests.test_ios_design_contract` for iOS
   source contracts, plus the browser contract scripts the change touches.
7. If you add a token, component or rule, update this file in the same change.

## Platform notes

- Web dark mode uses a light ink fill with dark labels for actions; iOS dark mode
  uses `#646F6A` with white labels (see section 3).
- iOS supports iOS 18 and later. Liquid Glass (`voltaGlass`, `voltaGlassGroup` in
  `Theme.swift`) applies only on iOS 26+; earlier versions get the opaque fallback.
- The native rebuild of 8 October 2026 was built and rendered on an iPhone SE (iOS 18.6) and
  an iPhone 17 Pro (iOS 27), light and dark, default and AX XXXL text.
