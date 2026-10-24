"""Fixture tests for the DGT NAP (plan 013) corroboration adapter.

The fixture is a real excerpt from the national DATEX II feed (verified
2026-07-17), wrapped in the publication skeleton. Stdlib unittest only.
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ingest_sources as ing

FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<d2:payload xmlns:d2="http://datex2.eu/schema/3/d2Payload" xsi:type="egi:EnergyInfrastructureTablePublication"
 lang="es" modelBaseVersion="3" xmlns:com="http://datex2.eu/schema/3/common"
 xmlns:loc="http://datex2.eu/schema/3/locationReferencing" xmlns:egi="http://datex2.eu/schema/3/energyInfrastructure"
 xmlns:fac="http://datex2.eu/schema/3/facilities" xmlns:locx="http://datex2.eu/schema/3/locationExtension"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <com:publicationTime>2026-07-17T10:18:40.592+02:00</com:publicationTime>
  <egi:energyInfrastructureTable id="ELECTROLINERAS" version="20260717">
    <egi:energyInfrastructureSite id="9TOKBPBKRBJVLI0RR4XG" version="">
      <fac:name><com:values><com:value lang="es">Carrer_del_Callao</com:value></com:values></fac:name>
      <fac:lastUpdated>2026-07-15T13:48:52.000+02:00</fac:lastUpdated>
      <fac:locationReference xsi:type="loc:PointLocation">
        <loc:_locationReferenceExtension><loc:facilityLocation><locx:address>
          <locx:postcode>7006</locx:postcode>
          <locx:addressLine order="1"><locx:type>generalTextLine</locx:type><locx:text>
            <com:values><com:value lang="es">Dirección: Carrer Del Callao, S/N</com:value></com:values>
          </locx:text></locx:addressLine>
          <locx:addressLine order="2"><locx:type>generalTextLine</locx:type><locx:text>
            <com:values><com:value lang="es">Municipio: Palma</com:value></com:values>
          </locx:text></locx:addressLine>
        </locx:address></loc:facilityLocation></loc:_locationReferenceExtension>
        <loc:coordinatesForDisplay>
          <loc:latitude>39.564915</loc:latitude>
          <loc:longitude>2.670856</loc:longitude>
        </loc:coordinatesForDisplay>
      </fac:locationReference>
      <fac:operator xsi:type="fac:OrganisationSpecification" id="ES*EMA" version="">
        <fac:name><com:values><com:value lang="es">EMAYA, S.A.</com:value></com:values></fac:name>
      </fac:operator>
      <egi:typeOfSite>onstreet</egi:typeOfSite>
      <egi:energyInfrastructureStation id="9TOKBPBKRBJVLI0RR4XG_1" version="">
        <egi:authenticationAndIdentificationMethods>apps</egi:authenticationAndIdentificationMethods>
        <egi:refillPoint xsi:type="egi:ElectricChargingPoint" id="RP1" version="">
          <fac:name><com:values><com:value lang="es">ES*EMA*EMELIBMALLO531</com:value></com:values></fac:name>
          <egi:connector>
            <egi:connectorType>iec62196T2</egi:connectorType>
            <egi:connectorFormat>socket</egi:connectorFormat>
            <egi:maxPowerAtSocket>22170.0</egi:maxPowerAtSocket>
          </egi:connector>
          <egi:connector>
            <egi:connectorType>iec62196T2COMBO</egi:connectorType>
            <egi:connectorFormat>cable</egi:connectorFormat>
            <egi:maxPowerAtSocket>50000.0</egi:maxPowerAtSocket>
          </egi:connector>
        </egi:refillPoint>
      </egi:energyInfrastructureStation>
    </egi:energyInfrastructureSite>
  </egi:energyInfrastructureTable>
</d2:payload>
"""


class DgtNapFeedHostTests(unittest.TestCase):
    def test_feed_uses_the_resource_url_listed_on_the_nap_dataset_page(self) -> None:
        # infocar.dgt.es answered HTTP 502 from 2026-10-21 while nap.dgt.es served the same file.
        self.assertTrue(ing.DGT_NAP_FEED_URL.startswith("https://nap.dgt.es/datex2/v3/miterd/"))


class DgtNapParserTests(unittest.TestCase):
    def parse(self):
        return ing.parse_dgt_nap_feed(io.BytesIO(FIXTURE.encode("utf-8")), minimum_sites=1)

    def test_site_fields_extracted(self) -> None:
        sites = self.parse()
        self.assertEqual(len(sites), 1)
        site = sites[0]
        self.assertEqual(site["site_id"], "9TOKBPBKRBJVLI0RR4XG")
        self.assertEqual(site["name"], "Carrer_del_Callao")
        self.assertAlmostEqual(site["lat"], 39.564915)
        self.assertAlmostEqual(site["lon"], 2.670856)
        self.assertEqual(site["city"], "Palma")
        self.assertEqual(site["address"], "Carrer Del Callao, S/N")
        self.assertEqual(site["operator_id"], "ES*EMA")
        self.assertEqual(site["operator_name"], "EMAYA, S.A.")
        self.assertEqual(site["type_of_site"], "onstreet")
        self.assertEqual(site["port_count"], 1)
        self.assertEqual(site["evse_ids"], ["ES*EMA*EMELIBMALLO531"])
        self.assertEqual(site["auth_methods"], ["apps"])
        self.assertEqual(site["connector_rows"], [("Type 2", 22.2), ("CCS2", 50.0)])

    def test_minimum_sites_guard(self) -> None:
        with self.assertRaises(RuntimeError):
            ing.parse_dgt_nap_feed(io.BytesIO(FIXTURE.encode("utf-8")), minimum_sites=2)

    def test_records_are_corroboration_only(self) -> None:
        records = ing.nap_records_from_sites(self.parse())
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertIsNone(record["price_min"])
        self.assertTrue((record["raw"] or {}).get("corroboration_only"))
        self.assertEqual(record["reliability"], "official-charger-location-price-unknown")
        self.assertEqual(record["city"], "Palma")
        # Operator handoff: EMAYA points at the MELIB app — attribution, not a data path.
        self.assertEqual((record["raw"] or {}).get("operator_app", {}).get("app"), "MELIB")
        source = record["sources"][0]
        self.assertEqual(source["attribution"], "DGT / MITECO — Punto de Acceso Nacional")
        self.assertEqual(source["license"], "CC BY")
        # Two connector groups survive with power in kW.
        powers = sorted(c["power_kw"] for c in record["connectors"])
        self.assertEqual(powers, [22.2, 50.0])

    def test_operator_app_mapping_covers_owner_named_networks(self) -> None:
        cases = {
            ("FENIE ENERGIA, SA", "ES*FEN"): "Recarga Feníe Energía",
            ("IBERDROLA CLIENTES S.A.U", "ES*IBD"): "Recarga Pública Iberdrola | bp pulse",
            ("CHARGING TOGETHER SL", "ES*CTO"): "Iberdrola | bp pulse",
            ("EMAYA, S.A.", "ES*EMA"): "MELIB",
            ("Gestión Inteligente de Cargas S.L.", "ES*GIC"): "GiC (eCoVe)",
            ("ACCIONA ENERGIA", "ES*ACC"): "ACCIONA recarga VE",
        }
        for (name, party_id), expected_app in cases.items():
            mapped = ing.nap_operator_app(name, party_id)
            self.assertIsNotNone(mapped, name)
            self.assertEqual(mapped["app"], expected_app)
        self.assertIsNone(ing.nap_operator_app("Unknown Local CPO", "ES*XXX"))


if __name__ == "__main__":
    unittest.main()
