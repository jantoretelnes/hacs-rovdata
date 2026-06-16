from __future__ import annotations

import json
import logging
import math
import re
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
    ROVBASE_FEATURE_URL,
    SCAN_INTERVAL_HOURS,
    ZONE_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

_WKT_POINT = re.compile(r"POINT\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)")


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


def _utm33n_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """Convert EPSG:25833 (ETRS89/UTM zone 33N) to WGS84 lat/lon."""
    k0 = 0.9996
    a = 6_378_137.0
    e2 = 0.00669437999014
    e_prime2 = e2 / (1 - e2)
    lon0 = math.radians(15.0)  # zone 33 central meridian

    x = easting - 500_000.0
    y = northing

    M = y / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))

    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )

    N1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    T1 = math.tan(phi1) ** 2
    C1 = e_prime2 * math.cos(phi1) ** 2
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    D = x / (N1 * k0)

    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
        D ** 2 / 2
        - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * e_prime2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * e_prime2 - 3 * C1 ** 2)
        * D ** 6
        / 720
    )
    lon = lon0 + (
        D
        - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * e_prime2 + 24 * T1 ** 2) * D ** 5 / 120
    ) / math.cos(phi1)

    return math.degrees(lat), math.degrees(lon)


def _parse_wkt_point(wkt: str) -> tuple[float, float] | None:
    """Parse WKT POINT in UTM33N, return (lat, lon) WGS84 or None."""
    m = _WKT_POINT.match(wkt or "")
    if not m:
        return None
    try:
        easting, northing = float(m.group(1)), float(m.group(2))
        return _utm33n_to_wgs84(easting, northing)
    except Exception:
        return None


def _point_in_zone(lat: float, lon: float, zone: dict) -> bool:
    """Check if a point is within the zone's radius."""
    delta_lat = lat - zone["latitude"]
    delta_lon = lon - zone["longitude"]
    dist_m = math.sqrt(
        (delta_lat * 111_000) ** 2
        + (delta_lon * 111_000 * math.cos(math.radians(zone["latitude"]))) ** 2
    )
    return dist_m <= zone["radius"]


class RovdataCoordinator(DataUpdateCoordinator[dict]):
    """Fetches wolf observations from Rovbase, GBIF, and ArcGIS."""

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

    # ── Rovbase ───────────────────────────────────────────────────────────────

    async def _fetch_rovbase(
        self,
        session: aiohttp.ClientSession,
        zones: list[dict],
        cutoff_date: str,
    ) -> dict[str, dict]:
        """Fetch wolf observations from Rovbase API and geo-filter by zones."""
        body = {
            "LanguageCode": "nb",
            "SearchFilter": {
                "Carnivore": [1],
                "CarnivoreDamage": [1, 2, 3, 4, 5],
                "Evaluation": [1, 2, 3],
                "Observation": [1, 2, 3, 11, 12],
                "Offspring": False,
                "FromDate": cutoff_date,
                "ToDate": datetime.utcnow().strftime("%Y-%m-%d"),
                "Country": [],
                "Region": [],
                "County": [],
                "Municipality": [],
                "IndividualNameOrID": "",
                "Barcode": [],
                "Rovdjursforum": False,
                "ID": [],
            },
        }

        try:
            async with session.post(
                ROVBASE_FEATURE_URL,
                data=json.dumps(body),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Referer": "https://www.rovbase.no/filter",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("Rovbase api/Feature returned %s", resp.status)
                    return {}
                records = await resp.json(content_type=None)
        except Exception as err:
            _LOGGER.error("Rovbase fetch error: %s", err)
            return {}

        # Build result keyed by individID (most recent observation per individual per zone)
        # individID may be empty for anonymous samples — fall back to dnaID
        best: dict[str, dict] = {}

        for rec in records:
            wkt = rec.get("wkt", "")
            coords = _parse_wkt_point(wkt)
            if coords is None:
                continue
            lat, lon = coords

            # Find which zone(s) this point belongs to
            matching_zones = [z for z in zones if _point_in_zone(lat, lon, z)]
            if not matching_zones:
                continue

            individ_raw = rec.get("individ") or {}
            individ = individ_raw[0] if isinstance(individ_raw, list) else individ_raw
            individ_id = (individ.get("individID") or "").strip()
            individ_name = (individ.get("individnavn") or "").strip()
            if not individ_id:
                individ_id = rec.get("dnaID") or rec.get("id") or ""
            if not individ_id:
                continue

            dato = rec.get("dato", "")
            date_str = dato[:10] if dato else ""

            for zone in matching_zones:
                key = f"rovbase_{individ_id}"
                existing = best.get(key)
                # Keep newest observation
                if existing and existing.get("event_date", "") >= date_str:
                    continue
                best[key] = {
                    "source": "rovbase",
                    "individ_id": individ_id,
                    "individ_name": individ_name,
                    "latitude": round(lat, 6),
                    "longitude": round(lon, 6),
                    "event_date": date_str,
                    "datatype": rec.get("datatype", ""),
                    "locality": rec.get("funnsted", ""),
                    "municipality": rec.get("kommune", ""),
                    "dna_id": rec.get("dnaID", ""),
                    "zone_name": zone["name"],
                    "zone_entity_id": zone["entity_id"],
                }

        return best

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
                results[f"gbif_{oid}"] = {
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
            rovbase_data = await self._fetch_rovbase(session, zones, cutoff)
            combined.update(rovbase_data)

            for zone in zones:
                gbif_data = await self._fetch_gbif_zone(session, zone, cutoff)
                combined.update(gbif_data)

                arcgis_data = await self._fetch_arcgis_zone(session, zone, token)
                combined.update(arcgis_data)

        rovbase_count = sum(1 for v in combined.values() if v.get("source") == "rovbase")
        gbif_count = sum(1 for v in combined.values() if v.get("source") == "gbif")
        arcgis_count = sum(1 for v in combined.values() if v.get("source") == "arcgis")
        _LOGGER.debug(
            "Fetched %d Rovbase + %d GBIF + %d ArcGIS across %d zone(s)",
            rovbase_count, gbif_count, arcgis_count, len(zones),
        )
        return combined
