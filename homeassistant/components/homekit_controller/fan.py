"""Support for Homekit fans."""

from __future__ import annotations

from typing import Any

from aiohomekit.model.characteristics import CharacteristicsTypes
from aiohomekit.model.characteristics.const import (
    TargetAirPurifierStateValues,
    TargetFanStateValues,
)
from aiohomekit.model.services import Service, ServicesTypes
from propcache.api import cached_property

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from . import KNOWN_DEVICES
from .connection import HKDevice
from .entity import HomeKitEntity

# 0 is clockwise, 1 is counter-clockwise. The match to forward and reverse is so that
# its consistent with homeassistant.components.homekit.
DIRECTION_TO_HK = {
    DIRECTION_REVERSE: 1,
    DIRECTION_FORWARD: 0,
}
HK_DIRECTION_TO_HA = {v: k for (k, v) in DIRECTION_TO_HK.items()}

PRESET_AUTO = "auto"


class BaseHomeKitFan(HomeKitEntity, FanEntity):
    """Representation of a Homekit fan."""

    # This must be set in subclasses to the name of a boolean characteristic
    # that controls whether the fan is on or off.
    on_characteristic: str
    preset_char = CharacteristicsTypes.FAN_STATE_TARGET
    preset_manual_value: int = TargetFanStateValues.MANUAL
    preset_automatic_value: int = TargetFanStateValues.AUTOMATIC

    @callback
    def _async_reconfigure(self) -> None:
        """Reconfigure entity."""
        self._async_clear_property_cache(
            (
                "_speed_range",
                "_min_speed",
                "_max_speed",
                "preset_modes",
                "speed_count",
                "supported_features",
            )
        )
        super()._async_reconfigure()

    def get_characteristic_types(self) -> list[str]:
        """Define the homekit characteristics the entity cares about."""
        types = [
            CharacteristicsTypes.SWING_MODE,
            CharacteristicsTypes.ROTATION_DIRECTION,
            CharacteristicsTypes.ROTATION_SPEED,
            self.on_characteristic,
        ]
        if self.service.has(self.preset_char):
            types.append(self.preset_char)
        return types

    @property
    def is_on(self) -> bool:
        """Return true if device is on."""
        return self.service.value(self.on_characteristic) == 1

    @cached_property
    def _speed_range(self) -> tuple[int, int]:
        """Return the speed range."""
        return (self._min_speed, self._max_speed)

    @cached_property
    def _min_speed(self) -> int:
        """Return the minimum speed."""
        return (
            round(self.service[CharacteristicsTypes.ROTATION_SPEED].minValue or 0) + 1
        )

    @cached_property
    def _max_speed(self) -> int:
        """Return the minimum speed."""
        return round(self.service[CharacteristicsTypes.ROTATION_SPEED].maxValue or 100)

    @property
    def percentage(self) -> int:
        """Return the current speed percentage."""
        if not self.is_on:
            return 0

        return ranged_value_to_percentage(
            self._speed_range, self.service.value(CharacteristicsTypes.ROTATION_SPEED)
        )

    @property
    def current_direction(self) -> str:
        """Return the current direction of the fan."""
        direction = self.service.value(CharacteristicsTypes.ROTATION_DIRECTION)
        return HK_DIRECTION_TO_HA[direction]

    @property
    def oscillating(self) -> bool:
        """Return whether or not the fan is currently oscillating."""
        oscillating = self.service.value(CharacteristicsTypes.SWING_MODE)
        return oscillating == 1

    @cached_property
    def supported_features(self) -> FanEntityFeature:
        """Flag supported features."""
        features = FanEntityFeature.TURN_OFF | FanEntityFeature.TURN_ON

        if self.service.has(CharacteristicsTypes.ROTATION_DIRECTION):
            features |= FanEntityFeature.DIRECTION

        if self.service.has(CharacteristicsTypes.ROTATION_SPEED):
            features |= FanEntityFeature.SET_SPEED

        if self.service.has(CharacteristicsTypes.SWING_MODE):
            features |= FanEntityFeature.OSCILLATE

        if self.service.has(self.preset_char):
            features |= FanEntityFeature.PRESET_MODE

        return features

    @cached_property
    def speed_count(self) -> int:
        """Speed count for the fan."""
        return round(
            min(self._max_speed, 100)
            / max(1, self.service[CharacteristicsTypes.ROTATION_SPEED].minStep or 0)
        )

    @cached_property
    def preset_modes(self) -> list[str]:
        """Return the preset modes."""
        return [PRESET_AUTO] if self.service.has(self.preset_char) else []

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if (
            self.service.has(self.preset_char)
            and self.service.value(self.preset_char) == self.preset_automatic_value
        ):
            return PRESET_AUTO
        return None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        if self.service.has(self.preset_char):
            await self.async_put_characteristics(
                {
                    self.preset_char: self.preset_automatic_value
                    if preset_mode == PRESET_AUTO
                    else self.preset_manual_value
                }
            )

    async def async_set_direction(self, direction: str) -> None:
        """Set the direction of the fan."""
        await self.async_put_characteristics(
            {CharacteristicsTypes.ROTATION_DIRECTION: DIRECTION_TO_HK[direction]}
        )

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan."""
        if percentage == 0:
            await self.async_turn_off()
            return

        characteristics = {
            CharacteristicsTypes.ROTATION_SPEED: round(
                percentage_to_ranged_value(self._speed_range, percentage)
            )
        }

        if FanEntityFeature.PRESET_MODE in self.supported_features:
            characteristics[self.preset_char] = self.preset_manual_value

        await self.async_put_characteristics(characteristics)

    async def async_oscillate(self, oscillating: bool) -> None:
        """Oscillate the fan."""
        await self.async_put_characteristics(
            {CharacteristicsTypes.SWING_MODE: 1 if oscillating else 0}
        )

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the specified fan on."""
        characteristics: dict[str, Any] = {}

        if not self.is_on:
            characteristics[self.on_characteristic] = True

        if preset_mode == PRESET_AUTO:
            characteristics[self.preset_char] = self.preset_automatic_value
        elif (
            percentage is not None
            and FanEntityFeature.SET_SPEED in self.supported_features
        ):
            characteristics[CharacteristicsTypes.ROTATION_SPEED] = round(
                percentage_to_ranged_value(self._speed_range, percentage)
            )
            if FanEntityFeature.PRESET_MODE in self.supported_features:
                characteristics[self.preset_char] = self.preset_manual_value

        if characteristics:
            await self.async_put_characteristics(characteristics)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the specified fan off."""
        await self.async_put_characteristics({self.on_characteristic: False})


class HomeKitFanV1(BaseHomeKitFan):
    """Implement fan support for public.hap.service.fan."""

    on_characteristic = CharacteristicsTypes.ON


class HomeKitFanV2(BaseHomeKitFan):
    """Implement fan support for public.hap.service.fanv2."""

    on_characteristic = CharacteristicsTypes.ACTIVE


class HomeKitAirPurifer(HomeKitFanV2):
    """Implement air purifier support for public.hap.service.airpurifier."""

    preset_char = CharacteristicsTypes.AIR_PURIFIER_STATE_TARGET
    preset_manual_value = TargetAirPurifierStateValues.MANUAL
    preset_automatic_value = TargetAirPurifierStateValues.AUTOMATIC


ENTITY_TYPES = {
    ServicesTypes.FAN: HomeKitFanV1,
    ServicesTypes.FAN_V2: HomeKitFanV2,
    ServicesTypes.AIR_PURIFIER: HomeKitAirPurifer,
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Homekit fans."""
    hkid: str = config_entry.data["AccessoryPairingID"]
    conn: HKDevice = hass.data[KNOWN_DEVICES][hkid]

    @callback
    def async_add_service(service: Service) -> bool:
        if not (entity_class := ENTITY_TYPES.get(service.type)):
            return False
        info = {"aid": service.accessory.aid, "iid": service.iid}
        entity: HomeKitEntity = entity_class(conn, info)
        conn.async_migrate_unique_id(
            entity.old_unique_id, entity.unique_id, Platform.FAN
        )
        async_add_entities([entity])
        return True

    conn.add_listener(async_add_service)
