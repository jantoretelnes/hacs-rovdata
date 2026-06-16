from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
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
            RovdataWolfSensor(coordinator, key)
            for key in coordinator.data or {}
            if key not in known
        ]
        if new:
            known.update(e.unique_id for e in new)
            async_add_entities(new)

    coordinator.async_add_listener(_add_new_entities)
    _add_new_entities()


class RovdataWolfSensor(CoordinatorEntity[RovdataCoordinator], SensorEntity):
    """Sensor with details for a single wolf observation or masked area."""

    _attr_icon = "mdi:paw"

    def __init__(self, coordinator: RovdataCoordinator, data_key: str) -> None:
        super().__init__(coordinator)
        self._data_key = data_key
        self._attr_unique_id = f"rovdata_sensor_{data_key}"

    @property
    def _obs(self) -> dict:
        return (self.coordinator.data or {}).get(self._data_key, {})

    @property
    def name(self) -> str:
        obs = self._obs
        src = obs.get("source")
        if src == "arcgis":
            mask_id = obs.get("masking_id", str(obs.get("objectid", "")))
            return f"Ulv område {mask_id}"
        if src == "rovbase":
            individ_id = obs.get("individ_id", self._data_key)
            individ_name = obs.get("individ_name", "")
            return f"Ulv {individ_id}" + (f" {individ_name}" if individ_name else "")
        individ_id = obs.get("gbif_id") or obs.get("occurrence_id", self._data_key)[:8]
        return f"Ulv {individ_id}"

    @property
    def device_class(self):
        if self._obs.get("source") in ("gbif", "rovbase"):
            return SensorDeviceClass.DATE
        return None

    @property
    def native_value(self):
        obs = self._obs
        if obs.get("source") == "arcgis":
            return obs.get("masking_id") or str(obs.get("objectid", ""))
        date_str = (obs.get("event_date") or "")[:10]
        if not date_str:
            return None
        try:
            return date.fromisoformat(date_str)
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict:
        obs = self._obs
        src = obs.get("source")
        if src == "arcgis":
            return {
                "kilde": "ArcGIS / Miljødirektoratet",
                "maskeringsrute_id": obs.get("masking_id"),
                "breddegrad": obs.get("latitude"),
                "lengdegrad": obs.get("longitude"),
                "art": obs.get("art"),
                "vitenskapelig_navn": obs.get("scientific_name"),
                "datasett": obs.get("dataset_name"),
                "institusjon": obs.get("institution"),
                "sone": obs.get("zone_name"),
            }
        if src == "rovbase":
            attrs = {
                "kilde": "Rovbase",
                "individ_id": obs.get("individ_id"),
                "individ_navn": obs.get("individ_name"),
                "kjønn": obs.get("kjonn") or None,
                "født_revir": obs.get("fodt_revir") or None,
                "opprinnelse_id": obs.get("opprinnelse_id") or None,
                "breddegrad": obs.get("latitude"),
                "lengdegrad": obs.get("longitude"),
                "lokalitet": obs.get("locality"),
                "kommune": obs.get("municipality"),
                "datatype": obs.get("datatype"),
                "dna_id": obs.get("dna_id") or None,
                "sone": obs.get("zone_name"),
            }
            attrs.update(obs.get("_dt_attrs") or {})
            return {k: v for k, v in attrs.items() if v is not None and v != ""}
        attrs = {
            "kilde": "GBIF / Skandobs",
            "occurrence_id": obs.get("occurrence_id"),
            "gbif_id": obs.get("gbif_id"),
            "dato": (obs.get("event_date") or "")[:10] or None,
            "breddegrad": obs.get("latitude"),
            "lengdegrad": obs.get("longitude"),
            "lokalitet": obs.get("locality") or None,
            "fylke": obs.get("state_province") or None,
            "antall_individer": obs.get("individual_count"),
            "registrert_av": obs.get("recorded_by") or None,
            "datasett": obs.get("dataset_name") or None,
            "merknader": obs.get("remarks") or None,
            "sone": obs.get("zone_name"),
        }
        return {k: v for k, v in attrs.items() if v is not None}

    @property
    def available(self) -> bool:
        return self._data_key in (self.coordinator.data or {})
