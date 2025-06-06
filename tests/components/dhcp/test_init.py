"""Test the DHCP discovery integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import datetime
import threading
from typing import Any, cast
from unittest.mock import patch

import aiodhcpwatcher
import pytest
from scapy import interfaces
from scapy.error import Scapy_Exception
from scapy.layers.dhcp import DHCP
from scapy.layers.l2 import Ether

from homeassistant import config_entries
from homeassistant.components import dhcp
from homeassistant.components.device_tracker import (
    ATTR_HOST_NAME,
    ATTR_IP,
    ATTR_MAC,
    ATTR_SOURCE_TYPE,
    CONNECTED_DEVICE_REGISTERED,
    SourceType,
)
from homeassistant.components.dhcp.const import DOMAIN
from homeassistant.components.dhcp.models import DHCPData
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_HOMEASSISTANT_STOP,
    STATE_HOME,
    STATE_NOT_HOME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.discovery_flow import DiscoveryKey
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from tests.common import (
    MockConfigEntry,
    MockModule,
    async_fire_time_changed,
    import_and_test_deprecated_constant,
    mock_integration,
)

# connect b8:b7:f1:6d:b5:33 192.168.210.56
RAW_DHCP_REQUEST = (
    b"\xff\xff\xff\xff\xff\xff\xb8\xb7\xf1m\xb53\x08\x00E\x00\x01P\x06E"
    b"\x00\x00\xff\x11\xb4X\x00\x00\x00\x00\xff\xff\xff\xff\x00D\x00C\x01<"
    b"\x0b\x14\x01\x01\x06\x00jmjV\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xb8\xb7\xf1m\xb53\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00c\x82Sc5\x01\x039\x02\x05\xdc2\x04\xc0\xa8\xd286"
    b"\x04\xc0\xa8\xd0\x017\x04\x01\x03\x1c\x06\x0c\x07connect\xff\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)

# iRobot-AE9EC12DD3B04885BCBFA36AFB01E1CC 50:14:79:03:85:2c 192.168.1.120
RAW_DHCP_RENEWAL = (
    b"\x00\x15\x5d\x8e\xed\x02\x50\x14\x79\x03\x85\x2c\x08\x00\x45\x00"
    b"\x01\x8e\x51\xd2\x40\x00\x40\x11\x63\xa1\xc0\xa8\x01\x78\xc0\xa8"
    b"\x01\x23\x00\x44\x00\x43\x01\x7a\x12\x09\x01\x01\x06\x00\xd4\xea"
    b"\xb2\xfd\xff\xff\x00\x00\xc0\xa8\x01\x78\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x50\x14\x79\x03\x85\x2c\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x63\x82\x53\x63\x35\x01\x03\x39\x02\x05"
    b"\xdc\x3c\x45\x64\x68\x63\x70\x63\x64\x2d\x35\x2e\x32\x2e\x31\x30"
    b"\x3a\x4c\x69\x6e\x75\x78\x2d\x33\x2e\x31\x38\x2e\x37\x31\x3a\x61"
    b"\x72\x6d\x76\x37\x6c\x3a\x51\x75\x61\x6c\x63\x6f\x6d\x6d\x20\x54"
    b"\x65\x63\x68\x6e\x6f\x6c\x6f\x67\x69\x65\x73\x2c\x20\x49\x6e\x63"
    b"\x20\x41\x50\x51\x38\x30\x30\x39\x0c\x27\x69\x52\x6f\x62\x6f\x74"
    b"\x2d\x41\x45\x39\x45\x43\x31\x32\x44\x44\x33\x42\x30\x34\x38\x38"
    b"\x35\x42\x43\x42\x46\x41\x33\x36\x41\x46\x42\x30\x31\x45\x31\x43"
    b"\x43\x37\x08\x01\x21\x03\x06\x1c\x33\x3a\x3b\xff"
)

# <no hostname> 60:6b:bd:59:e4:b4 192.168.107.151
RAW_DHCP_REQUEST_WITHOUT_HOSTNAME = (
    b"\xff\xff\xff\xff\xff\xff\x60\x6b\xbd\x59\xe4\xb4\x08\x00\x45\x00"
    b"\x02\x40\x00\x00\x00\x00\x40\x11\x78\xae\x00\x00\x00\x00\xff\xff"
    b"\xff\xff\x00\x44\x00\x43\x02\x2c\x02\x04\x01\x01\x06\x00\xff\x92"
    b"\x7e\x31\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x60\x6b\xbd\x59\xe4\xb4\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x63\x82\x53\x63\x35\x01\x03\x3d\x07\x01"
    b"\x60\x6b\xbd\x59\xe4\xb4\x3c\x25\x75\x64\x68\x63\x70\x20\x31\x2e"
    b"\x31\x34\x2e\x33\x2d\x56\x44\x20\x4c\x69\x6e\x75\x78\x20\x56\x44"
    b"\x4c\x69\x6e\x75\x78\x2e\x31\x2e\x32\x2e\x31\x2e\x78\x32\x04\xc0"
    b"\xa8\x6b\x97\x36\x04\xc0\xa8\x6b\x01\x37\x07\x01\x03\x06\x0c\x0f"
    b"\x1c\x2a\xff\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)


async def _async_get_handle_dhcp_packet(
    hass: HomeAssistant,
    integration_matchers: dhcp.DhcpMatchers,
    address_data: dict | None = None,
) -> Callable[[Any], Awaitable[None]]:
    """Make a handler for a dhcp packet."""
    if address_data is None:
        address_data = {}
    dhcp_watcher = dhcp.DHCPWatcher(
        hass,
        DHCPData(integration_matchers, set(), address_data),
    )

    with patch("aiodhcpwatcher.async_start"):
        await dhcp_watcher.async_start()

    def _async_handle_dhcp_request(request: aiodhcpwatcher.DHCPRequest) -> None:
        dhcp_watcher._async_process_dhcp_request(request)

    handler = aiodhcpwatcher.make_packet_handler(_async_handle_dhcp_request)

    async def _async_handle_dhcp_packet(packet):
        handler(packet)

    return cast("Callable[[Any], Awaitable[None]]", _async_handle_dhcp_packet)


async def test_dhcp_start_using_multiple_interfaces(
    hass: HomeAssistant,
) -> None:
    """Test start using multiple interfaces."""

    def _generate_mock_adapters():
        return [
            {
                "index": 1,
                "auto": False,
                "default": False,
                "enabled": True,
                "ipv4": [{"address": "192.168.0.1", "network_prefix": 24}],
                "ipv6": [],
                "name": "eth0",
            },
            {
                "index": 2,
                "auto": True,
                "default": True,
                "enabled": True,
                "ipv4": [{"address": "192.168.1.1", "network_prefix": 24}],
                "ipv6": [],
                "name": "eth1",
            },
        ]

    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "connect", "macaddress": "B8B7F1*"}]
    )
    dhcp_watcher = dhcp.DHCPWatcher(
        hass,
        DHCPData(integration_matchers, set(), {}),
    )

    with (
        patch("aiodhcpwatcher.async_start") as mock_start,
        patch(
            "homeassistant.components.dhcp.network.async_get_adapters",
            return_value=_generate_mock_adapters(),
        ),
    ):
        await dhcp_watcher.async_start()

    mock_start.assert_called_with(dhcp_watcher._async_process_dhcp_request, [1, 2])


async def test_dhcp_match_hostname_and_macaddress(hass: HomeAssistant) -> None:
    """Test matching based on hostname and macaddress."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "connect", "macaddress": "B8B7F1*"}]
    )
    packet = Ether(RAW_DHCP_REQUEST)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)
        # Ensure no change is ignored
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )


