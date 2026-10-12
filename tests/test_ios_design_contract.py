import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios" / "Volta" / "Volta"


# Every P4.2 paywall, recovery, and in-app legal string. Keeping this explicit makes a
# missing locale a cheap source contract instead of something discovered in App Review.
PAYWALL_LOCALIZATION_KEYS = {
    "%@ is charged now and renews every %@ until cancelled. Cancel in App Store subscription settings.",
    "%@ until %@.",
    "%lld × %@",
    "Access is still syncing",
    "Access was not granted. Retry or restore through the App Store.",
    "Apple found no active Volta Plus subscription for this Apple ID.",
    "Apple is retrying the renewal. Update your App Store payment method, then restore purchases.",
    "Apple is waiting for approval. You can return later; access starts only after verification.",
    "Apple revoked this subscription after a refund. Choose a plan to subscribe again.",
    "Apple will show the final purchase confirmation",
    "Auto-renewal is off",
    "Billing grace period",
    "Checking App Store access…",
    "Checks this Apple ID for a verified Volta Plus subscription",
    "Choose a plan to start a new subscription, or restore if you renewed on another device.",
    "Choose chargers with evidence, not guesswork.",
    "Choose your plan",
    "City searches are relayed by the Volta service to Nominatim. The service may cache requests and results, and logs the client IP address for security and rate limiting.",
    "Configured Volta service",
    "Contacting App Store…",
    "Do not interact with Volta while driving. Follow road rules, posted parking conditions, and charger operator instructions.",
    "Eligible now: %@ free. Then %@ every %@ until cancelled. Eligibility is confirmed by the App Store. Cancel in App Store subscription settings.",
    "Evidence-checked charger database",
    "Honest live status",
    "Instructions before you drive",
    "Live, stale, directory, and community evidence never blur together.",
    "Loading App Store plans",
    "Loading App Store plans…",
    "MapKit loads map content from Apple. Charger details may show optional photos from the attributed third-party source named in the app.",
    "Maps and charger photos",
    "No Volta account. Your subscription follows the Apple ID used for App Store purchases.",
    "No active subscription found",
    "No price or plan was guessed. Retry the App Store catalog or restore an existing purchase.",
    "Nothing was charged.",
    "Older StoreKit versions cannot distinguish every revocation reason. This subscription no longer grants access; restore purchases or choose a plan.",
    "On-device preferences",
    "One subscription unlocks the cleaned national database and the complete decision experience. Charger safety labels and warnings always stay honest.",
    "Payment needs attention",
    "Contact and control",
    "Using Volta",
    "Price, parking, connector, and source facts stay in one decision.",
    "Privacy",
    "Public sources cleaned and matched, with verification shown only where evidence supports it.",
    "Purchase cancelled",
    "Purchase could not be verified",
    "Purchase not completed",
    "Purchase pending",
    "Purchase refunded",
    "Purchases restored",
    "Responsible use",
    "Restore failed",
    "Restore purchases",
    "Restoring purchases…",
    "Retry",
    "Search relay and service logs",
    "Save %@",
    "Selected",
    "Selects this subscription plan",
    "Start %@ free",
    "Step %lld: %@. %@",
    "Subscribe for %@",
    "Subscription active",
    "Subscription expired",
    "Subscription revoked or refunded",
    "Subscriptions",
    "Subscriptions and payments",
    "Subscriptions are temporarily unavailable",
    "Terms",
    "Terms of Use",
    "The App Store could not complete the purchase. Please try again.",
    "The App Store could not sync purchases. Check your connection and try again.",
    "The App Store did not return the complete Volta Plus catalog.",
    "The App Store transaction could not be verified. Access was not granted.",
    "The App Store will show the full terms before purchase. Cancel in App Store subscription settings.",
    "The purchase was verified, but access is not available yet. Restore purchases or try again.",
    "These terms apply when you use Volta. Availability, price, parking, and access can change; verify the public source and onsite conditions before routing or charging.",
    "Questions about privacy, access, or deletion can be sent through Volta Support. You can stop location sharing in iOS Settings and remove on-device preferences by deleting the app.",
    "StoreKit and the App Store handle Volta Plus subscriptions, payments, renewals, restoration, and refunds. Volta does not receive payment details.",
    "Volta Plus",
    "Volta Plus is account-less and tied to the Apple ID used for App Store purchases. Apple handles payment, renewal, cancellation, restoration, and refunds.",
    "Volta Plus is ready.",
    "Volta helps you compare public charger evidence. Availability, price, parking, and access can change; verify the station before routing or charging.",
    "Volta sends searches and visible map areas to the configured Volta service when you search or move the map. After location permission is granted, precise coordinates are also sent when the app opens and when you ask to find or rank nearby chargers.",
    "Volta stores normal app preferences on this device. The app contains no advertising or third-party analytics SDK.",
    "What Volta provides",
    "Your access continues through the current paid period",
    "Your access continues while Apple retries payment. Review your App Store payment method",
    "Your purchase was verified. Restore purchases or try again in a moment.",
    "Your verified subscription is active.",
}


# Trust/status and public-source metadata are driver-facing too. These keys are kept
# separate from the paywall set so a source-evidence change cannot silently fall back to
# English in es/ru just because the Xcode extractor has not refreshed the checked-in
# catalog yet.
TRUST_SOURCE_LOCALIZATION_KEYS = {
    "Attribution: %@",
    "Fresh live status is available; cached rows remain marked stale.",
    "License: %@",
    "Live + cached",
    "Refresh all live status",
    "Retrieved: %@",
    "Source updated: %@",
    "Transformation: %@",
}


class IOSDesignContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_view = (IOS / "RootView.swift").read_text(encoding="utf-8")
        cls.sheet_view = (IOS / "SheetView.swift").read_text(encoding="utf-8")
        cls.station_views = (IOS / "StationViews.swift").read_text(encoding="utf-8")
        cls.discovery_controls = (IOS / "DiscoveryControls.swift").read_text(encoding="utf-8")
        cls.discovery_filters = (IOS / "DiscoveryFilters.swift").read_text(encoding="utf-8")
        cls.station_actions = (IOS / "StationActions.swift").read_text(encoding="utf-8")
        cls.station_presentation = (IOS / "StationPresentation.swift").read_text(encoding="utf-8")
        cls.theme = (IOS / "Theme.swift").read_text(encoding="utf-8")
        cls.moments = (IOS / "VisualMomentViews.swift").read_text(encoding="utf-8")
        cls.app_state = (IOS / "AppState.swift").read_text(encoding="utf-8")
        cls.models = (IOS / "Models.swift").read_text(encoding="utf-8")
        cls.settings_view = (IOS / "SettingsView.swift").read_text(encoding="utf-8")
        cls.volta_app = (IOS / "VoltaApp.swift").read_text(encoding="utf-8")
        cls.paywall_config = (IOS / "PaywallConfig.swift").read_text(encoding="utf-8")
        cls.subscription_manager = (IOS / "SubscriptionManager.swift").read_text(encoding="utf-8")
        paywall_path = IOS / "PaywallView.swift"
        cls.paywall_view = paywall_path.read_text(encoding="utf-8") if paywall_path.exists() else ""
        presentation_path = IOS / "PaywallPresentation.swift"
        cls.paywall_presentation = presentation_path.read_text(encoding="utf-8") if presentation_path.exists() else ""
        cls.xcstrings_path = IOS / "Localizable.xcstrings"
        cls.chrome = cls.discovery_controls + cls.sheet_view + cls.moments

    def test_map_annotations_are_native_named_buttons(self):
        self.assertRegex(
            self.root_view,
            re.compile(r'Annotation\("", coordinate: cluster\.coordinate\) \{\s+Button \{', re.S),
        )
        self.assertRegex(
            self.root_view,
            re.compile(r'Annotation\("", coordinate: st\.coordinate\) \{\s+Button \{', re.S),
        )
        self.assertNotIn(".onTapGesture", self.root_view)
        # Nearby chargers merge into a counted cluster; none is silently dropped.
        self.assertIn("if group.count == 1 { return .station(group[0]) }", self.root_view)
        self.assertIn("return .cluster(MapCluster(stations: group))", self.root_view)
        for phrase in (
            '.accessibilityLabel("Charger cluster")',
            '.accessibilityLabel(st.name)',
            '.accessibilityHint("Opens this charger on the map")',
        ):
            self.assertIn(phrase, self.root_view)

    def test_primary_controls_keep_names_and_44_point_targets(self):
        # Locate, Settings and the route-ready dismiss are square 44-point icon buttons.
        self.assertGreaterEqual(self.chrome.count(".frame(width: 44, height: 44)"), 4)
        for phrase in (
            '"Find my location"',
            '.accessibilityLabel("Settings")',
            '.accessibilityLabel("Search this area")',
            '.accessibilityLabel("Close search")',
            '.accessibilityLabel("Clear search text")',
            '.accessibilityLabel("Dismiss route-ready notice")',
        ):
            self.assertIn(phrase, self.chrome)

    def test_station_actions_meet_the_44_point_minimum_and_share_the_row(self):
        """A chosen station opens in the sheet (no floating map card); its peer actions
        keep 44-point targets, split the row evenly and stack at accessibility sizes."""
        self.assertNotIn("struct MapPinCard", self.root_view)
        self.assertEqual(self.station_actions.count(".frame(maxWidth: .infinity, minHeight: 44)"), 3)
        self.assertIn("ViewThatFits(in: .horizontal)", self.station_actions)
        self.assertIn("struct VoltaActionButtonStyle: ButtonStyle", self.station_actions)
        self.assertIn("prominent ? Theme.action : Theme.surface", self.station_actions)
        self.assertIn("Theme.hairlineStrong", self.station_actions)

    def test_map_prices_require_explicit_price_verification_and_annotation_targets(self):
        """Exact OSM/directory locations may be pinned, but their source price is not a
        map price or VoiceOver tariff until verification.price_verified is explicit."""
        self.assertIn("var hasVerifiedPrice: Bool", self.models)
        self.assertIn("verification?.priceVerified == true", self.models)
        self.assertIn("if station.hasVerifiedPrice { parts.append(station.priceText) }", self.root_view)
        price_pill = self.root_view[self.root_view.index("struct PricePill"):]
        self.assertIn("if station.hasVerifiedPrice {", price_pill)
        self.assertIn(".frame(minWidth: 44, minHeight: 44)", price_pill)
        # Green fill on the map belongs to route-ready chargers only.
        self.assertIn("station.isRouteReady ? Theme.go : Theme.surface", price_pill)
        cluster_bubble = self.root_view[self.root_view.index("struct ClusterBubble"):self.root_view.index("struct PricePill")]
        self.assertIn(".frame(minWidth: 44, minHeight: 44)", cluster_bubble)
        self.assertIn("cluster.hasRouteReady ? Theme.go", cluster_bubble)

    def test_plain_controls_have_press_feedback_and_filter_selection_semantics(self):
        self.assertIn("struct VoltaPressButtonStyle: ButtonStyle", self.theme)
        self.assertIn("configuration.isPressed", self.theme)
        self.assertIn("accessibilityReduceMotion", self.theme)
        self.assertIn("filterFeedback.toggle()", self.discovery_filters)
        self.assertIn(".accessibilityValue(title)", self.discovery_filters)
        self.assertIn(".accessibilityAddTraits(state.freeOnly ? .isSelected : [])", self.discovery_filters)
        self.assertIn(".accessibilityAddTraits(state.focusedStationID == st.id ? .isSelected : [])", self.sheet_view)
        self.assertIn(".accessibilityAddTraits(state.focusedStationID == st.id ? .isSelected : [])", self.root_view)
        self.assertRegex(
            self.sheet_view,
            re.compile(r'if state\.liveState == \.failed \{\s+Button \{', re.S),
        )
        # Selected chips turn ink (never green) with a checkmark in a reserved slot.
        self.assertIn("active ? Theme.action : Theme.surface", self.discovery_filters)
        self.assertIn('Image(systemName: "checkmark")', self.discovery_filters)
        self.assertNotIn("Theme.go", self.discovery_filters)

    def test_discovery_entry_and_settings_do_not_require_a_brand_reveal(self):
        self.assertIn("DiscoveryMapControls()", self.root_view)
        self.assertIn('Label("Volta Go", systemImage: "bolt.fill")', self.discovery_controls)
        self.assertNotIn("controlsExpanded", self.root_view + self.discovery_controls)
        self.assertNotIn("brandLauncherButton", self.root_view + self.discovery_controls)
        self.assertIn("selectedDetent: PresentationDetent = .medium", self.root_view)
        # Search and Settings share the pinned row at the top of the sheet: one Settings
        # entry, visible at every detent, never hidden by panning the map.
        search_row = self.sheet_view.split("private var searchRow: some View", 1)[1].split("// MARK: - Place search", 1)[0]
        self.assertIn('TextField("Search a city…", text: $state.searchText)', search_row)
        self.assertIn('.accessibilityIdentifier("discoverySearchField")', search_row)
        self.assertIn("Button(action: onOpenSettings)", search_row)
        self.assertIn('.accessibilityIdentifier("settingsButton")', search_row)
        self.assertNotIn("onOpenSettings", self.discovery_controls)
        self.assertIn("MapFilterBar()", self.sheet_view)
        heading = self.sheet_view.split("private var resultsHeading: some View", 1)[1].split("private var statusRows", 1)[0]
        self.assertIn('Button("Clear filters") { state.clearFilters() }', heading)
        self.assertIn("if state.hasActiveFilters", heading)

    def test_map_filters_keep_native_sliders_multiselect_and_visible_sheet_entry(self):
        self.assertIn("Slider(", self.discovery_filters)
        self.assertGreaterEqual(self.discovery_filters.count("FilterSliderEditor("), 2)
        self.assertIn("state.connectorTypes", self.discovery_filters)
        self.assertIn("state.toggleConnectorType", self.discovery_filters)
        self.assertIn(".accessibilityValue(valueText(draft))", self.discovery_filters)
        self.assertIn(".frame(minHeight: 44)", self.discovery_filters)
        self.assertIn("MapFilterBar()", self.sheet_view)
        self.assertRegex(
            self.root_view,
            re.compile(r'onMapCameraChange\(frequency: \.onEnd\).*?state\.visibleRegion = ctx\.region', re.S),
        )
        self.assertIn("@Published var connectorTypes: Set<String> = []", self.app_state)
        self.assertIn("connectorTypes.contains", self.app_state)

    def test_station_list_uses_native_sheet_and_every_in_view_row(self):
        # The collapsed detent is measured from the search row, so it shows exactly it.
        self.assertIn("private var peekDetent: PresentationDetent { .height(peekHeight) }", self.root_view)
        self.assertIn(".presentationBackground(Theme.canvas)", self.root_view)
        self.assertIn("List {", self.sheet_view)
        self.assertIn("ForEach(state.listRows) { st in", self.sheet_view)
        self.assertNotIn("ScrollView(.horizontal", self.sheet_view)
        self.assertNotIn("state.listRows.prefix", self.sheet_view)
        self.assertIn("onSelect(st)", self.sheet_view)
        self.assertIn("selected = station", self.app_state)
        self.assertIn(".navigationDestination(item: $state.selected)", self.sheet_view)
        self.assertIn('accessibilityIdentifier("stationRow-\\(st.id)")', self.sheet_view)
        self.assertNotIn(".presentationCornerRadius(28)", self.root_view)
        self.assertNotIn("Rectangle()\n                .fill(station.decisionKind.color)\n                .frame(width: 5)", self.sheet_view)
        self.assertNotIn(".shadow(", self.station_views.split("struct StationCard", 1)[1].split("struct StationCardSkeleton", 1)[0])
        self.assertIn("StationCard(station: st, selected: state.focusedStationID == st.id)", self.sheet_view)
        self.assertIn("selected ? Theme.selectedSurface : Theme.surface", self.theme)

    def test_motion_respects_the_system_reduce_motion_setting(self):
        self.assertIn("accessibilityReduceMotion", self.root_view)
        self.assertIn("reduceMotion ? VoltaMotion.state : VoltaMotion.spatial", self.root_view)
        self.assertIn("reduceMotion ? nil : VoltaMotion.state", self.discovery_filters)
        self.assertIn("state.locating && !reduceMotion", self.discovery_controls)
        self.assertIn("enum VoltaMotion", self.theme)
        self.assertNotIn("duration: 1.15", self.moments)
        self.assertIn("reduceMotion ? .opacity", self.root_view)
        # No particles or confetti: the route-ready moment is a quiet notice (section 1.5).
        self.assertNotIn("Particles", self.moments)
        self.assertIn("UIAccessibility.isReduceMotionEnabled", self.app_state)

    def test_map_control_material_has_transparency_and_contrast_fallbacks(self):
        # Liquid Glass only on iOS 26+, only for map chrome, with an opaque fallback.
        self.assertIn("accessibilityReduceTransparency", self.theme)
        self.assertIn("colorSchemeContrast", self.theme)
        self.assertIn("if #available(iOS 26, *), !reduceTransparency, contrast != .increased", self.theme)
        self.assertIn(".background(Theme.surface, in: shape)", self.theme)
        self.assertIn("GlassEffectContainer", self.theme)
        self.assertIn(".voltaGlass(in:", self.discovery_controls)
        self.assertIn(".voltaGlassGroup(", self.discovery_controls)
        for content in (self.sheet_view, self.station_views, self.station_actions):
            self.assertNotIn("glassEffect", content)
            self.assertNotIn(".voltaGlass(", content)

    def test_accessibility_dynamic_type_uses_adaptive_header_and_large_sheet(self):
        self.assertIn("@Environment(\\.dynamicTypeSize)", self.root_view)
        self.assertIn("dynamicTypeSize.isAccessibilitySize ? [.medium, .large]", self.root_view)
        self.assertIn("selectedDetent = .large", self.root_view)
        self.assertIn(".fixedSize(horizontal: false, vertical: true)", self.sheet_view)
        self.assertGreaterEqual(self.station_views.count("dynamicTypeSize.isAccessibilitySize"), 3)
        # Facts and badges wrap onto as many lines as the text size needs.
        self.assertIn("struct FlowLayout: Layout", self.theme)
        self.assertIn("FlowLayout(spacing: 12, lineSpacing: 6) {\n            availabilityFacts\n            routeFacts", self.station_views)
        self.assertIn(".labelStyle(.titleAndIcon)", self.station_views)
        self.assertIn("if dynamicTypeSize.isAccessibilitySize", self.station_actions)
        self.assertIn("VStack(spacing: 10) { orderedActions(icons: true) }", self.station_actions)
        self.assertIn(".fixedSize(horizontal: false, vertical: true)", self.station_actions)
        # Floating map chrome stops growing before it would cover the map.
        self.assertIn(".dynamicTypeSize(...DynamicTypeSize.accessibility1)", self.root_view)

    def test_station_action_priority_follows_route_readiness_on_map_and_detail(self):
        self.assertIn("StationActions(station: station)", self.station_views)
        self.assertRegex(
            self.station_actions,
            re.compile(
                r"if station\.isRouteReady \{\s+mapsAction\(prominent: true, icons: icons\)\s+"
                r"sourceAction\(prominent: false, icons: icons\)\s+\} else \{\s+"
                r"sourceAction\(prominent: true, icons: icons\)\s+mapsAction\(prominent: false, icons: icons\)",
                re.S,
            ),
        )
        self.assertIn("if let url = station.sourceURL", self.station_actions)
        self.assertIn("if let url = station.mapsURL", self.station_actions)
        self.assertIn("verify the station before driving", self.station_actions)
        self.assertIn("if !station.isRouteReady", self.station_views)
        self.assertIn("StationParkingAccessFacts(station: station)", self.station_views)
        # One decision pill per station; only route-ready Go now is a solid fill.
        self.assertIn("let solid = kind == .go && station.isRouteReady", self.station_views)

    def test_chargers_response_drops_coordless_rows_instead_of_crashing_the_whole_list(self):
        """CORR-07: server.py deliberately keeps some leads coord-less (keyed by name only,
        no lat/lon -- see server.py's _normalize_lead/enrich_station, which never backfill
        lat/lon). Station.lat/Station.lon stay non-optional Double (unchanged -- ~40 call
        sites across the app read them as plain Doubles), so ChargersResponse must decode
        `stations`/`leads` leniently at the ARRAY boundary: one malformed/coord-less element
        must be silently dropped, never abort the whole array decode and blank the list."""
        self.assertIn("@propertyWrapper", self.models)
        self.assertIn("struct LossyArray<Element: Decodable>: Decodable", self.models)
        # The per-element wrapper must never itself throw, or the unkeyed container's cursor
        # could stall on the very element it exists to skip.
        self.assertIn("value = try? container.decode(Element.self)", self.models)
        # Station identity/coords stay required Doubles -- this fix is array-boundary leniency
        # (option b), not optional coords on Station (option a).
        self.assertIn("var id: String", self.models)
        self.assertIn("var lat: Double", self.models)
        self.assertIn("var lon: Double", self.models)
        # ChargersResponse.stations/.leads use the lenient wrapper, not a plain [Station].
        self.assertIn("@LossyArray var stations: [Station]", self.models)
        self.assertIn("@LossyArray var leads: [Station]", self.models)
        self.assertNotRegex(self.models, re.compile(r"(?<!@LossyArray )var (stations|leads): \[Station\]"))

    def test_models_declares_a_claim_type_with_charge_and_parking_fields_on_station(self):
        """P3.3: server.py's ensure_claim_contract() (server.py ~750-795) ALWAYS attaches BOTH
        charge_claim and parking_claim to every station/lead row -- charging-PRICE provenance
        and parking-COST provenance are tracked SEPARATELY (AGENTS.md Prime Directive #1).
        Before this unit, Station had neither field, so JSONDecoder silently dropped both keys.
        """
        self.assertIn("struct Claim: Decodable, Hashable", self.models)
        self.assertIn("var chargeClaim: Claim?", self.models)
        self.assertIn("var parkingClaim: Claim?", self.models)
        # The pre-existing conflict summary field stays untouched by this addition.
        self.assertIn("struct ClaimConflict: Decodable, Hashable", self.models)

    def test_charge_and_parking_claim_blocks_are_distinct_and_accessible(self):
        """StationDetailView must render two visually distinct "Trust & provenance" blocks --
        one per claim, each with its OWN source/link -- mirroring
        frontend/src/components/SourceProvenance.tsx's "Charging claim" / "Parking claim"
        blocks, and must never merge charge and parking provenance into one. New rows are born
        accessible so VoiceOver reads each claim as one element, not fragmented."""
        self.assertIn('"Charging claim"', self.station_views)
        self.assertIn('"Parking claim"', self.station_views)
        self.assertIn("station.chargeClaim", self.station_views)
        self.assertIn("station.parkingClaim", self.station_views)
        self.assertIn(
            "private func claimRow(icon: String, label: String, claim: Claim) -> some View",
            self.station_views,
        )
        self.assertIn(".accessibilityElement(children: .combine)", self.station_views)
        # Each claim keeps its own source label/link -- never a shared/merged one.
        self.assertIn("claim.sourceLabel", self.station_views)
        self.assertIn("claim.sourceURL", self.station_views)

    def test_row_card_shows_freshness_fact_gated_on_presence_not_lead_status(self):
        """DIR-03: the ROW card (StationRow/AvailabilityLine) must show evidence age / live
        freshness for BOTH live and lead rows, gated only on freshnessText's presence --
        matching the web's `st.last_seen || st.freshness_minutes != null` gate
        (frontend/src/components/StationCard.tsx), never gated on isLead."""
        self.assertIn("var freshnessRowText: String?", self.models)
        self.assertIn("showFreshness", self.station_views)
        self.assertIn("AvailabilityLine(station: station, showFreshness: true)", self.station_views)
        self.assertIn("station.freshnessRowText", self.station_views)

    def test_overview_does_not_restore_removed_dashboard_headings_or_lead_totals(self):
        """The native discovery list keeps non-live evidence labeled beside each result."""
        self.assertNotIn("BEST OPTIONS NOW", self.sheet_view)
        self.assertNotIn("chargers in view", self.sheet_view)
        self.assertIn('dot(.secondary, String(localized: "Not live"))', self.station_views)
        self.assertIn("AvailabilityLine(station: station, showFreshness: true)", self.station_views)

    def test_haptics_are_sparing_and_present_at_named_moments(self):
        """P3.4 / GOAL_CONTRACT.md Section 5: 'light impact on selection, success notification
        on route-ready confirmation -- sparing, never decorative.' Four deliberate call
        sites cover map pin/cluster, top filter selection, station ticket, and route-ready."""
        # Map pin AND cluster tap share one .selection trigger on the always-mounted Map, not
        # a per-annotation one (pins/clusters get rebuilt on almost every pan/zoom, and
        # .sensoryFeedback never fires on a freshly-mounted view's own initial value).
        self.assertIn(".sensoryFeedback(.selection, trigger: mapTapFeedback)", self.root_view)
        self.assertEqual(self.root_view.count("mapTapFeedback.toggle()"), 2)

        # Station row tap (SheetView owns the Button that wraps StationRow).
        self.assertIn(".sensoryFeedback(.selection, trigger: rowTapFeedback)", self.sheet_view)
        self.assertIn("rowTapFeedback.toggle()", self.sheet_view)

        # Route-ready SUCCESS notification -- the one item Section 5 names explicitly.
        self.assertIn(".sensoryFeedback(.success, trigger: appeared)", self.moments)
        self.assertRegex(
            self.moments,
            re.compile(r"struct RouteReadyMomentView: View \{.*?\.sensoryFeedback\(\.success", re.S),
        )

        # Sparing: no haptic on locate/settings/search-area/clear-search/dismiss chrome, and
        # no haptic on locate/settings/search-area/close-search/dismiss chrome.
        total_calls = (
            self.root_view.count(".sensoryFeedback(")
            + self.discovery_filters.count(".sensoryFeedback(")
            + self.discovery_controls.count(".sensoryFeedback(")
            + self.sheet_view.count(".sensoryFeedback(")
            + self.moments.count(".sensoryFeedback(")
            + self.station_views.count(".sensoryFeedback(")
        )
        self.assertEqual(total_calls, 4)
        self.assertEqual(self.sheet_view.count(".sensoryFeedback("), 1)
        self.assertEqual(self.root_view.count(".sensoryFeedback("), 1)
        self.assertEqual(self.discovery_filters.count(".sensoryFeedback("), 1)
        self.assertIn(".sensoryFeedback(.selection, trigger: filterFeedback)", self.discovery_filters)
        self.assertEqual(self.discovery_controls.count(".sensoryFeedback("), 0)
        self.assertEqual(self.moments.count(".sensoryFeedback("), 1)

    def test_detail_trust_rows_and_connector_rows_are_combined_for_voiceover(self):
        """StationDetailView's row(icon:title:detail:) helper (verification/corroboration/
        parking/access/claim-conflict/freshness rows) and the connector-list rows previously
        had NO grouping, so VoiceOver fragmented each into 2-3 stops and could announce the
        decorative SF Symbol/status-dot on its own. Both must now read as ONE combined
        element each, matching the P3.3 claimRow precedent (D-159/D-160/TC-121)."""
        row_fn = re.search(
            r"private func row\(icon: String, title: String, detail: String\?, "
            r"tint: Color = \.secondary\) -> some View \{(.*?)\n    \}\n\}",
            self.station_views,
            re.S,
        )
        self.assertIsNotNone(row_fn, "row(icon:title:detail:) helper not found")
        self.assertIn(".accessibilityElement(children: .combine)", row_fn.group(1))

        connectors_section = re.search(
            r'Section\("Connectors"\) \{(.*?)\n                \}\n            \}',
            self.station_views,
            re.S,
        )
        self.assertIsNotNone(connectors_section, "Connectors section not found")
        self.assertIn(".accessibilityElement(children: .combine)", connectors_section.group(1))

        # Three independent .combine call sites now: the pre-existing claimRow (P3.3), the
        # connector rows, and the row() helper -- a regression that drops any of them would
        # silently re-fragment VoiceOver traversal on the Detail screen.
        self.assertGreaterEqual(
            self.station_views.count(".accessibilityElement(children: .combine)"), 3
        )

    def test_settings_view_has_accessible_presets_field_and_test_button(self):
        """SettingsView.swift had ZERO accessibility annotations before this unit. The preset
        buttons, the manual-entry backend-address field, and the test-connection button must
        all be labeled/hinted, and the currently-selected preset must be unmistakable to
        VoiceOver via .isSelected -- the checkmark glyph alone carries no accessible signal."""
        self.assertIn(
            '.accessibilityLabel(Text(verbatim: "Backend server address"))',
            self.settings_view,
        )
        self.assertIn(
            'prompt: Text(verbatim: "http://host:8773")', self.settings_view
        )
        self.assertIn(
            '.accessibilityHint(Text(verbatim: "Sets the backend address to \\(preset.url)"))',
            self.settings_view,
        )
        self.assertIn(
            ".accessibilityAddTraits(urlText == preset.url ? .isSelected : [])",
            self.settings_view,
        )
        self.assertIn(
            '.accessibilityLabel(Text(verbatim: testing ? "Testing connection" : "Test connection"))',
            self.settings_view,
        )
        self.assertGreaterEqual(self.settings_view.count(".accessibilityLabel"), 3)
        self.assertGreaterEqual(self.settings_view.count(".accessibilityHint"), 3)

    def test_cold_launch_signpost_instruments_app_start_to_first_content(self):
        """P3.6 / GOAL_CONTRACT.md Section 5 performance bar: 'Cold launch to first
        meaningful content < 1.5s ... measure with signposts; record the number.'
        AppState carries an OSSignposter interval from construction (app start,
        the earliest hook available without a custom AppDelegate/main.swift) to the
        FIRST successful loadOverview() (first real content ready to render) --
        pins the exact call sites so a future refactor can't silently drop the
        instrumentation, and pins that the interval ends at most once (a later
        reload -- baseURLChanged, a retry -- must never re-fire it and corrupt the
        measurement)."""
        self.assertIn(
            'OSSignposter(subsystem: "tech.arsik.volta", category: "ColdLaunch")',
            self.app_state,
        )
        self.assertIn('beginInterval("ColdLaunchToFirstContent")', self.app_state)
        self.assertIn('endInterval("ColdLaunchToFirstContent"', self.app_state)
        self.assertIn("private func recordFirstMeaningfulContentIfNeeded()", self.app_state)
        self.assertIn("guard !didRecordFirstMeaningfulContent else { return }", self.app_state)
        self.assertIn("didRecordFirstMeaningfulContent = true", self.app_state)
        # The interval must end inside loadOverview()'s SUCCESS path only -- ending on
        # failure would claim "content ready" when the fail-closed path showed nothing.
        load_overview_fn = re.search(
            r"func loadOverview\(\) async \{(.*?)\n    \}",
            self.app_state,
            re.S,
        )
        self.assertIsNotNone(load_overview_fn, "loadOverview() not found")
        body = load_overview_fn.group(1)
        self.assertIn("recordFirstMeaningfulContentIfNeeded()", body)
        do_block, _, catch_block = body.partition("} catch {")
        self.assertIn("recordFirstMeaningfulContentIfNeeded()", do_block)
        self.assertNotIn("recordFirstMeaningfulContentIfNeeded()", catch_block)

    def test_localizable_string_catalog_covers_es_and_ru(self):
        """P3.5: Localizable.xcstrings is a String Catalog (extract-from-source model -- the
        Swift literals above stay the catalog keys, byte-identical) covering en/es/ru. Cheap
        regression guard: the catalog exists, is valid JSON, is wired into the project's known
        regions, and a sample of driver-facing/honesty-critical keys (decision labels, the
        "Not live" honesty marker, the two inflect(:true) plural strings) carry non-empty es
        and ru translations -- draft quality (owner/native-speaker sign-off deferred to P5),
        but present and structurally correct."""
        self.assertTrue(self.xcstrings_path.exists(), "ios/Volta/Volta/Localizable.xcstrings is missing")
        catalog = json.loads(self.xcstrings_path.read_text(encoding="utf-8"))
        self.assertEqual(catalog.get("sourceLanguage"), "en")

        pbxproj = (ROOT / "ios" / "Volta" / "Volta.xcodeproj" / "project.pbxproj").read_text(encoding="utf-8")
        known_regions = re.search(r"knownRegions = \((.*?)\);", pbxproj, re.S)
        self.assertIsNotNone(known_regions, "knownRegions not found in project.pbxproj")
        for region in ("es", "ru"):
            self.assertIn(region, known_regions.group(1))

        strings = catalog.get("strings", {})

        def locs(key):
            self.assertIn(key, strings, f"{key!r} missing from Localizable.xcstrings")
            return strings[key]["localizations"]

        # Flat (non-plural) honesty-critical keys: decision tiers and the "Not live" marker
        # that distinguishes a lead from a live row (AGENTS.md Prime Directive #1).
        for key in ("Go now", "Check first", "Verify first", "Not live", "Free"):
            localizations = locs(key)
            for locale in ("es", "ru"):
                self.assertIn(locale, localizations, f"{key!r} has no {locale} localization")
                value = localizations[locale]["stringUnit"]["value"]
                self.assertTrue(value.strip(), f"{key!r} has an empty {locale} translation")

        # The two pre-existing inflect(:true) plural strings need real plural variations: en
        # one/other, es one/other, ru one/few/many/other -- and EVERY locale's translated
        # VALUES (en included) must NOT re-embed the "^[...](inflect: true)" markdown wrapper
        # (empirically confirmed via simulator screenshot to render as literal on-screen text,
        # e.g. "^[809 cargadores](inflect: true) a la vista", when the wrapper is duplicated
        # into a bucket value -- the wrapper belongs only in the KEY, which Swift's own
        # compiler derives from source). P3.6 caught this exact bug live in the DEFAULT/en
        # locale (the primary, always-visible screen) because this guard originally checked
        # es/ru only -- en is included here so that gap can never reopen silently.
        plural_keys = {
            "^[%lld charger](inflect: true) in view": {
                "en": {"one", "other"}, "es": {"one", "other"}, "ru": {"one", "few", "many", "other"},
            },
            "^[%lld port](inflect: true)": {
                "en": {"one", "other"}, "es": {"one", "other"}, "ru": {"one", "few", "many", "other"},
            },
        }
        for key, locale_buckets in plural_keys.items():
            localizations = locs(key)
            for locale, buckets in locale_buckets.items():
                self.assertIn(locale, localizations, f"{key!r} has no {locale} localization")
                variations = localizations[locale].get("variations", {}).get("plural", {})
                self.assertEqual(
                    set(variations.keys()), buckets,
                    f"{key!r}[{locale}] plural buckets {set(variations.keys())} != expected {buckets}",
                )
                for bucket, unit in variations.items():
                    value = unit["stringUnit"]["value"]
                    self.assertTrue(value.strip(), f"{key!r}[{locale}][{bucket}] is empty")
                    self.assertNotIn(
                        "(inflect: true)", value,
                        f"{key!r}[{locale}][{bucket}] re-embeds the inflect markdown wrapper in the "
                        "TRANSLATED value -- renders as literal text on screen instead of resolving",
                    )

        # P4.2 deliberately checks the WHOLE catalog, not another sample. Any newly extracted
        # paywall/recovery/legal copy must ship in all supported locales, and interpolated
        # translations must preserve the source placeholder types even when positional syntax
        # (`%1$@`) is needed for a natural word order.
        def translated_values(node):
            if isinstance(node, dict):
                for name, value in node.items():
                    if name == "value" and isinstance(value, str):
                        yield value
                    else:
                        yield from translated_values(value)
            elif isinstance(node, list):
                for value in node:
                    yield from translated_values(value)

        for key, entry in strings.items():
            localizations = entry.get("localizations", {})
            for locale in ("en", "es", "ru"):
                self.assertIn(locale, localizations, f"{key!r} has no {locale} localization")
                values = list(translated_values(localizations[locale]))
                self.assertTrue(values, f"{key!r}[{locale}] has no translated values")
                self.assertTrue(all(value.strip() for value in values), f"{key!r}[{locale}] has an empty value")

        self.assertEqual(len(PAYWALL_LOCALIZATION_KEYS), 89)
        self.assertTrue(PAYWALL_LOCALIZATION_KEYS.issubset(strings))
        self.assertTrue(TRUST_SOURCE_LOCALIZATION_KEYS.issubset(strings))
        self.assertTrue({"", "%lld"}.isdisjoint(strings))
        sheet_status_keys = {"%lld leads", "%lld live · %lld leads"}
        self.assertTrue(sheet_status_keys.issubset(strings))
        placeholder_pattern = re.compile(r"%(?:\d+\$)?(lld|@)")
        for key in PAYWALL_LOCALIZATION_KEYS | TRUST_SOURCE_LOCALIZATION_KEYS | sheet_status_keys:
            entry = strings[key]
            self.assertEqual(entry.get("extractionState"), "manual")
            source_placeholders = sorted(placeholder_pattern.findall(key))
            for locale in ("en", "es", "ru"):
                value = entry["localizations"][locale]["stringUnit"]["value"]
                self.assertEqual(
                    sorted(placeholder_pattern.findall(value)),
                    source_placeholders,
                    f"{key!r}[{locale}] changed its interpolation contract",
                )

    def test_static_swiftui_literals_are_present_in_the_string_catalog(self):
        """A checked-in String Catalog can lag source extraction. Guard direct SwiftUI
        literals across the native app so a newly added visible label, button, section,
        navigation title, or accessibility phrase cannot ship as English-only by omission.
        DEBUG-only operational copy is deliberately excluded because Release localization
        resources must not contain private server-configuration language.
        Interpolated trust/source keys are covered explicitly above because their catalog
        placeholders depend on the interpolated Swift type."""
        strings = json.loads(self.xcstrings_path.read_text(encoding="utf-8")).get("strings", {})
        call_pattern = re.compile(
            r"(?:Text|Label|Button|Section|Link|String\(localized:|"
            r"\.navigationTitle|\.accessibilityLabel|\.accessibilityHint)"
            r"\(\s*\"((?:\\.|[^\"\\])*)\""
        )

        def release_visible_source(source):
            output = []
            stack = []
            active = True
            for line in source.splitlines():
                directive = line.strip()
                if directive == "#if DEBUG":
                    stack.append((active, True))
                    active = False
                    continue
                if directive.startswith("#if "):
                    stack.append((active, False))
                    continue
                if directive == "#else" and stack:
                    parent_active, is_debug = stack[-1]
                    if is_debug:
                        active = parent_active
                    continue
                if directive == "#endif" and stack:
                    active, _ = stack.pop()
                    continue
                if active:
                    output.append(line)
            return "\n".join(output)

        missing = set()
        for path in IOS.glob("*.swift"):
            source = release_visible_source(path.read_text(encoding="utf-8"))
            for raw_literal in call_pattern.findall(source):
                if "\\(" in raw_literal:
                    continue
                literal = (
                    raw_literal.replace(r'\\"', '"')
                    .replace(r"\\n", "\n")
                    .replace(r"\\\\", "\\")
                )
                if literal not in strings:
                    missing.add(f"{path.name}: {literal}")
        self.assertFalse(
            missing,
            "Static driver-facing SwiftUI literals missing from Localizable.xcstrings:\n"
            + "\n".join(sorted(missing)),
        )

    def test_paywall_is_default_off_and_release_has_no_override_surface(self):
        """P4.2: the friends-and-family build remains fully free by a literal source gate.
        UI-test launch arguments may force presentation only in a DEBUG compilation block;
        Release evaluates the literal gate directly and contains no argument lookup."""
        self.assertIn("static let enabled = false", self.paywall_config)
        self.assertRegex(
            self.volta_app,
            re.compile(r"#if DEBUG.*?PaywallDebugLaunch.*?#else\s+return PaywallConfig\.enabled\s+#endif", re.S),
        )
        release_branch = self.volta_app.split("#else", 1)[1].split("#endif", 1)[0]
        self.assertNotIn("ProcessInfo", release_branch)
        self.assertNotIn("launchArguments", release_branch)
        self.assertNotIn("VoltaPaywall", release_branch)

    def test_disabled_paywall_branch_is_the_existing_full_app_path(self):
        """The default-off branch must start AppState and render RootView directly. No
        charger decision, warning, freshness, provenance, or routing file may consult the
        subscription feature gate."""
        self.assertRegex(
            self.volta_app,
            re.compile(
                r"if shouldUsePaywallGate \{.*?PaywallLaunchGate.*?\} else \{\s+VoltaContentRoot\(\)",
                re.S,
            ),
        )
        self.assertRegex(
            self.volta_app,
            re.compile(r"struct VoltaContentRoot: View \{.*?RootView\(\).*?\.task \{ state\.start\(\) \}", re.S),
        )
        for source in (
            self.root_view, self.sheet_view, self.station_views, self.models, self.app_state,
            self.discovery_controls, self.discovery_filters, self.station_actions, self.station_presentation,
        ):
            self.assertNotIn("PaywallConfig", source)
            self.assertNotIn("isSubscribed", source)

    def test_public_source_details_render_license_attribution_transformation_and_update(self):
        for field in (
            "var license: String?",
            "var licenseUrl: String?",
            "var attribution: String?",
            "var transformation: String?",
            "var sourceFetchedAt: String?",
        ):
            self.assertIn(field, self.models)
        for visible_contract in (
            'Text("Attribution: \\(attribution)")',
            'Link("License: \\(license)", destination: licenseURL)',
            'Text("Transformation: \\(transformation)")',
            'Text("Source updated: \\(updated)")',
            'Text("Retrieved: \\(fetched)")',
        ):
            self.assertIn(visible_contract, self.station_views)

    def test_paywall_accessibility_and_metadata_contracts_are_source_pinned(self):
        self.assertIn("ScrollView", self.paywall_view)
        self.assertIn("dynamicTypeSize.isAccessibilitySize", self.paywall_view)
        self.assertIn("product.displayName", self.paywall_view)
        self.assertIn("product.displayPrice", self.paywall_view)
        self.assertIn("product.subscriptionPeriodFormatStyle", self.paywall_presentation)
        self.assertIn("isEligibleForIntroOffer", self.subscription_manager)
        self.assertIn(".accessibilityAddTraits(isSelected ? .isSelected : [])", self.paywall_view)
        self.assertIn("frame(minHeight: 44)", self.paywall_view)
        self.assertIn(".foregroundStyle(.primary)", self.paywall_view)
        self.assertIn(".tint(Theme.action)", self.paywall_view)
        self.assertIn("dynamicTypeSize.isAccessibilitySize", self.paywall_view)
        for source in (self.paywall_view, self.paywall_presentation):
            for literal in re.findall(r'"(?:\\.|[^"\\])*"', source):
                self.assertNotRegex(literal, r"(?:€|£|\$\s*\d|\b7[- ]day)")


if __name__ == "__main__":
    unittest.main()
