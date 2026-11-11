"""Attribution lines stamped on every charger row.

The owner accepted the reuse-rights risk on 10 Nov 2026 and asked for the
attribution line on each row, shown in charger detail. Existing text is kept.
"""
from __future__ import annotations

from typing import Any, Optional

# Longer, more specific needles first. Match source name and URL only.
_RULES = (
    ("openstreetmap", "© OpenStreetMap contributors"),
    ("openchargemap", "OpenChargeMap"),
    ("mapareve", "MapaREVE"),
    ("gaia", "Gaia Charge"),
    ("fenie", "Feníe Energía — Red de recarga (public map)"),
    ("repsol", "Repsol"),
    ("electromaps", "Electromaps"),
    ("chargemap", "Chargemap"),
    ("consum", "Consum"),
    ("endolla", "Ajuntament de Barcelona / Endolla"),
    ("madrid public", "Ayuntamiento de Madrid"),
    ("zaragoza", "Ayuntamiento de Zaragoza"),
    ("valència opendata", "Ajuntament de València"),
    ("valencia opendata", "Ajuntament de València"),
    ("municipal charger", "Ajuntament de València"),
    ("dgt", "DGT / MITECO — Punto de Acceso Nacional"),
    ("miteco", "DGT / MITECO — Punto de Acceso Nacional"),
    ("icaen", "Institut Català d'Energia (ICAEN) — Generalitat de Catalunya"),
    ("navarra", "Gobierno de Navarra — IDENA"),
    ("idena", "Gobierno de Navarra — IDENA"),
    ("castilla", "Junta de Castilla y León"),
    ("canarias", "Canarias te recarga"),
    ("lidl", "Lidl"),
    ("etecnic", "Etecnic Movilidad Eléctrica"),
    ("zunder", "Zunder (Grupo Easycharger)"),
    ("spirii", "Spirii"),
    ("moraleja", "ACCIONA / Moraleja Green"),
    ("acciona", "ACCIONA / Moraleja Green"),
    ("movem", "Diputación de Badajoz"),
    ("badajoz", "Diputación de Badajoz"),
    ("tesla", "Tesla"),
    ("mercadona", "Mercadona"),
)


def attribution_for(*parts: Any) -> Optional[str]:
    blob = " ".join(str(part or "") for part in parts).casefold()
    if not blob.strip():
        return None
    for needle, line in _RULES:
        if needle in blob:
            return line
    return None


def stamp_row(row: dict) -> None:
    """Fill a missing attribution on each source entry. Never replaces one."""
    if not isinstance(row, dict):
        return
    sources = row.get("sources")
    if not isinstance(sources, list) or not sources:
        if row.get("source"):
            sources = [{"name": row.get("source"), "url": row.get("source_url")}]
            row["sources"] = sources
        else:
            return
    for source in sources:
        if not isinstance(source, dict):
            continue
        if str(source.get("attribution") or "").strip():
            continue
        # Match this source entry on its own. A merged row's primary source
        # (often OpenStreetMap) must not overwrite a Red MOVEM, Moraleja or
        # other corroborating line. Fall back to the row only when the entry
        # itself names no known source.
        line = attribution_for(source.get("name"), source.get("url"))
        if not line:
            line = attribution_for(row.get("source"), row.get("source_url"))
        if line:
            source["attribution"] = line