async def test_dhcp_renewal_match_hostname_and_macaddress(hass: HomeAssistant) -> None:
    """Test renewal matching based on hostname and macaddress."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "irobot-*", "macaddress": "501479*"}]
    )

    packet = Ether(RAW_DHCP_RENEWAL)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)
        # Ensure no change is ignored
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="50147903852c", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.1.120",
        hostname="irobot-ae9ec12dd3b04885bcbfa36afb01e1cc",
        macaddress="50147903852c",
    )


async def test_registered_devices(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Test discovery flows are created for registered devices."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [
            {"domain": "not-matching", "registered_devices": True},
            {"domain": "mock-domain", "registered_devices": True},
        ]
    )

    packet = Ether(RAW_DHCP_RENEWAL)

    config_entry = MockConfigEntry(domain="mock-domain", data={})
    config_entry.add_to_hass(hass)
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "50147903852c")},
        name="name",
    )
    # Not enabled should not get flows
    config_entry2 = MockConfigEntry(domain="mock-domain-2", data={})
    config_entry2.add_to_hass(hass)
    device_registry.async_get_or_create(
        config_entry_id=config_entry2.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "50147903852c")},
        name="name",
    )

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)
        # Ensure no change is ignored
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="50147903852c", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.1.120",
        hostname="irobot-ae9ec12dd3b04885bcbfa36afb01e1cc",
        macaddress="50147903852c",
    )


async def test_dhcp_match_hostname(hass: HomeAssistant) -> None:
    """Test matching based on hostname only."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "connect"}]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )


