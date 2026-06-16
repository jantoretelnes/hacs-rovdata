from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RovdataCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RovdataCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new = [
            RovdataWolfTracker(coordinator, key)
            for key in coordinator.data or {}
            if key not in known
            and (coordinator.data or {}).get(key, {}).get("latitude") is not None
        ]
        if new:
            known.update(e.unique_id for e in new)
            async_add_entities(new)

    coordinator.async_add_listener(_add_new_entities)
    _add_new_entities()


class RovdataWolfTracker(CoordinatorEntity[RovdataCoordinator], TrackerEntity):
    """Represents a wolf observation or masked area as a map tracker."""

    _attr_icon = "mdi:paw"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: RovdataCoordinator, data_key: str) -> None:
        super().__init__(coordinator)
        self._data_key = data_key
        self._attr_unique_id = f"rovdata_tracker_{data_key}"

    @property
    def _obs(self) -> dict:
        return (self.coordinator.data or {}).get(self._data_key, {})

    @property
    def name(self) -> str:
        obs = self._obs
        if obs.get("source") == "arcgis":
            zone = obs.get("zone_name", "")
            mask_id = obs.get("masking_id", str(obs.get("objectid", "")))
            return f"Ulv område – {mask_id or zone}"
        locality = obs.get("locality") or obs.get("state_province") or ""
        date = (obs.get("event_date") or "")[:10]
        label = locality or date or self._data_key[:8]
        return f"Ulv – {label}"

    @property
    def latitude(self) -> float | None:
        return self._obs.get("latitude")

    @property
    def longitude(self) -> float | None:
        return self._obs.get("longitude")

    @property
    def extra_state_attributes(self) -> dict:
        obs = self._obs
        if obs.get("source") == "arcgis":
            return {
                "kilde": "ArcGIS / Miljødirektoratet",
                "maskeringsrute_id": obs.get("masking_id"),
                "art": obs.get("art"),
                "vitenskapelig_navn": obs.get("scientific_name"),
                "datasett": obs.get("dataset_name"),
                "institusjon": obs.get("institution"),
                "sone": obs.get("zone_name"),
            }
        return {
            "kilde": "GBIF / Skandobs",
            "occurrence_id": obs.get("occurrence_id"),
            "gbif_id": obs.get("gbif_id"),
            "dato": obs.get("event_date"),
            "lokalitet": obs.get("locality"),
            "fylke": obs.get("state_province"),
            "antall_individer": obs.get("individual_count"),
            "registrert_av": obs.get("recorded_by"),
            "datasett": obs.get("dataset_name"),
            "merknader": obs.get("remarks"),
            "sone": obs.get("zone_name"),
        }

    @property
    def available(self) -> bool:
        return self._data_key in (self.coordinator.data or {})
