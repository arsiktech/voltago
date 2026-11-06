# OSM socket power

`socket:*:output` is documented in kW, but mappers also publish bare watts, amps and
volts. `parse_osm_socket_output` in `station_truth.py` reads one value and returns kW or
unknown, with the reason in `basis`. Ingestion stores that reason as
`connectors[].raw.output_reading`.

## Rule

1. A stated unit is trusted, up to the connector's ceiling. `7400 W` is 7.4 kW.
   `7400 kW` is unknown: a stated unit is never reinterpreted.
2. A bare number inside the connector's kW range is kW. `22` is 22 kW.
3. A bare number above that range is watts only if the result is a real rating for that
   connector. `7400` on Type 2 is 7.4 kW. `50000` on Type 2 is unknown, because 50 kW is
   above the AC range. `50000` on CCS2 is 50 kW.
4. Everything else is unknown, and an unknown power fails every power filter.

| Connector | Ceiling | Bare as kW | Bare as watts, in kW |
| --- | ---: | ---: | ---: |
| Schuko | 3.84 | 1.38–3.84 | 1.38–3.84 |
| Type 2 | 1200 | 2.3–150 | 2.3–43.7 |
| Type 1, GB/T AC | 1200 | 1.38–43.7 | 1.38–43.7 |
| CCS1, CCS2, CHAdeMO, Tesla, NACS, GB/T DC | 1200 | 5–1200 | 5–1200 |

- Bare 16, 32 and 63 on AC connectors are unknown. They are the standard AC currents, and
  mappers often write `32 A` in this tag.
- The 150 kW bare limit on Type 2 keeps legacy Tesla DC ratings and rejects values such as
  230 (volts) and 300 or 400 (probably a CCS rating on the wrong socket).
- Ceilings: Schuko is OSM's 240 V × 16 A. The others are IEC 62196-1's 1500 V × 800 A DC
  envelope. Type 2 alone does not prove AC.
- The kW floors drop socket counts such as `1` and `2`.
- `7,400` is unknown, because the comma could be a decimal or a thousands separator.

## Evidence

[Taginfo](https://taginfo.openstreetmap.org/keys/socket%3Atype2%3Aoutput#values) worldwide
counts on 6 November 2026, bare values only:

- `socket:type2:output`: 22 (4,757), 11 (617), 18 (260), 22144 (123), 22000 (41), 32 (20),
  300 (16), 20400 (15), 15000 (13), 7400 (11), 11000 (8), 230 (3).
- `socket:schuko:output`: 3.7 (45), 3 (30), 2.3 (12), 2300 (10), 3700 (8), 16 (6), 230 (3).
- `socket:type2_combo:output`: 50 (513), 60 (380), 300 (284), 50000 (10), 250000 (2),
  1 (8), 2 (12).

No Type 2 charger delivers 7,400 kW, so these values cannot be kW. This is the only reason
for reading them as watts. The rule does not divide every large number by 1000.

## Where it runs

- New ingestion: `source_osm` in `scripts/ingest_sources.py` and `normalize_osm` in
  `scripts/global_sources.py`.
- Served rows: `repair_osm_connector_power` runs in `_normalize_lead` in `server.py`. It
  re-reads the retained `raw.output`, so a snapshot built before this rule is corrected on
  read. The snapshot file does not change.

A rejected output tag also blocks the station description and voltage/amperage fallback,
and the Red MOVEM tariff needs a value with a stated unit.