async def test_dhcp_match_macaddress(hass: HomeAssistant) -> None:
    """Test matching based on macaddress only."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "macaddress": "B8B7F1*"}]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )


async def test_dhcp_multiple_match_only_one_flow(hass: HomeAssistant) -> None:
    """Test matching the domain multiple times only generates one flow."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [
            {"domain": "mock-domain", "macaddress": "B8B7F1*"},
            {"domain": "mock-domain", "hostname": "connect"},
        ]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )


async def test_dhcp_match_macaddress_without_hostname(hass: HomeAssistant) -> None:
    """Test matching based on macaddress only."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "macaddress": "606BBD*"}]
    )

    packet = Ether(RAW_DHCP_REQUEST_WITHOUT_HOSTNAME)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="606bbd59e4b4", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.107.151",
        hostname="",
        macaddress="606bbd59e4b4",
    )


async def test_dhcp_nomatch(hass: HomeAssistant) -> None:
    """Test not matching based on macaddress only."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "macaddress": "ABC123*"}]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 0


async def test_dhcp_nomatch_hostname(hass: HomeAssistant) -> None:
    """Test not matching based on hostname only."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "nomatch*"}]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 0


async def test_dhcp_nomatch_non_dhcp_packet(hass: HomeAssistant) -> None:
    """Test matching does not throw on a non-dhcp packet."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "nomatch*"}]
    )

    packet = Ether(b"")

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 0


async def test_dhcp_nomatch_non_dhcp_request_packet(hass: HomeAssistant) -> None:
    """Test nothing happens with the wrong message-type."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "nomatch*"}]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    packet[DHCP].options = [
        ("message-type", 4),
        ("max_dhcp_size", 1500),
        ("requested_addr", "192.168.210.56"),
        ("server_id", "192.168.208.1"),
        ("param_req_list", [1, 3, 28, 6]),
        ("hostname", b"connect"),
    ]

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 0


async def test_dhcp_invalid_hostname(hass: HomeAssistant) -> None:
    """Test we ignore invalid hostnames."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "nomatch*"}]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    packet[DHCP].options = [
        ("message-type", 3),
        ("max_dhcp_size", 1500),
        ("requested_addr", "192.168.210.56"),
        ("server_id", "192.168.208.1"),
        ("param_req_list", [1, 3, 28, 6]),
        ("hostname", "connect"),
    ]

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 0


