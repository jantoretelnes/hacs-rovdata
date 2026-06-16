from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_MAX_AGE_DAYS,
    DEFAULT_MAX_AGE_DAYS,
    CONF_ARCGIS_TOKEN,
    GBIF_TAXON_KEY,
    GBIF_API_URL,
    GBIF_PAGE_LIMIT,
    ARCGIS_WOLF_URL,
    SCAN_INTERVAL_HOURS,
    ZONE_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


def _bounding_box(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    delta_lat = radius_m / 111_000
    delta_lon = radius_m / (111_000 * math.cos(math.radians(lat)))
    return (
        round(lat - delta_lat, 6),
        round(lat + delta_lat, 6),
        round(lon - delta_lon, 6),
        round(lon + delta_lon, 6),
    )


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    """Compute centroid of a polygon ring [[lon, lat], ...]."""
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return sum(lats) / len(lats), sum(lons) / len(lons)


class RovdataCoordinator(DataUpdateCoordinator[dict]):
    """Fetches wolf observations from GBIF and masked areas from ArcGIS."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=SCAN_INTERVAL_HOURS),
        )
        self._entry = entry

    def _get_option(self, key: str, default):
        return self._entry.options.get(key, self._entry.data.get(key, default))

    @property
    def max_age_days(self) -> int:
        return self._get_option(CONF_MAX_AGE_DAYS, DEFAULT_MAX_AGE_DAYS)

    @property
    def arcgis_token(self) -> str:
        return self._get_option(CONF_ARCGIS_TOKEN, "")

    def _get_rovdata_zones(self) -> list[dict]:
        zones = []
        for state in self.hass.states.async_all("zone"):
            if not state.entity_id.startswith(f"zone.{ZONE_PREFIX}"):
                continue
            attrs = state.attributes
            lat = attrs.get("latitude")
            lon = attrs.get("longitude")
            radius = attrs.get("radius", 10_000)
            if lat is None or lon is None:
                continue
            zones.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.name,
                    "latitude": float(lat),
                    "longitude": float(lon),
                    "radius": float(radius),
                }
            )
        return zones

    # ── GBIF ──────────────────────────────────────────────────────────────────

    async def _fetch_gbif_zone(
        self,
        session: aiohttp.ClientSession,
        zone: dict,
        cutoff_date: str,
    ) -> dict[str, dict]:
        lat_min, lat_max, lon_min, lon_max = _bounding_box(
            zone["latitude"], zone["longitude"], zone["radius"]
        )
        params = {
            "taxonKey": GBIF_TAXON_KEY,
            "decimalLatitude": f"{lat_min},{lat_max}",
            "decimalLongitude": f"{lon_min},{lon_max}",
            "hasCoordinate": "true",
            "occurrenceStatus": "PRESENT",
            "eventDate": f"{cutoff_date},{datetime.utcnow().strftime('%Y-%m-%d')}",
            "limit": GBIF_PAGE_LIMIT,
            "offset": 0,
        }

        results: dict[str, dict] = {}
        while True:
            try:
                async with session.get(
                    GBIF_API_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("GBIF %s for zone %s", resp.status, zone["name"])
                        break
                    data = await resp.json()
            except Exception as err:
                _LOGGER.error("GBIF fetch error zone %s: %s", zone["name"], err)
                break

            for rec in data.get("results", []):
                oid = str(rec.get("occurrenceID") or rec.get("gbifID") or "")
                if not oid:
                    continue
                results[oid] = {
                    "source": "gbif",
                    "occurrence_id": oid,
                    "gbif_id": str(rec.get("gbifID", "")),
                    "latitude": rec.get("decimalLatitude"),
                    "longitude": rec.get("decimalLongitude"),
                    "event_date": rec.get("eventDate", ""),
                    "locality": rec.get("locality", ""),
                    "state_province": rec.get("stateProvince", ""),
                    "individual_count": rec.get("individualCount", 1) or 1,
                    "recorded_by": rec.get("recordedBy", ""),
                    "dataset_name": rec.get("datasetName", ""),
                    "remarks": rec.get("occurrenceRemarks", ""),
                    "zone_name": zone["name"],
                    "zone_entity_id": zone["entity_id"],
                }

            if data.get("endOfRecords", True):
                break
            params["offset"] += GBIF_PAGE_LIMIT

        return results

    # ── ArcGIS ────────────────────────────────────────────────────────────────

    async def _fetch_arcgis_zone(
        self,
        session: aiohttp.ClientSession,
        zone: dict,
        token: str,
    ) -> dict[str, dict]:
        lat_min, lat_max, lon_min, lon_max = _bounding_box(
            zone["latitude"], zone["longitude"], zone["radius"]
        )
        params = {
            "geometry": f"{lon_min},{lat_min},{lon_max},{lat_max}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "OBJECTID,MaskeringsruteID,Artsnavn,VitenskapeligArtsnavn,Datasettnavn,Institusjon",
            "outSR": "4326",
            "returnGeometry": "true",
            "f": "json",
            "resultRecordCount": 500,
        }
        if token:
            params["token"] = token

        results: dict[str, dict] = {}
        try:
            async with session.get(
                ARCGIS_WOLF_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("ArcGIS %s for zone %s", resp.status, zone["name"])
                    return results
                data = await resp.json(content_type=None)
        except Exception as err:
            _LOGGER.error("ArcGIS fetch error zone %s: %s", zone["name"], err)
            return results

        if "error" in data:
            _LOGGER.warning("ArcGIS error for zone %s: %s", zone["name"], data["error"])
            return results

        for feat in data.get("features", []):
            attrs = feat.get("attributes", {})
            geom = feat.get("geometry", {})
            rings = geom.get("rings", [])

            objectid = attrs.get("OBJECTID")
            if objectid is None:
                continue
            key = f"area_{objectid}"

            lat, lon = None, None
            if rings:
                lat, lon = _ring_centroid(rings[0])

            results[key] = {
                "source": "arcgis",
                "objectid": objectid,
                "masking_id": attrs.get("MaskeringsruteID", ""),
                "latitude": lat,
                "longitude": lon,
                "art": attrs.get("Artsnavn", "Ulv"),
                "scientific_name": attrs.get("VitenskapeligArtsnavn", ""),
                "dataset_name": attrs.get("Datasettnavn", ""),
                "institution": attrs.get("Institusjon", ""),
                "zone_name": zone["name"],
                "zone_entity_id": zone["entity_id"],
            }

        return results

    # ── Main update ───────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, dict]:
        zones = self._get_rovdata_zones()
        if not zones:
            _LOGGER.debug("No rovdata_ zones found")
            return {}

        cutoff = (datetime.utcnow() - timedelta(days=self.max_age_days)).strftime("%Y-%m-%d")
        token = self.arcgis_token
        combined: dict[str, dict] = {}

        async with aiohttp.ClientSession() as session:
            for zone in zones:
                gbif_data = await self._fetch_gbif_zone(session, zone, cutoff)
                combined.update(gbif_data)

                arcgis_data = await self._fetch_arcgis_zone(session, zone, token)
                combined.update(arcgis_data)

        gbif_count = sum(1 for v in combined.values() if v.get("source") == "gbif")
        arcgis_count = sum(1 for v in combined.values() if v.get("source") == "arcgis")
        _LOGGER.debug(
            "Fetched %d GBIF observations + %d ArcGIS areas across %d zone(s)",
            gbif_count, arcgis_count, len(zones),
        )
        return combined
