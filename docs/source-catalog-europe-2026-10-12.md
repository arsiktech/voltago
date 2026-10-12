# Europe EV charging source catalog — 2026-10-12

A discovery list of official registers, maps, data hubs and charging operators across Europe,
for choosing Volta's next data sources outside Spain. Machine-readable copy:
[source-catalog-europe-2026-10-12.csv](source-catalog-europe-2026-10-12.csv).

**Read this first.**
- A listing here is **not** a reuse licence and **not** an ingestion decision. Every new source still
  needs the rights review, the GET-only transport rule in `AGENTS.md` and an owner decision before an adapter.
- The existing verdicts stay in force: Electromaps link-only, Chargemap skip, Consum owner decision,
  ACCIONA platform no-ingest ([2026-07-15](source-research-2026-07-15-national.md),
  [2026-07-17](source-research-2026-07-17-free-first.md)); exclusions live in `data/source_policy_exclusions.json`.
- "Loads" means the URL answered on 2026-10-12 (HTTP 200/202, after redirects). It says nothing about
  data quality, freshness, licence or whether an API exists.

**Link check, 2026-10-12:** 119 sources; 110 load, 6 block automated checks
(open in a browser), 3 unreachable that day.

**Already used by Volta (Spain):** DGT National Access Point (largest source), OpenStreetMap, Open Charge Map,
Feníe Energía, Canarias Te Recarga, Madrid / Barcelona / Zaragoza / València open data, MapaREVE, Lidl free list.

**Most useful next, by AFIR (Regulation 2023/1804):** each country's National Access Point publishes
location data and, increasingly, price and live status. Start from NAPCORE's directory, then the national
rows below (Portugal MOBI.E, France IRVE, Italy PUN, Germany Mobilithek are the strongest).

## EU-wide official

| Country | Source | Type | Link | 2026-10-12 | Note |
|---|---|---|---|---|---|
| EU | AFIR, Regulation (EU) 2023/1804 | Official | <https://eur-lex.europa.eu/eli/reg/2023/1804/oj> | ✅ loads | EUR-Lex returns 202 (loads in browser) |
| EU | European Alternative Fuels Observatory (EAFO) | Official | <https://alternative-fuels-observatory.ec.europa.eu/> | ✅ loads |  |
| EU | NAPCORE (directory of National Access Points) | Official | <https://napcore.eu/> | ✅ loads |  |

## National registers and national networks