async def test_dhcp_missing_hostname(hass: HomeAssistant) -> None:
    """Test we ignore missing hostnames."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "nomatch*"}]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    packet[DHCP].options = [
        ("message-type", 3),
        ("max_dhcp_size", 1500),
        ("requested_addr", "192.168.210.56"),
        ("server_id", "192.168.208.1"),
        ("param_req_list", [1, 3, 28, 6]),
        ("hostname", None),
    ]

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 0


async def test_dhcp_invalid_option(hass: HomeAssistant) -> None:
    """Test we ignore invalid hostname option."""
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "nomatch*"}]
    )

    packet = Ether(RAW_DHCP_REQUEST)

    packet[DHCP].options = [
        ("message-type", 3),
        ("max_dhcp_size", 1500),
        ("requested_addr", "192.168.208.55"),
        ("server_id", "192.168.208.1"),
        ("param_req_list", [1, 3, 28, 6]),
        "hostname",
    ]

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers
    )
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)

    assert len(mock_init.mock_calls) == 0


async def test_setup_and_stop(hass: HomeAssistant) -> None:
    """Test we can setup and stop."""

    assert await async_setup_component(
        hass,
        DOMAIN,
        {},
    )
    await hass.async_block_till_done()

    with (
        patch.object(
            interfaces,
            "resolve_iface",
        ) as resolve_iface_call,
        patch("scapy.arch.common.compile_filter"),
        patch("homeassistant.components.dhcp.DiscoverHosts.async_discover"),
    ):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    resolve_iface_call.assert_called_once()


async def test_setup_fails_as_root(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test we handle sniff setup failing as root."""

    assert await async_setup_component(
        hass,
        DOMAIN,
        {},
    )
    await hass.async_block_till_done()

    wait_event = threading.Event()

    with (
        patch("os.geteuid", return_value=0),
        patch.object(
            interfaces,
            "resolve_iface",
            side_effect=Scapy_Exception,
        ),
        patch("homeassistant.components.dhcp.DiscoverHosts.async_discover"),
    ):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    wait_event.set()
    assert "Cannot watch for dhcp packets" in caplog.text


async def test_setup_fails_non_root(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test we handle sniff setup failing as non-root."""

    assert await async_setup_component(
        hass,
        DOMAIN,
        {},
    )
    await hass.async_block_till_done()

    with (
        patch("os.geteuid", return_value=10),
        patch("scapy.arch.common.compile_filter"),
        patch.object(
            interfaces,
            "resolve_iface",
            side_effect=Scapy_Exception,
        ),
        patch("homeassistant.components.dhcp.DiscoverHosts.async_discover"),
    ):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()

    assert "Cannot watch for dhcp packets without root or CAP_NET_RAW" in caplog.text


async def test_setup_fails_with_broken_libpcap(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test we abort if libpcap is missing or broken."""

    assert await async_setup_component(
        hass,
        DOMAIN,
        {},
    )
    await hass.async_block_till_done()

    with (
        patch(
            "scapy.arch.common.compile_filter",
            side_effect=ImportError,
        ) as compile_filter,
        patch.object(
            interfaces,
            "resolve_iface",
        ) as resolve_iface_call,
        patch("homeassistant.components.dhcp.DiscoverHosts.async_discover"),
    ):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()

    assert compile_filter.called
    assert not resolve_iface_call.called
    assert (
        "Cannot watch for dhcp packets without a functional packet filter"
        in caplog.text
    )


def _make_device_tracker_watcher(
    hass: HomeAssistant, matchers: list[dhcp.DHCPMatcher]
) -> dhcp.DeviceTrackerWatcher:
    return dhcp.DeviceTrackerWatcher(
        hass,
        DHCPData(
            dhcp.async_index_integration_matchers(matchers),
            set(),
            {},
        ),
    )


def _make_device_tracker_registered_watcher(
    hass: HomeAssistant, matchers: list[dhcp.DHCPMatcher]
) -> dhcp.DeviceTrackerRegisteredWatcher:
    return dhcp.DeviceTrackerRegisteredWatcher(
        hass,
        DHCPData(
            dhcp.async_index_integration_matchers(matchers),
            set(),
            {},
        ),
    )


def _make_network_watcher(
    hass: HomeAssistant, matchers: list[dhcp.DHCPMatcher]
) -> dhcp.NetworkWatcher:
    return dhcp.NetworkWatcher(
        hass,
        DHCPData(
            dhcp.async_index_integration_matchers(matchers),
            set(),
            {},
        ),
    )


async def test_device_tracker_hostname_and_macaddress_exists_before_start(
    hass: HomeAssistant,
) -> None:
    """Test matching based on hostname and macaddress before start."""
    hass.states.async_set(
        "device_tracker.august_connect",
        STATE_HOME,
        {
            ATTR_HOST_NAME: "Connect",
            ATTR_IP: "192.168.210.56",
            ATTR_SOURCE_TYPE: SourceType.ROUTER,
            ATTR_MAC: "B8:B7:F1:6D:B5:33",
        },
    )

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "connect",
                    "macaddress": "B8B7F1*",
                }
            ],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )


