from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RovdataCoordinator
from .sensor import _device_info


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
    _attr_icon = "mdi:paw"
    _attr_source_type = SourceType.GPS
    _attr_has_entity_name = True
    _attr_name = "Posisjon"

    def __init__(self, coordinator: RovdataCoordinator, data_key: str) -> None:
        super().__init__(coordinator)
        self._data_key = data_key
        self._attr_unique_id = f"rovdata_tracker_{data_key}"

    @property
    def _obs(self) -> dict:
        return (self.coordinator.data or {}).get(self._data_key, {})

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._data_key, self._obs)

    @property
    def latitude(self) -> float | None:
        return self._obs.get("latitude")

    @property
    def longitude(self) -> float | None:
        return self._obs.get("longitude")

    @property
    def extra_state_attributes(self) -> dict:
        obs = self._obs
        src = obs.get("source")
        if src == "arcgis":
            return {
                "kilde": "ArcGIS / Miljødirektoratet",
                "maskeringsrute_id": obs.get("masking_id"),
                "sone": obs.get("zone_name"),
            }
        if src == "rovbase":
            return {
                "kilde": "Rovbase",
                "dato": obs.get("event_date"),
                "kommune": obs.get("municipality"),
                "lokalitet": obs.get("locality"),
                "sone": obs.get("zone_name"),
            }
        return {
            "kilde": "GBIF / Skandobs",
            "dato": obs.get("event_date"),
            "lokalitet": obs.get("locality"),
            "sone": obs.get("zone_name"),
        }

    @property
    def available(self) -> bool:
        return self._data_key in (self.coordinator.data or {})
