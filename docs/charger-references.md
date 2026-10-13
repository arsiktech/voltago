# Charger references

Internal alias proof separates immutable origin anchors from refresh accounting.
The old primary and the independent prior owner's primary must each have unique
current identities and current anchors within 250 m of the target. Both original
coordinate anchors remain unchanged. Former corroborators do not own the old app
reference: every retained identifier is separately accounted for against current
records, an exact rights decision, or explicit absent corroboration. Reconciliation
and the shared validator recompute this accounting on every refresh. A missing
contributor's own app reference remains subject to its independent proof gates.

An expired Red MOVEM tariff review holds affected OSM rows, including their old
free tags. The adapter finishes unrelated rows and then reports the held records
as a pricing-policy error. This is partial source failure, not successful collection.

The reference contract preserves app references without copying historical charger
facts. `scripts/station_identity.py` owns validation. Raw `merged_ids` is an ingestion
claim, never a resolver. Both merge stages retain source identity and coordinate
anchors in `reference_inputs`; reconciliation admits aliases against the previous
dataset and current independent owner. Alias refresh checks all source keys before
uniqueness filtering, both original coordinate anchors, and the independent owner's
current primary identity. The automatic distance limit remains 250 metres.

Current source and owner anchors are mandatory on the first and every later alias
refresh. Removing `reference_inputs` cannot bypass the check. The same validation
checks prior source uniqueness and exact prior anchors in reconciliation and gates.

Primary source keys come from the row's own typed fields and station URL. Absorbed
source links remain separate evidence. A unique previous primary can keep its app ID
when corroborating contributions become separate pins. Every retained source key is
accounted for against current pins, an exact artifact-bound denied grant, or explicitly
absent corroboration. Absent corroboration supplies no current facts and cannot resolve
or withdraw another app reference. A denied primary cannot transfer its app ID to a
permitted contributor. Endolla location IDs,
València socket object IDs and Zaragoza station IDs retain their source-specific types.
Madrid address-only rows do not acquire invented stable IDs.

Repeated Canarias map markers can represent one station page. The producer retains
each marker's coordinates and map/marker IDs and selects an existing source coordinate
in stable marker order. A copy alias requires the same page and map, distinct retained
marker IDs, both previous anchors, and current marker evidence within 250 metres.
Unidentified duplicate source keys still fail validation. Repeated map pages do not
absorb a neighboring registry station through proximity alone.

Both merge stages reject conflicting explicit charging operators, including conflicts
through a third contribution. Charging-brand aliases are normalized; venue-owner
fields are not charging-operator evidence. Current OCM coordinate checks exclude
conflicting historical postcodes without restoring historical facts.

Canonical station rows represent current data. Noncanonical references have three
states:

| State | Meaning | Target |
| --- | --- | --- |
| `alias` | Validated previous reference | Exactly one current canonical ID |
| `withheld` | Reuse rights cannot be established from retained source metadata | None |
| `unresolved` | Identity evidence cannot justify a transition | None; blocks promotion |

A withheld reference does not assert that a physical charger closed. Neither
unavailable state is a pin, price claim, navigation target, or community-write target.

## Wire version 1

`/api/chargers`, `/api/intel` and `/api/free-registry` expose an additive `references`
object with `version: 1`, a `revision`, and `entries`. Each entry contains `key`,
`state`, and (only for an alias) `canonical_id`. Canonical IDs remain on station rows.
The entry key is lowercase hexadecimal SHA-256 of the exact UTF-8 bytes of
`"volta-reference-v1\0" + appID`. Do not trim or normalize the ID. The NUL is one
zero byte. Python, TypeScript and Swift tests include identical ASCII and Unicode
vectors.

The revision hashes the internal records and reference entries together. It is an
opaque dataset identifier to clients, not a claim of live freshness. Intelligence
rows carry `reference_revision`; aliases resolve only to rows with the catalogue's
revision. Duplicate keys, cycles, canonical conflicts, unknown versions and malformed
entries fail closed. A refresh race can return `references: null`; keep the original
saved reference and show that lookup is unavailable. Do not redirect through an old
catalogue to a row from a different revision.

Audit anchors, `identity_aliases`, `merged_ids`, `merged_source_identifiers`, raw
withdrawal records and old generated IDs are not part of the public projection.
The public catalogue is bounded to 100,000 entries and travels with its response.
There is no request containing the device's favorites. Favorites retain their exact
stored IDs until an explicit save/remove action. The existing service worker caches
only its public national overview; viewport and favorite queries remain excluded from service-worker storage.
Offline cached rows retain their existing non-live labeling and trust rules.

## Rights and gates

The OCM adapter records denied grants from the actual source POI and provider
metadata, with source ID, snapshot commit, record hash and artifact SHA-256. Missing
or unclear rights do not become permission. Mixed-source references are not withdrawn
on the basis of a single contributor. Gates re-read retained source artifacts and
compare the decision to those bytes. Pass a copied cache with `--reference-cache`
when auditing an offline candidate using `qa_ingestion_delta.py` or
`qa_ingestion_canary.py`.

Internal anchors keep all contributor origins, including unrecognized origins and
incomplete source lists. Unknown provenance blocks wholesale withdrawal. The delta
caller passes the complete previous payload, including `station_references`, so
withdrawals and aliases remain accountable on successive refreshes.

The delta report separates canonical counts, validated merges, explicit withdrawals,
and unresolved references. Runtime requires zero unexplained loss and stops before
canary on unresolved reconciliation. The remaining quality, transport, licensing,
source-family and canary-history gates still apply. A schema-valid unresolved
catalogue is useful for local testing but is not eligible for runtime promotion.

Community admission uses the validated resolver. Existing check-ins/photos keep
their stored IDs. Read aggregation maps original IDs to a canonical owner once,
applies moderation, and calculates success/failure chronology across that group.
Community evidence can downgrade a decision; it cannot create live authority.
Historical reads require active moderated history, not current write admission. A
cold live cache does not hide stored summaries or photos. Unknown historical IDs are
read under their exact stored IDs; only validated aliases can redirect them. Explicit
withheld and unresolved references block reads and writes. Current write admission
still requires an admitted canonical station or its validated alias.

## Rollout

1. Validate the complete candidate against the retained production baseline and
   actual canary history. Keep unresolved IDs and reasons as a blocking report.
2. Deliver additive server and web support while the existing dataset remains active.
   Verify desktop/mobile selection, saved deep links, refresh races and offline data.
3. Build and exercise native active, alias, withheld and unresolved favorites, including
   save/remove and cached states. Native distribution requires separate owner approval.
4. Establish compatibility for deployed native binaries before activating the data
   migration. Older binaries compare only canonical IDs and cannot show unavailable
   saved references. Code completion does not make those binaries compatible.
5. Only an independently approved rollout may promote the gated dataset. Keep the
   previous dataset bundle for rollback. Never rewrite the community database or
   on-device favorites to manufacture compatibility.
