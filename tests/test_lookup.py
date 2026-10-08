"""Consultas por dispositivo e por access point, sem falar com o Catalyst Center."""

from __future__ import annotations

import unittest
from datetime import datetime

from catalyst_center.client import APIError
from catalyst_center.lookup import (
    access_point_rows,
    classify_identifier,
    fetch_access_points,
    fetch_device_connection,
    is_access_point,
)


START = datetime(2026, 10, 8, 14, 0)
END = datetime(2026, 10, 8, 17, 0)


class IdentifierTests(unittest.TestCase):
    def test_classifies_mac_ip_and_name(self) -> None:
        self.assertEqual(classify_identifier("aa:bb:cc:dd:ee:ff"), ("mac", "AA:BB:CC:DD:EE:FF"))
        self.assertEqual(classify_identifier("10.1.1.10"), ("ip", "10.1.1.10"))
        self.assertEqual(classify_identifier("Loja-AP-01"), ("name", "Loja-AP-01"))


class DeviceTests(unittest.TestCase):
    def test_filters_the_requested_mac(self) -> None:
        class Stub:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def list_assurance_clients(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    {
                        "macAddress": "AA:BB:CC:DD:EE:FF",
                        "type": "Wireless",
                        "ssid": "Loja",
                        "connectedScore": 4,
                        "overallScore": 6,
                        "ipv4Address": "10.1.1.10",
                    },
                    {"macAddress": "11:22:33:44:55:66", "type": "Wireless", "connectedScore": 10},
                ]

        rows, columns = fetch_device_connection(
            Stub(),  # type: ignore[arg-type]
            query="aa:bb:cc:dd:ee:ff",
            period_start=START,
            period_end=END,
        )
        self.assertEqual([row["macAddress"] for row in rows], ["AA:BB:CC:DD:EE:FF"])
        self.assertEqual(columns[0]["key"] if columns else "", "macAddress")
        keys = [column["key"] for column in columns]
        self.assertIn("connectedScore", keys)
        self.assertIn("ssid", keys)


class AccessPointTests(unittest.TestCase):
    def test_one_row_per_radio(self) -> None:
        rows = access_point_rows(
            {
                "name": "Loja-AP-01",
                "managementIpAddress": "10.8.0.4",
                "macAddress": "AA:BB:CC:00:00:01",
                "deviceFamily": "Unified AP",
                "siteHierarchy": "Global/ES/Serra",
                "wirelessClientCount": 12,
                "metricsDetails": {"overallHealthScore": 8, "cpuUtilization": 21.5, "memoryUtilization": 40},
                "apDetails": {
                    "apOperationalState": "Registered",
                    "connectedWlcName": "WLC-1",
                    "radios": [
                        {"slot": "0", "radioBand": "5.0Ghz", "noise": -90, "clientCount": 8, "airQuality": 80},
                        {"slot": "1", "radioBand": "2.4Ghz", "noise": -80, "clientCount": 4, "airQuality": 55},
                    ],
                },
            }
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["radioBand"], "5.0Ghz")
        self.assertEqual(rows[0]["overallHealthScore"], 8)
        self.assertEqual(rows[1]["radioClientCount"], 4)
        self.assertEqual(rows[0]["cpuUtilization"], 21.5)

    def test_name_query_keeps_matching_aps(self) -> None:
        class Stub:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def list_network_devices(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    {"name": "Loja-AP-01", "deviceFamily": "Unified AP", "siteHierarchy": "Global/Serra"},
                    {"name": "Core-SW", "deviceFamily": "Switches and Hubs", "siteHierarchy": "Global/Serra"},
                ]

        rows, _columns = fetch_access_points(
            Stub(),  # type: ignore[arg-type]
            query="loja-ap",
            location="Serra",
            period_start=START,
            period_end=END,
        )
        self.assertEqual([row["name"] for row in rows], ["Loja-AP-01"])
        self.assertEqual(rows[0]["siteHierarchy"], "Global/Serra")

    def test_retries_without_radio_view(self) -> None:
        class Stub:
            def __init__(self) -> None:
                self.views: list = []

            def list_network_devices(self, **kwargs):
                self.views.append(kwargs.get("views"))
                if kwargs.get("views"):
                    raise APIError("view recusada", 400)
                return [{"name": "AP-1", "deviceFamily": "Unified AP", "macAddress": "AA:BB:CC:00:00:09"}]

        notes: list[str] = []
        rows, _columns = fetch_access_points(
            Stub(),  # type: ignore[arg-type]
            query="AA:BB:CC:00:00:09",
            location="",
            period_start=START,
            period_end=END,
            notes=notes,
        )
        self.assertEqual(len(rows), 1)
        self.assertTrue(notes)
        self.assertTrue(is_access_point({"deviceFamily": "Unified AP"}))
        self.assertFalse(is_access_point({"deviceFamily": "Appliance"}))
