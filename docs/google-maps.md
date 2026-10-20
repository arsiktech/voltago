# Google Maps listing links

Every web station card, map popup, and native station card/detail has a separate
Google Maps action. Existing source and coordinate navigation actions retain their
evidence hierarchy. The listing action never changes a station's availability,
price, location verification, recommendation score, or route readiness.

- **View Google Maps** opens a source-published place identifier, identified Google
  listing URL, or reviewed Google share link.
- **Search Google Maps** searches for an EV charger using the station name, address,
  city and coordinates. This is an unmatched lookup; a Google listing may not exist.

Google opens outside Volta, where its current ratings, reviews, photos, opening
hours and other place details can be read when available.

## Data contract

`enrich_station` adds `navigation.google_listing` only when a listing reference
exists. It always supplies `navigation.google_search`. Both survive compact and
summary responses so opening a card's Details is unnecessary. The original
`navigation.google` / `apple` directions remain separate.

Accepted inputs are `google_place_id`, `google_maps.place_id`,
`navigation.google_place_id`, `google_maps_url`, `google_maps.url`,
`navigation.google_listing`, an identified `navigation.google` URL, or the
station's own Google Maps source URL. Known Google place IDs generate Maps URLs
with `query_place_id`. No ID is guessed from coordinates or a nearby business.

Reviewed associations live in `data/google_maps_links.json` outside the generated
charger catalogue. Buenavista's association uses its exact Canarias Te Recarga
source URL/canonical ID and a supplied Google share link. The public sample does
not contain that charger; the association takes effect whenever the full
catalogue supplies its row. A similarly named or nearby charger does not inherit it.

Only credential-free HTTPS Google Maps hosts and recognized listing identifier
forms are accepted. A coordinate query, generic Maps homepage, bare place name,
directions URL or non-Google host never becomes a confirmed listing. External
links load only after the driver selects them; no extra source requests or API
credentials are needed for these handoffs.

## Optional Google ratings and reviews

Displaying Google's live data inside Volta requires a configured Google Maps
Platform project, Places API access and billing. No such credential is configured
for this change. The app does not scrape Maps pages, copy screenshot ratings, or
substitute community/source scores for Google ratings.

A follow-up integration should:

1. Resolve and verify the exact charger place ID, preserving match provenance.
   Keep uncertain matches as searches. Google's Places Text Search (New) uses a
   read-only POST, so automatic matching needs a new explicit source-transport
   exception before it can run under the repository's GET-only policy.
2. Fetch Place Details (New) by verified ID using a server-side GET and a bounded
   field mask, for example `id,googleMapsUri,rating,userRatingCount,reviews`.
   Request additional opening-hours/business-status fields only when displayed.
   Review fields have their own billing tier and return a limited selection,
   rather than every Google review.
3. Render Google attribution and review-author attribution/links, and check the
   current Places display, non-Google-map, storage and regional terms before
   choosing where this content appears. Keep fetched-at time and unavailable
   states visible; never invent a zero-star rating when Google omits one.
4. Bound request concurrency, rate and spend; keep the key on the server; and
   refresh or discard content according to Google's permitted retention rules.
   API failures must leave cards and their external listing links usable.
5. Keep user reviews, Google opening hours and Google business status separate
   from live connector telemetry, charging tariffs and parking authority. They
   cannot promote a station to Go now.

This change implements listing handoffs. It does not enable API billing, fetch
Google content, or establish a live-data integration.