async def test_device_tracker_registered(hass: HomeAssistant) -> None:
    """Test matching based on hostname and macaddress when registered."""
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_registered_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "connect",
                    "macaddress": "B8B7F1*",
                }
            ],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        async_dispatcher_send(
            hass,
            CONNECTED_DEVICE_REGISTERED,
            {"ip": "192.168.210.56", "mac": "b8b7f16db533", "host_name": "connect"},
        )
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )
    device_tracker_watcher.async_stop()
    await hass.async_block_till_done()


async def test_device_tracker_registered_hostname_none(hass: HomeAssistant) -> None:
    """Test handle None hostname."""
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "connect",
                    "macaddress": "B8B7F1*",
                }
            ],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        async_dispatcher_send(
            hass,
            CONNECTED_DEVICE_REGISTERED,
            {"ip": "192.168.210.56", "mac": "b8b7f16db533", "host_name": None},
        )
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 0
    device_tracker_watcher.async_stop()
    await hass.async_block_till_done()


async def test_device_tracker_hostname_and_macaddress_after_start(
    hass: HomeAssistant,
) -> None:
    """Test matching based on hostname and macaddress after start."""

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "connect",
                    "macaddress": "B8B7F1*",
                }
            ],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        hass.states.async_set(
            "device_tracker.august_connect",
            STATE_HOME,
            {
                ATTR_HOST_NAME: "Connect",
                ATTR_IP: "192.168.210.56",
                ATTR_SOURCE_TYPE: SourceType.ROUTER,
                ATTR_MAC: "B8:B7:F1:6D:B5:33",
            },
        )
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )


async def test_device_tracker_hostname_and_macaddress_after_start_not_home(
    hass: HomeAssistant,
) -> None:
    """Test matching based on hostname and macaddress after start but not home."""

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "connect",
                    "macaddress": "B8B7F1*",
                }
            ],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        hass.states.async_set(
            "device_tracker.august_connect",
            STATE_NOT_HOME,
            {
                ATTR_HOST_NAME: "connect",
                ATTR_IP: "192.168.210.56",
                ATTR_SOURCE_TYPE: SourceType.ROUTER,
                ATTR_MAC: "B8:B7:F1:6D:B5:33",
            },
        )
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 0


