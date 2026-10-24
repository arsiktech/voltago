# MapaREVE refusal and live-status alternatives (2026-10-24)

## Finding

Since 2026-10-21 08:59 UTC, every MapaREVE live query from the production host has
received HTTP 403 from MapaREVE's Imperva bot protection. The response carries an
`X-Iinfo` header. The same `_fetch_mapareve_page` code, headers and query succeed
from another network in about 1.7 s, and no commit from 19–21 Oct changed the
request. The refusal therefore applies to the production host's network address, not
to the request. Before 21 Oct, the only MapaREVE errors were Volta's own concurrency
limit. The trigger is not known.

Volta now respects the refusal: it pauses, probes once, and reports the state in
`/api/source-health` (see [backend engine notes](backend-engine.md)). Routing the
same queries through another network address would evade the refusal and is out of
scope.

## Alternatives checked

| Route | State on 2026-10-24 | Use |
| --- | --- | --- |
| DGT NAP static table (`nap.dgt.es/datex2/v3/miterd/EnergyInfrastructureTablePublication/electrolineras.xml`) | Live (HTTP 200). DATEX II v3, CC BY, daily. No live status, no tariffs. | Already ingested as corroboration (`source_dgt_nap()`). |
| DGT NAP status publication (`.../EnergyInfrastructureStatusPublication/`) | Not published (HTTP 404 on `nap.dgt.es`; `infocar.dgt.es` returned 502). The NAP dataset page lists only the static table. | Watch. On the day it appears, it becomes the authoritative live-status source. |
| Mapa REVE external API (`scripts/reve_external.py`) | Prepared and gated on an owner-held key. | Official route to MapaREVE live data without the public map query. |
| Ask MapaREVE to lift the refusal | Not started. | Owner decision. |
| Spirii AFIR and Endolla feeds | Live in production for their own sites. | Unchanged. |

## Recommendation

1. Keep the refusal pause in place, and watch whether the block lifts.
2. Request a Mapa REVE external API key, or ask MapaREVE to lift the refusal. Both
   are owner actions.
3. Re-check the NAP status publication monthly. Add a connector only when it exists.
