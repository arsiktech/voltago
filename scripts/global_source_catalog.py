"""Lightweight adapter metadata shared by runtime and offline index summaries."""

ADAPTERS = (
    ("ocm", "OpenChargeMap", "Worldwide", "Public repository export; per-provider licence filters", None),
    ("osm", "OpenStreetMap", "Worldwide by country or area", "Bounded public Overpass GET", None),
    ("france_irve", "France IRVE", "FR and overseas territories", "Official public dataset", None),
    ("germany_bnetza", "Bundesnetzagentur", "DE", "Official public dataset", None),
    ("hk_clp", "CLP Hong Kong", "HK", "Official public dataset", None),
    ("afdc", "US AFDC / NLR", "US and CA", "Official GET API", "Authorized API key required"),
    ("etecnic", "Etecnic / EVcharge", "ES", "Public operator map GET", None),
    ("atlante", "Atlante", "ES, FR, IT and PT", "Public operator location export GET", None),
    ("zunder", "Zunder", "ES", "Public operator map page GET", None),
    ("mot_reus", "MOT / Reus", "Reus, ES", "Municipal GIS GET", None),
    ("mot_tarragona", "MOT / Tarragona", "Tarragona, ES", "Municipal CSV GET", None),
    ("interparking", "Interparking / P-APP", "ES", "Public parking finder GET; EV feature required", None),
    ("telpark", "Telpark", "ES", "Public parking pages GET; confirmed EV feature required", None),
    ("elektrum_drive", "Elektrum Drive", "EE, LV and LT", "Public charging location page GET", None),
)
ADAPTER_IDS = tuple(item[0] for item in ADAPTERS)
