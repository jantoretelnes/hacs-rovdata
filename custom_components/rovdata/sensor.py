from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RovdataCoordinator


@dataclass(frozen=True)
class RovdataSensorDef:
    key: str
    name: str
    device_class: str | None
    value_fn: Callable[[dict], Any]
    sources: tuple[str, ...]  # which sources this sensor applies to
    unit: str | None = None


_ROVBASE_SENSORS: list[RovdataSensorDef] = [
    RovdataSensorDef("dato", "Dato", SensorDeviceClass.DATE,
                     lambda o: _parse_date(o.get("event_date")),
                     ("rovbase",)),
    RovdataSensorDef("kjonn", "Kjønn", None,
                     lambda o: o.get("kjonn") or None,
                     ("rovbase",)),
    RovdataSensorDef("fodt_revir", "Født revir", None,
                     lambda o: o.get("fodt_revir") or None,
                     ("rovbase",)),
    RovdataSensorDef("datatype", "Observasjonstype", None,
                     lambda o: _dt_label(o.get("datatype")),
                     ("rovbase",)),
    RovdataSensorDef("kontrollstatus", "Kontrollstatus", None,
                     lambda o: (o.get("_dt_attrs") or {}).get("kontrollstatus") or None,
                     ("rovbase",)),
    RovdataSensorDef("vurdering", "Vurdering", None,
                     lambda o: (o.get("_dt_attrs") or {}).get("vurdering") or None,
                     ("rovbase",)),
]

_GBIF_SENSORS: list[RovdataSensorDef] = [
    RovdataSensorDef("dato", "Dato", SensorDeviceClass.DATE,
                     lambda o: _parse_date(o.get("event_date")),
                     ("gbif",)),
    RovdataSensorDef("lokalitet", "Lokalitet", None,
                     lambda o: o.get("locality") or None,
                     ("gbif",)),
    RovdataSensorDef("antall", "Antall individer", None,
                     lambda o: o.get("individual_count"),
                     ("gbif",)),
]

_ARCGIS_SENSORS: list[RovdataSensorDef] = [
    RovdataSensorDef("masking_id", "Maskeringsrute", None,
                     lambda o: o.get("masking_id") or None,
                     ("arcgis",)),
    RovdataSensorDef("art", "Art", None,
                     lambda o: o.get("art") or None,
                     ("arcgis",)),
]

_ALL_SENSORS = _ROVBASE_SENSORS + _GBIF_SENSORS + _ARCGIS_SENSORS

_DATATYPE_LABEL = {
    "dna": "DNA-prøve",
    "DodeRovdyr": "Dødt rovdyr",
    "Rovviltobservasjon": "Rovviltobservasjon",
    "Rovviltskade": "Rovviltskade",
}


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str[:10])
    except ValueError:
        return None


def _dt_label(datatype: str | None) -> str | None:
    return _DATATYPE_LABEL.get(datatype or "", datatype) or None


def _device_info(data_key: str, obs: dict) -> DeviceInfo:
    src = obs.get("source", "")
    if src == "rovbase":
        individ_id = obs["individ_id"]
        individ_name = obs.get("individ_name", "")
        name = f"Ulv {individ_id}" + (f" {individ_name}" if individ_name else "")
        return DeviceInfo(
            identifiers={(DOMAIN, individ_id)},
            name=name,
            manufacturer="Rovbase",
            model=_DATATYPE_LABEL.get(obs.get("datatype", ""), obs.get("datatype", "")),
        )
    if src == "gbif":
        gbif_id = obs.get("gbif_id", data_key)
        return DeviceInfo(
            identifiers={(DOMAIN, data_key)},
            name=f"Ulv {gbif_id}",
            manufacturer="GBIF / Skandobs",
            model=obs.get("dataset_name", ""),
        )
    # arcgis
    return DeviceInfo(
        identifiers={(DOMAIN, data_key)},
        name=f"Ulv område {obs.get('masking_id', data_key)}",
        manufacturer="Miljødirektoratet",
        model="ArcGIS",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RovdataCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new: list[RovdataWolfSensor] = []
        for data_key, obs in (coordinator.data or {}).items():
            src = obs.get("source", "")
            defs = (
                _ROVBASE_SENSORS if src == "rovbase"
                else _GBIF_SENSORS if src == "gbif"
                else _ARCGIS_SENSORS
            )
            for sdef in defs:
                uid = f"rovdata_sensor_{data_key}_{sdef.key}"
                if uid not in known:
                    known.add(uid)
                    new.append(RovdataWolfSensor(coordinator, data_key, sdef))
        if new:
            async_add_entities(new)

    coordinator.async_add_listener(_add_new_entities)
    _add_new_entities()


class RovdataWolfSensor(CoordinatorEntity[RovdataCoordinator], SensorEntity):
    _attr_icon = "mdi:paw"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RovdataCoordinator,
        data_key: str,
        sdef: RovdataSensorDef,
    ) -> None:
        super().__init__(coordinator)
        self._data_key = data_key
        self._sdef = sdef
        self._attr_unique_id = f"rovdata_sensor_{data_key}_{sdef.key}"
        self._attr_name = sdef.name
        self._attr_device_class = sdef.device_class

    @property
    def _obs(self) -> dict:
        return (self.coordinator.data or {}).get(self._data_key, {})

    @property
    def device_info(self) -> DeviceInfo:
        return _device_info(self._data_key, self._obs)

    @property
    def native_value(self):
        return self._sdef.value_fn(self._obs)

    @property
    def available(self) -> bool:
        return self._data_key in (self.coordinator.data or {})