async def test_device_tracker_hostname_and_macaddress_after_start_not_router(
    hass: HomeAssistant,
) -> None:
    """Test matching based on hostname and macaddress after start but not router."""

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_watcher(
            hass,
            [{"domain": "mock-domain", "hostname": "connect", "macaddress": "B8B7F1*"}],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        hass.states.async_set(
            "device_tracker.august_connect",
            STATE_HOME,
            {
                ATTR_HOST_NAME: "connect",
                ATTR_IP: "192.168.210.56",
                ATTR_SOURCE_TYPE: "something_else",
                ATTR_MAC: "B8:B7:F1:6D:B5:33",
            },
        )
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 0


async def test_device_tracker_hostname_and_macaddress_after_start_hostname_missing(
    hass: HomeAssistant,
) -> None:
    """Test matching based on hostname and macaddress after start but missing hostname."""

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_watcher(
            hass,
            [{"domain": "mock-domain", "hostname": "connect", "macaddress": "B8B7F1*"}],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        hass.states.async_set(
            "device_tracker.august_connect",
            STATE_HOME,
            {
                ATTR_IP: "192.168.210.56",
                ATTR_SOURCE_TYPE: SourceType.ROUTER,
                ATTR_MAC: "B8:B7:F1:6D:B5:33",
            },
        )
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 0


async def test_device_tracker_invalid_ip_address(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test an invalid ip address."""

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_watcher(
            hass,
            [{"domain": "mock-domain", "hostname": "connect", "macaddress": "B8B7F1*"}],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        hass.states.async_set(
            "device_tracker.august_connect",
            STATE_HOME,
            {
                ATTR_IP: "invalid",
                ATTR_SOURCE_TYPE: SourceType.ROUTER,
                ATTR_MAC: "B8:B7:F1:6D:B5:33",
            },
        )
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert "Ignoring invalid IP Address: invalid" in caplog.text
    assert len(mock_init.mock_calls) == 0


async def test_device_tracker_ignore_self_assigned_ips_before_start(
    hass: HomeAssistant,
) -> None:
    """Test matching ignores self assigned ip address."""
    hass.states.async_set(
        "device_tracker.august_connect",
        STATE_HOME,
        {
            ATTR_HOST_NAME: "connect",
            ATTR_IP: "169.254.210.56",
            ATTR_SOURCE_TYPE: SourceType.ROUTER,
            ATTR_MAC: "B8:B7:F1:6D:B5:33",
        },
    )

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        device_tracker_watcher = _make_device_tracker_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "connect",
                    "macaddress": "B8B7F1*",
                }
            ],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 0


async def test_aiodiscover_finds_new_hosts(hass: HomeAssistant) -> None:
    """Test aiodiscover finds new host."""
    with (
        patch.object(hass.config_entries.flow, "async_init") as mock_init,
        patch(
            "homeassistant.components.dhcp.DiscoverHosts.async_discover",
            return_value=[
                {
                    dhcp.DISCOVERY_IP_ADDRESS: "192.168.210.56",
                    dhcp.DISCOVERY_HOSTNAME: "connect",
                    dhcp.DISCOVERY_MAC_ADDRESS: "b8b7f16db533",
                }
            ],
        ),
    ):
        device_tracker_watcher = _make_network_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "connect",
                    "macaddress": "B8B7F1*",
                }
            ],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )


async def test_aiodiscover_does_not_call_again_on_shorter_hostname(
    hass: HomeAssistant,
) -> None:
    """Verify longer hostnames generate a new flow but shorter ones do not.

    Some routers will truncate hostnames so we want to accept
    additional discovery where the hostname is longer and then
    reject shorter ones.
    """
    with (
        patch.object(hass.config_entries.flow, "async_init") as mock_init,
        patch(
            "homeassistant.components.dhcp.DiscoverHosts.async_discover",
            return_value=[
                {
                    dhcp.DISCOVERY_IP_ADDRESS: "192.168.210.56",
                    dhcp.DISCOVERY_HOSTNAME: "irobot-abc",
                    dhcp.DISCOVERY_MAC_ADDRESS: "b8b7f16db533",
                },
                {
                    dhcp.DISCOVERY_IP_ADDRESS: "192.168.210.56",
                    dhcp.DISCOVERY_HOSTNAME: "irobot-abcdef",
                    dhcp.DISCOVERY_MAC_ADDRESS: "b8b7f16db533",
                },
                {
                    dhcp.DISCOVERY_IP_ADDRESS: "192.168.210.56",
                    dhcp.DISCOVERY_HOSTNAME: "irobot-abc",
                    dhcp.DISCOVERY_MAC_ADDRESS: "b8b7f16db533",
                },
            ],
        ),
    ):
        device_tracker_watcher = _make_network_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "irobot-*",
                    "macaddress": "B8B7F1*",
                }
            ],
        )
        device_tracker_watcher.async_start()
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 2
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="irobot-abc",
        macaddress="b8b7f16db533",
    )
    assert mock_init.mock_calls[1][1][0] == "mock-domain"
    assert mock_init.mock_calls[1][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[1][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="irobot-abcdef",
        macaddress="b8b7f16db533",
    )


async def test_aiodiscover_finds_new_hosts_after_interval(hass: HomeAssistant) -> None:
    """Test aiodiscover finds new host after interval."""
    with (
        patch.object(hass.config_entries.flow, "async_init") as mock_init,
        patch(
            "homeassistant.components.dhcp.DiscoverHosts.async_discover",
            return_value=[],
        ),
    ):
        device_tracker_watcher = _make_network_watcher(
            hass,
            [
                {
                    "domain": "mock-domain",
                    "hostname": "connect",
                    "macaddress": "B8B7F1*",
                }
            ],
        )

        device_tracker_watcher.async_start()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 0

    with (
        patch.object(hass.config_entries.flow, "async_init") as mock_init,
        patch(
            "homeassistant.components.dhcp.DiscoverHosts.async_discover",
            return_value=[
                {
                    dhcp.DISCOVERY_IP_ADDRESS: "192.168.210.56",
                    dhcp.DISCOVERY_HOSTNAME: "connect",
                    dhcp.DISCOVERY_MAC_ADDRESS: "b8b7f16db533",
                }
            ],
        ),
    ):
        async_fire_time_changed(hass, dt_util.utcnow() + datetime.timedelta(minutes=65))
        await hass.async_block_till_done()
        device_tracker_watcher.async_stop()
        await hass.async_block_till_done()

    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )


@pytest.mark.parametrize(
    (
        "entry_domain",
        "entry_discovery_keys",
    ),
    [
        # Matching discovery key
        (
            "mock-domain",
            {"dhcp": (DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),)},
        ),
        # Matching discovery key
        (
            "mock-domain",
            {
                "dhcp": (DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),),
                "other": (DiscoveryKey(domain="other", key="blah", version=1),),
            },
        ),
        # Matching discovery key, other domain
        # Note: Rediscovery is not currently restricted to the domain of the removed
        # entry. Such a check can be added if needed.
        (
            "comp",
            {"dhcp": (DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),)},
        ),
    ],
)
@pytest.mark.parametrize(
    "entry_source",
    [
        config_entries.SOURCE_DHCP,
        config_entries.SOURCE_IGNORE,
        config_entries.SOURCE_USER,
    ],
)
async def test_dhcp_rediscover(
    hass: HomeAssistant,
    entry_domain: str,
    entry_discovery_keys: dict[str, tuple[DiscoveryKey, ...]],
    entry_source: str,
) -> None:
    """Test we reinitiate flows when an ignored config entry is removed."""

    entry = MockConfigEntry(
        domain=entry_domain,
        discovery_keys=entry_discovery_keys,
        unique_id="mock-unique-id",
        state=config_entries.ConfigEntryState.LOADED,
        source=entry_source,
    )
    entry.add_to_hass(hass)

    address_data = {}
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "connect", "macaddress": "B8B7F1*"}]
    )
    packet = Ether(RAW_DHCP_REQUEST)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers, address_data
    )
    rediscovery_watcher = dhcp.RediscoveryWatcher(
        hass, DHCPData(integration_matchers, set(), address_data)
    )
    rediscovery_watcher.async_start()
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)
        # Ensure no change is ignored
        await async_handle_dhcp_packet(packet)

    # Assert the cached MAC address is hexstring without :
    assert address_data == {
        "b8b7f16db533": {"hostname": "connect", "ip": "192.168.210.56"}
    }

    expected_context = {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == expected_context
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        assert len(mock_init.mock_calls) == 1
        assert mock_init.mock_calls[0][1][0] == "mock-domain"
        assert mock_init.mock_calls[0][2]["context"] == expected_context


@pytest.mark.usefixtures("mock_async_zeroconf")
@pytest.mark.parametrize(
    (
        "entry_domain",
        "entry_discovery_keys",
        "entry_source",
        "entry_unique_id",
    ),
    [
        # Discovery key from other domain
        (
            "mock-domain",
            {
                "bluetooth": (
                    DiscoveryKey(domain="bluetooth", key="b8b7f16db533", version=1),
                )
            },
            config_entries.SOURCE_IGNORE,
            "mock-unique-id",
        ),
        # Discovery key from the future
        (
            "mock-domain",
            {"dhcp": (DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=2),)},
            config_entries.SOURCE_IGNORE,
            "mock-unique-id",
        ),
    ],
)
async def test_dhcp_rediscover_no_match(
    hass: HomeAssistant,
    entry_domain: str,
    entry_discovery_keys: dict[str, tuple[DiscoveryKey, ...]],
    entry_source: str,
    entry_unique_id: str,
) -> None:
    """Test we don't reinitiate flows when a non matching config entry is removed."""

    mock_integration(hass, MockModule(entry_domain))

    entry = MockConfigEntry(
        domain=entry_domain,
        discovery_keys=entry_discovery_keys,
        unique_id=entry_unique_id,
        state=config_entries.ConfigEntryState.LOADED,
        source=entry_source,
    )
    entry.add_to_hass(hass)

    address_data = {}
    integration_matchers = dhcp.async_index_integration_matchers(
        [{"domain": "mock-domain", "hostname": "connect", "macaddress": "B8B7F1*"}]
    )
    packet = Ether(RAW_DHCP_REQUEST)

    async_handle_dhcp_packet = await _async_get_handle_dhcp_packet(
        hass, integration_matchers, address_data
    )
    rediscovery_watcher = dhcp.RediscoveryWatcher(
        hass, DHCPData(integration_matchers, set(), address_data)
    )
    rediscovery_watcher.async_start()
    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await async_handle_dhcp_packet(packet)
        # Ensure no change is ignored
        await async_handle_dhcp_packet(packet)

    expected_context = {
        "discovery_key": DiscoveryKey(domain="dhcp", key="b8b7f16db533", version=1),
        "source": config_entries.SOURCE_DHCP,
    }
    assert len(mock_init.mock_calls) == 1
    assert mock_init.mock_calls[0][1][0] == "mock-domain"
    assert mock_init.mock_calls[0][2]["context"] == expected_context
    assert mock_init.mock_calls[0][2]["data"] == dhcp.DhcpServiceInfo(
        ip="192.168.210.56",
        hostname="connect",
        macaddress="b8b7f16db533",
    )

    with patch.object(hass.config_entries.flow, "async_init") as mock_init:
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        assert len(mock_init.mock_calls) == 0


@pytest.mark.parametrize(
    ("constant_name", "replacement_name", "replacement"),
    [
        (
            "DhcpServiceInfo",
            "homeassistant.helpers.service_info.dhcp.DhcpServiceInfo",
            DhcpServiceInfo,
        ),
    ],
)
def test_deprecated_constants(
    caplog: pytest.LogCaptureFixture,
    constant_name: str,
    replacement_name: str,
    replacement: Any,
) -> None:
    """Test deprecated automation constants."""
    import_and_test_deprecated_constant(
        caplog,
        dhcp,
        constant_name,
        replacement_name,
        replacement,
        "2026.2",
    )
