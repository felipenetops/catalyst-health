"""Testes da consulta de saúde de clientes, sem falar com o Catalyst Center."""

from __future__ import annotations

import csv
import tempfile
import time
import unittest
from pathlib import Path

from catalyst_center.client import CatalystCenterClient
from catalyst_center.consultar import (
    _parse_client_type,
    fetch_client_health,
    write_csv,
    write_reports,
)
from catalyst_center.dashboard import render_dashboard
from catalyst_center.health import (
    CLIENT_COLUMNS,
    CLIENT_LOCATION_FIELDS,
    filter_by_location,
    filter_by_ssid,
    format_duration,
    normalize_client,
    normalize_site,
    select_columns,
)


class NormalizeTests(unittest.TestCase):
    def test_flat_client(self) -> None:
        row = normalize_client(
            {
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "type": "Wireless",
                "connectionStatus": "connected",
                "overallScore": 10,
                "siteHierarchy": "Global/Brasil/Loja 1",
                "ssid": "CORP",
                "rssi": -52,
                "isPrivateMacAddress": False,
                "lastUpdatedTime": 1_699_444_237_000,
            }
        )
        self.assertEqual(row["macAddress"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(row["connectionStatus"], "Conectado")
        self.assertEqual(row["overallScore"], 10)
        self.assertEqual(row["rssi"], -52)
        self.assertEqual(row["isPrivateMacAddress"], "Não")
        self.assertIn("/", row["lastUpdatedTime"])

    def test_nested_client_and_health_list(self) -> None:
        row = normalize_client(
            {
                "id": "AA:BB:CC:DD:EE:01",
                "healthScore": [
                    {"healthType": "OVERALL", "score": 4},
                    {"healthType": "ONBOARDING", "score": 1},
                ],
                "connection": {"ssid": "VISITANTES", "sessionDuration": 125000},
                "connectedNetworkDevice": {"connectedNetworkDeviceName": "AP-01"},
                "ipv6Addresses": ["2001:db8::1", "2001:db8::2"],
                "overallScore": -1,
            }
        )
        self.assertEqual(row["macAddress"], "AA:BB:CC:DD:EE:01")
        self.assertEqual(row["overallScore"], 4)
        self.assertEqual(row["onboardingScore"], 1)
        self.assertEqual(row["ssid"], "VISITANTES")
        self.assertEqual(row["connectedNetworkDeviceName"], "AP-01")
        self.assertEqual(row["ipv6Addresses"], "2001:db8::1, 2001:db8::2")
        self.assertEqual(row["sessionDuration"], "2min 05s")

    def test_site_drops_missing_score(self) -> None:
        row = normalize_site(
            {
                "siteName": "Loja 1",
                "parentSiteName": "Brasil",
                "siteType": "building",
                "clientHealthWired": -1,
                "clientHealthWireless": 8,
                "numberOfClients": 0,
                "healthyClientsPercentage": 80,
            }
        )
        self.assertNotIn("clientHealthWired", row)
        self.assertEqual(row["clientHealthWireless"], 8)
        self.assertEqual(row["numberOfClients"], 0)
        self.assertEqual(row["healthyClientsPercentage"], 80)

    def test_duration_and_location_filter(self) -> None:
        self.assertEqual(format_duration(3_600_000), "1h 00min")
        self.assertIsNone(format_duration(-1))
        rows = [
            {"siteHierarchy": "Global/Brasil/Loja 1"},
            {"siteHierarchy": "Global/Argentina/Loja 2"},
        ]
        selected = filter_by_location(rows, "brasil", CLIENT_LOCATION_FIELDS)
        self.assertEqual(selected, [rows[0]])
        self.assertEqual(filter_by_location(rows, "  ", CLIENT_LOCATION_FIELDS), rows)
        self.assertEqual(
            filter_by_location([{"macAddress": "AA"}], "loja", CLIENT_LOCATION_FIELDS),
            [{"macAddress": "AA"}],
        )

    def test_empty_columns_are_omitted(self) -> None:
        columns = select_columns([{"macAddress": "AA", "overallScore": 0}], CLIENT_COLUMNS)
        keys = [column["key"] for column in columns]
        self.assertEqual(keys, ["macAddress", "overallScore"])


class SsidTests(unittest.TestCase):
    def test_wireless_keeps_only_the_requested_ssid(self) -> None:
        rows = [
            {"type": "Wireless", "ssid": "Loja-Corp", "macAddress": "AA"},
            {"type": "Wireless", "ssid": "Visitantes", "macAddress": "BB"},
            {"type": "Wired", "macAddress": "CC"},
            {"type": "Wireless", "ssid": "", "macAddress": "DD"},
        ]
        selected = filter_by_ssid(rows, ("loja-corp",))
        self.assertEqual([row["macAddress"] for row in selected], ["AA"])

    def test_all_types_keep_wired_and_matching_wireless(self) -> None:
        rows = [
            {"type": "Wireless", "ssid": "Corp", "macAddress": "AA"},
            {"type": "Wireless", "ssid": "Guest", "macAddress": "BB"},
            {"type": "Wired", "macAddress": "CC"},
        ]
        selected = filter_by_ssid(rows, ("Corp", "Guest"), keep_wired=True)
        self.assertEqual([row["macAddress"] for row in selected], ["AA", "BB", "CC"])
        wildcard = filter_by_ssid(rows, ("*corp*",), keep_wired=True)
        self.assertEqual([row["macAddress"] for row in wildcard], ["AA", "CC"])

    def test_wireless_query_sends_ssid_to_the_api(self) -> None:
        class Stub:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def list_assurance_clients(self, **kwargs):
                self.calls.append(kwargs)
                return [
                    {"macAddress": "AA", "type": "Wireless", "ssid": "Loja-Corp", "connectedScore": 10},
                    {"macAddress": "BB", "type": "Wireless", "ssid": "Visitantes", "connectedScore": 10},
                ]

        stub = Stub()
        _mode, rows, _columns = fetch_client_health(
            stub,  # type: ignore[arg-type]
            location="Serra",
            client_type="Wireless",
            ssids=("Loja-Corp",),
        )
        self.assertEqual(stub.calls[0]["ssid"], "Loja-Corp")
        self.assertEqual(stub.calls[0]["view"], "Wireless")
        self.assertEqual(stub.calls[0]["client_type"], "Wireless")
        self.assertEqual([row["macAddress"] for row in rows], ["AA"])

    def test_retries_without_ssid_and_uses_the_full_site_path(self) -> None:
        class Stub:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def list_sites(self):
                return [
                    {
                        "name": "Serra",
                        "groupNameHierarchy": "Global/ES/Serra",
                        "id": "building",
                    },
                    {
                        "name": "Andar 1",
                        "groupNameHierarchy": "Global/ES/Serra/Andar 1",
                        "id": "floor",
                    },
                ]

            def list_assurance_clients(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs.get("ssid"):
                    return []
                if kwargs.get("site_hierarchy") != "Global/ES/Serra":
                    return []
                return [
                    {
                        "macAddress": "AA",
                        "type": "WIRELESS",
                        "ssid": "RFBOTI1",
                        "siteHierarchy": "Global/ES/Serra/Andar 1",
                        "connectedScore": 4,
                    },
                    {
                        "macAddress": "BB",
                        "type": "Wireless",
                        "ssid": "Visitantes",
                        "siteHierarchy": "Global/ES/Serra/Andar 1",
                        "connectedScore": 10,
                    },
                ]

        stub = Stub()
        notes: list[str] = []
        _mode, rows, _columns = fetch_client_health(
            stub,  # type: ignore[arg-type]
            location="Serra",
            client_type="Wireless",
            ssids=("rfboti1",),
            notes=notes,
        )
        self.assertEqual(stub.calls[0]["site_hierarchy"], "Global/ES/Serra")
        self.assertEqual([row["macAddress"] for row in rows], ["AA"])
        self.assertTrue(any("filtro foi aplicado" in note.casefold() for note in notes))


class ReportTests(unittest.TestCase):
    def test_client_type_prompt(self) -> None:
        self.assertIsNone(_parse_client_type(" "))
        self.assertEqual(_parse_client_type("cabeado"), "Wired")
        self.assertEqual(_parse_client_type("sem fio"), "Wireless")
        with self.assertRaises(ValueError):
            _parse_client_type("ethernet")

    def test_csv_and_dashboard(self) -> None:
        rows = [
            {
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "type": "Wireless",
                "overallScore": 9,
                "siteHierarchy": "Global/Brasil/Loja 1",
            }
        ]
        columns = select_columns(rows, CLIENT_COLUMNS)
        with tempfile.TemporaryDirectory() as directory:
            csv_path, html_path = write_reports(
                mode="clients",
                rows=rows,
                columns=columns,
                host="https://dnac.example",
                location="Loja 1",
                client_type=None,
                output_dir=Path(directory),
                now=__import__("datetime").datetime(2026, 10, 1, 16, 5, 0),
            )
            self.assertTrue(csv_path.name.startswith("clientes-saude-"))
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                table = list(csv.reader(handle))
            self.assertEqual(table[0], ["MAC", "Tipo", "Saúde", "Localidade"])
            self.assertEqual(table[1][0], "AA:BB:CC:DD:EE:FF")
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Filtrar", html)
            self.assertIn("data-drag-key", html)
            self.assertIn("Loja 1", html)
            self.assertNotIn("<script>alert", html)
            extra = Path(directory) / "extra.csv"
            write_csv(extra, columns, rows)
            self.assertTrue(extra.read_text(encoding="utf-8-sig").startswith("MAC"))


class PaginationTests(unittest.TestCase):
    def test_stops_when_page_is_short(self) -> None:
        pages = {
            1: {"response": [{"id": "a"}, {"id": "b"}]},
            3: {"response": [{"id": "c"}]},
        }

        class Session:
            def __init__(self) -> None:
                self.offsets: list[int] = []

            def request(self, method, url, params=None, **kwargs):
                self.offsets.append(params["offset"])
                payload = pages[params["offset"]]

                class Response:
                    status_code = 200
                    ok = True
                    content = b"{}"

                    def json(self):
                        return payload

                return Response()

        session = Session()
        client = CatalystCenterClient(
            "https://dnac.example",
            "user",
            "secret",
            session=session,
        )
        client._token = "token"
        client._token_obtained_at = time.monotonic()
        seen: list[int] = []
        items = client.get_all("dna/data/api/v1/clients", page_size=2, on_page=seen.append)
        self.assertEqual(session.offsets, [1, 3])
        self.assertEqual([item["id"] for item in items], ["a", "b", "c"])
        self.assertEqual(seen, [2, 3])


class DashboardTests(unittest.TestCase):
    def test_payload_escapes_markup(self) -> None:
        html = render_dashboard(
            title="Saúde",
            subtitle="teste",
            columns=[{"key": "name", "label": "Nome", "kind": "text"}],
            rows=[{"name": "</script><script>alert(1)</script>"}],
        )
        self.assertNotIn("</script><script>", html)
        self.assertIn("\\u003c", html)


if __name__ == "__main__":
    unittest.main()