| Country | Source | Type | Link | 2026-10-12 | Note |
|---|---|---|---|---|---|
| Austria | E-Control Ladestellenverzeichnis | Official | <https://www.ladestellen.at/> | ✅ loads |  |
| Belgium | transportdata.be | Official | <https://www.transportdata.be/> | ✅ loads |  |
| Croatia | HEP ELEN | Operator | <https://elen.hep.hr/> | ✅ loads | Added (smaller countries) |
| Czechia | Ministry of Industry (MPO) charging station list | Official | <https://www.mpo.gov.cz/> | ✅ loads | Added (smaller countries) |
| Denmark | Vejdirektoratet NAP (du-portal) | Official | <https://du-portal-ui.dataudveksler.app.vd.dk/> | ✅ loads | Added (smaller countries) |
| Estonia | Enefit Volt | Operator | <https://www.enefit.ee/> | ✅ loads | Added (smaller countries) |
| Finland | Fintraffic Digitraffic | Official | <https://www.digitraffic.fi/> | ✅ loads | Added (smaller countries) |
| France | Base nationale des IRVE (data.gouv.fr) | Official | <https://www.data.gouv.fr/fr/datasets/fichier-consolide-des-bornes-de-recharge-pour-vehicules-electriques/> | ✅ loads |  |
| France | transport.data.gouv.fr | Official | <https://transport.data.gouv.fr/> | ✅ loads |  |
| Germany | Bundesnetzagentur Ladesäulenregister | Official | <https://www.bundesnetzagentur.de/DE/Fachthemen/ElektrizitaetundGas/E-Mobilitaet/start.html> | ✅ loads |  |
| Germany | Mobilithek | Official | <https://mobilithek.info/> | ✅ loads | Python TLS failed; works in curl/browser |
| Germany | Nationale Leitstelle Ladeinfrastruktur (StandortTOOL / Ladeatlas) | Official | <https://nationale-leitstelle.de/> | ✅ loads |  |
| Greece | Registry of charging points (ΜΥΦΑΗ / ΜΗΤΡΩΟ) | Official | <https://electrokinisi.yme.gov.gr/> | ✅ loads | Added (smaller countries) |
| Hungary | e-Mobi | Operator | <https://e-mobi.hu/> | 🔴 unreachable | Not reachable today; not confirmed; Added (smaller countries) |
| Iceland | ON (Orka náttúrunnar) | Operator | <https://www.on.is/> | ✅ loads | Added (smaller countries) |
| Ireland | ESB ecars (main public network) | Operator | <https://esb.ie/ecars> | ✅ loads | Added (smaller countries) |
| Ireland | National Access Point (Transport Infrastructure Ireland) | Official | <https://www.tii.ie/> | ✅ loads | Added (smaller countries) |
| Italy | PUN, Piattaforma Unica Nazionale | Official | <https://www.piattaformaunicanazionale.it/> | ✅ loads |  |
| Latvia | Elektrum Drive | Operator | <https://www.elektrum.lv/> | ✅ loads | Added (smaller countries) |
| Lithuania | Ignitis ON | Operator | <https://ignitis.lt/> | ✅ loads | Added (smaller countries) |
| Luxembourg | Chargy (national network) | Operator | <https://chargy.lu/> | ✅ loads | Added (smaller countries) |
| Netherlands | NDW | Official | <https://www.ndw.nu/> | ✅ loads |  |
| Norway/Sweden | NOBIL | Official | <https://www.nobil.no/> | ✅ loads | Moved to info.nobil.no |
| Poland | EIPA (UDT) | Official | <https://eipa.udt.gov.pl/> | ✅ loads |  |
| Portugal | MOBI.E | Official | <https://www.mobie.pt/> | ✅ loads |  |
| Romania | Renovatio e-charge | Operator | <https://www.renovatio-e-charge.ro/> | 🔴 unreachable | Not reachable today; not confirmed; Added (smaller countries) |
| Slovakia | SAPI (Slovak electromobility association) | Industry body | <https://www.sapi.sk/> | ✅ loads | Added (smaller countries) |
| Slovenia | NAP Slovenia | Official | <https://www.ncup.si/> | ✅ loads | Added (smaller countries) |
| Spain | DGT National Access Point | Official | <https://nap.dgt.es/> | ✅ loads | Used by Volta |
| Spain | MapaREVE | Official | <https://www.mapareve.es/> | ✅ loads | Used by Volta |
| Spain (Canarias) | Canarias Te Recarga | Official | <https://apps.apple.com/us/app/canarias-te-recarga-app/id1607076018> | ✅ loads | Used by Volta; Website down (TLS error); official app works; Old address dead; corrected |
| Sweden | Trafikverket open data | Official | <https://data.trafikverket.se/> | ✅ loads | Added (smaller countries) |
| Switzerland | ich-tanke-strom.ch (SFOE) | Official | <https://www.ich-tanke-strom.ch/> | ✅ loads | Now on uvek-gis.admin.ch (Swiss gov) |

## Maps and aggregator apps

| Country | Source | Type | Link | 2026-10-12 | Note |
|---|---|---|---|---|---|
| Europe | ABRP (A Better Routeplanner) | Aggregator | <https://abetterrouteplanner.com/> | ✅ loads |  |
| Europe | Bonnet | Aggregator | <https://www.joinbonnet.com/> | ✅ loads |  |
| Europe | Chargemap | Aggregator | <https://chargemap.com/> | ✅ loads | Excluded (rights verdict 2026-07-15: SKIP) |
| Europe | Electromaps | Aggregator | <https://www.electromaps.com/> | ✅ loads | Excluded (rights verdict 2026-07-15: link-only) |
| Europe | Electroverse (Octopus) | Aggregator | <https://electroverse.octopus.energy/> | ✅ loads | Moved to electroverse.com |
| Europe | Elli (Volkswagen) | Aggregator | <https://www.elli.eco/> | ✅ loads |  |
| Europe | Open Charge Map | Open | <https://openchargemap.org/> | ✅ loads | Used by Volta; Python TLS failed; works in curl/browser |
| Europe | OpenStreetMap (amenity=charging_station) | Open | <https://wiki.openstreetmap.org/wiki/Tag:amenity%3Dcharging_station> | ✅ loads | Used by Volta |
| Europe | PlugShare | Aggregator | <https://www.plugshare.com/> | ✅ loads |  |
| Europe | Plugsurfing | Aggregator | <https://www.plugsurfing.com/> | ✅ loads |  |
| Europe | Shell Recharge | Aggregator | <https://shellrecharge.com/> | ✅ loads | Redirects to a country page |
| UK | Zapmap | Aggregator | <https://www.zapmap.com/> | ✅ loads |  |

