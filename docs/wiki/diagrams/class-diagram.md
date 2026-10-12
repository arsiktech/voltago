# Class Diagram

Volta is mostly function-first Python and React, so this diagram focuses on the
real class/type surfaces that cross module boundaries: the HTTP handler, API row
shapes, and iOS state/client models.

```mermaid
classDiagram
    class Handler {
        +do_GET()
        +do_HEAD()
        +send_json(status, payload)
        +serve_static(path)
    }
    class GaiaJumpPageParser {
        +handle_starttag(tag, attrs)
        +handle_data(data)
    }
    class ChargersResponse {
        +Station[] stations
        +Station[] leads
        +Meta meta
        +CacheInfo cache
        +string warning
    }
    class Station {
        +string id
        +string name
        +number lat
        +number lon
        +Decision decision
        +Verification verification
        +Corroboration corroboration
        +Claim charge_claim
        +Claim parking_claim
        +bool lead
    }
    class Connector {
        +string connector_id
        +string label
        +string status
        +number power_kw
        +bool tesla_ok
    }
    class SourceRef {
        +string name
        +string url
        +string evidence
    }
    class Decision {
        +string level
        +string label
        +string reason
    }
    class AppState {
        +Mode mode
        +LiveState liveState
        +Station[] stations
        +Station[] leads
        +start()
        +fetchLive(reason)
        +loadOverview()
    }
    class VoltaAPI {
        +overview(maxPrice)
        +live(bbox, coords, maxPrice, availableOnly)
        +geocode(query)
    }
    Handler --> ChargersResponse : emits
    ChargersResponse --> Station : contains
    Station --> Connector : has
    Station --> SourceRef : cites
    Station --> Decision : has
    GaiaJumpPageParser --> Station : parsed into
    AppState --> VoltaAPI : calls
    AppState --> Station : stores
    VoltaAPI --> ChargersResponse : decodes
```

## Notes

- TypeScript and Swift both mirror the enriched Python row shape, but most row
  fields are optional because source coverage varies.
- Unknown backend decision levels are handled forward-compatibly by frontend and
  iOS helpers rather than crashing the UI.