## Commercial data and roaming hubs

| Country | Source | Type | Link | 2026-10-12 | Note |
|---|---|---|---|---|---|
| Europe | Eco-Movement | B2B data | <https://www.eco-movement.com/> | ✅ loads |  |
| Europe | Gireve | B2B roaming hub | <https://www.gireve.com/> | ✅ loads |  |
| Europe | Hubject | B2B roaming hub | <https://www.hubject.com/> | ✅ loads |  |
| Europe | e-clearing.net | B2B roaming hub | <https://e-clearing.net/> | ✅ loads |  |

## Operators in many countries

| Country | Source | Type | Link | 2026-10-12 | Note |
|---|---|---|---|---|---|
| Europe | Allego | Operator | <https://www.allego.eu/> | ✅ loads |  |
| Europe | Atlante | Operator | <https://www.atlante.energy/> | ✅ loads |  |
| Europe | Electra | Operator | <https://www.go-electra.com/> | ✅ loads |  |
| Europe | Fastned | Operator | <https://www.fastnedcharging.com/> | ✅ loads |  |
| Europe | Free2Move eSolutions | Operator | <https://www.esolutions.free2move.com/eu/en_gb/home-charging> | 🟡 blocks bots | Old address dead; corrected |
| Europe | Ionity | Operator | <https://ionity.eu/> | ✅ loads |  |
| Europe | Mer (Statkraft) | Operator | <https://mer.eco/> | ✅ loads |  |
| Europe | Powerdot | Operator | <https://powerdot.eu/> | ✅ loads |  |
| Europe | Recharge | Operator | <https://rechargeinfra.com/> | ✅ loads |  |
| Europe | Tesla Supercharger | Operator | <https://www.tesla.com/support/charging/supercharging-other-evs> | 🟡 blocks bots | Old address dead; corrected |
| Europe | TotalEnergies charging | Operator | <https://chargingservices.totalenergies.com/en/home> | 🟡 blocks bots | Old address dead; corrected |
| Europe | Zunder | Operator | <https://www.zunder.com/> | ✅ loads |  |
| Europe | bp pulse | Operator | <https://www.bppulse.com/> | ✅ loads |  |

## Operators by country

| Country | Source | Type | Link | 2026-10-12 | Note |
|---|---|---|---|---|---|
| Austria | SMATRICS EnBW | Operator | <https://smatrics.com/> | ✅ loads | Added (smaller countries) |
| Austria | Wien Energie | Operator | <https://www.wienenergie.at/> | ✅ loads | Added (smaller countries) |
| Belgium | Blue Corner | Operator | <https://www.bluecorner.be/> | ✅ loads | Now Blink Charging (redirects); Added (smaller countries) |
| Czechia | ČEZ e-mobilita | Operator | <https://www.cez.cz/> | ✅ loads | Added (smaller countries) |
| Czechia/Slovakia | PRE / ZSE Drive | Operator | <https://www.zsedrive.sk/> | ✅ loads | Added (smaller countries) |
| Denmark | Clever | Operator | <https://clever.dk/> | ✅ loads |  |
| Europe | Kaufland e-charging | Operator | <https://www.kaufland.de/> | 🟡 blocks bots | Added (smaller countries) |
| Europe | Lidl e-charging (DE) | Operator | <https://www.lidl.de/> | ✅ loads | Used by Volta; Added (smaller countries) |
| Finland | K-Lataus | Operator | <https://www.k-lataus.fi/> | ✅ loads | Moved to k-auto.fi/k-lataus |
| France | Engie Vianeo | Operator | <https://engie-vianeo.com/> | ✅ loads | Old address dead; corrected |
| France | Freshmile | Operator | <https://www.freshmile.com/> | ✅ loads |  |
| France | Izivia (EDF) | Operator | <https://www.izivia.com/> | ✅ loads |  |
| Germany | Aral pulse | Operator | <https://www.aral.de/de/global/retail/pulse.html> | ✅ loads |  |
| Germany | E.ON Drive | Operator | <https://www.eon.de/de/pk/e-mobility.html> | 🟡 blocks bots | Site blocks automated checks; open in browser |
| Germany | EWE Go | Operator | <https://www.ewe-go.de/> | ✅ loads |  |
| Germany | EnBW mobility+ | Operator | <https://www.enbw.com/elektromobilitaet/> | ✅ loads |  |
| Germany | Pfalzwerke | Operator | <https://www.pfalzwerke.de/> | ✅ loads |  |
| Greece | Blink Charging Hellas | Operator | <https://www.blinkcharging.gr/> | ✅ loads | Added (smaller countries) |
| Hungary | MOL Plugee | Operator | <https://molplugee.hu/> | ✅ loads | Added (smaller countries) |
| Italy | A2A E-Moving | Operator | <https://www.a2a.it/> | ✅ loads |  |
| Italy | Duferco Energia | Operator | <https://www.dufercoenergia.com/> | ✅ loads |  |
| Italy | Enel X Way / Ewiva | Operator | <https://www.ewiva.com/> | ✅ loads |  |
| Italy | Free To X | Operator | <https://freeto-x.it/en> | ✅ loads | Old address dead; corrected |
| Netherlands | Eneco | Operator | <https://www.eneco.nl/> | ✅ loads |  |
| Netherlands | Eneco eMobility | Operator | <https://www.eneco-emobility.com/> | ✅ loads | Added (smaller countries) |
| Netherlands | Vattenfall InCharge | Operator | <https://incharge.vattenfall.nl/en> | ✅ loads | Old address dead; corrected |
| Nordics | Circle K charging | Operator | <https://www.circlek.no/> | ✅ loads |  |
| Norway | Eviny | Operator | <https://www.eviny.no/> | ✅ loads | Added (smaller countries) |
| Poland | GreenWay | Operator | <https://greenwaypolska.pl/> | ✅ loads | Moved to greenwaynetwork.com |
| Poland | Orlen Charge | Operator | <https://www.orlen.pl/> | ✅ loads | Homepage, not EV page |
| Portugal | Prio | Operator | <https://www.prio.pt/> | ✅ loads |  |
| Spain | EDP | Operator | <https://www.edp.com/> | ✅ loads |  |
| Spain | Easycharger | Operator | <https://www.easycharger.es/> | 🔴 unreachable | Timed out today; not confirmed |
| Spain | Endesa X Way | Operator | <https://www.endesaxway.com/> | ✅ loads |  |
| Spain | Etecnic (Etecnic e-mobility) | Operator | <https://www.etecnic.es/> | ✅ loads |  |
| Spain | Feníe Energía | Operator | <https://www.fenieenergia.es/> | ✅ loads | Used by Volta |
| Spain | Iberdrola / bp pulse | Operator | <https://www.iberdrola.es/movilidad-electrica> | 🟡 blocks bots | Site blocks automated checks; open in browser |
| Spain | Moeve (formerly Cepsa) | Operator | <https://www.moeveglobal.com/> | ✅ loads |  |
| Spain | Plenitude (Eni) | Operator | <https://eniplenitude.com/> | ✅ loads |  |
| Spain | Repsol / Waylet | Operator | <https://www.repsol.es/waylet/> | ✅ loads | Moved to repsol.es/waylet |
| Spain | Wenea | Operator | <https://wenea.com/> | ✅ loads |  |
| Spain (Canarias) | DISA | Operator | <https://www.disagrupo.es/> | ✅ loads |  |
| Spain/Portugal | Galp Electric | Operator | <https://www.galp.com/> | ✅ loads |  |
| Sweden | Vattenfall InCharge (SE) | Operator | <https://incharge.vattenfall.se/> | ✅ loads | Added (smaller countries) |
| Switzerland | GOFAST | Operator | <https://www.gofast.swiss/> | ✅ loads | Added (smaller countries) |
| Switzerland | Move Mobility | Operator | <https://www.move.ch/> | ✅ loads | Added (smaller countries) |
| UK | Be.EV | Operator | <https://be-ev.co.uk/> | ✅ loads | Old address dead; corrected |
| UK | Connected Kerb | Operator | <https://www.connectedkerb.com/> | ✅ loads |  |
| UK | Gridserve | Operator | <https://www.gridserve.com/> | ✅ loads |  |
| UK | InstaVolt | Operator | <https://instavolt.co.uk/> | ✅ loads |  |
| UK | MFG EV Power | Operator | <https://www.mfgevpower.co.uk/> | ✅ loads |  |
| UK | Osprey | Operator | <https://www.ospreycharging.co.uk/> | ✅ loads |  |
| UK | Pod Point | Operator | <https://pod-point.com/> | ✅ loads | Now Pod Energy (redirects) |
| UK | char.gy | Operator | <https://char.gy/> | ✅ loads |  |

## Method

Compiled 2026-10-12 from existing Volta research plus general knowledge, then every URL was fetched
(Python urllib, then curl with a browser user agent for failures). Dead or moved addresses were replaced
after a web search and re-fetched. Re-run the check before relying on any single link.
