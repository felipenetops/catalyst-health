"""Reincidentes com saúde de conexão baixa, sem consultar o Catalyst Center."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from catalyst_center.consultar import fetch_client_health, write_reports
from catalyst_center.recurrence import (
    aggregate_recurring,
    build_slices,
    describe_slices,
    is_low_connection,
    parse_minimum_occurrences,
    parse_user_datetime,
    period_from_choice,
)


NOW = datetime(2026, 10, 1, 16, 0)


def _observation(
    mac: str,
    score: int,
    location: str,
    start: datetime,
    *,
    name: str = "caixa",
    rssi: int | None = -70,
) -> dict:
    end = start + timedelta(hours=6)
    return {
        "macAddress": mac,
        "name": name,
        "type": "Wireless",
        "connectedScore": score,
        "overallScore": score,
        "siteHierarchy": location,
        "ssid": "CORP",
        "rssi": rssi,
        "sliceLabel": f"{start:%d/%m %H:%M}",
        "sliceStart": start,
        "sliceEnd": end,
    }


class RuleTests(unittest.TestCase):
    def test_low_connection_band(self) -> None:
        self.assertTrue(is_low_connection(1))
        self.assertTrue(is_low_connection(2))
        self.assertTrue(is_low_connection(4))
        self.assertTrue(is_low_connection(6))
        self.assertTrue(is_low_connection(7))
        self.assertTrue(is_low_connection(8, low=1, high=10))
        self.assertFalse(is_low_connection(8))
        self.assertFalse(is_low_connection(2, low=4, high=7))
        self.assertFalse(is_low_connection(0))
        self.assertFalse(is_low_connection(9))
        self.assertFalse(is_low_connection(10))
        self.assertFalse(is_low_connection(None))

    def test_period_choices(self) -> None:
        start, end = period_from_choice("1", NOW)
        self.assertEqual(end, NOW)
        self.assertEqual(start, NOW - timedelta(hours=1))
        start, end = period_from_choice("", NOW)
        self.assertEqual(start, NOW - timedelta(hours=24))
        start, end = period_from_choice("4", NOW)
        self.assertEqual(start, NOW - timedelta(hours=12))
        with self.assertRaises(ValueError):
            period_from_choice("9", NOW)
        self.assertEqual(parse_user_datetime("01/10/2026"), datetime(2026, 10, 1))
        self.assertEqual(
            parse_user_datetime("01/10/2026", end_of_day=True),
            datetime(2026, 10, 1, 23, 59, 59),
        )
        self.assertEqual(parse_minimum_occurrences(""), 3)
        self.assertEqual(parse_minimum_occurrences("3"), 3)

    def test_slices_cover_the_period(self) -> None:
        day = build_slices(NOW - timedelta(hours=24), NOW)
        self.assertEqual(len(day), 6)
        self.assertEqual(day[0].start, NOW - timedelta(hours=24))
        self.assertEqual(day[-1].end, NOW)
        self.assertIn("4 horas", describe_slices(day))
        one_hour = build_slices(NOW - timedelta(hours=1), NOW)
        self.assertEqual(len(one_hour), 5)
        self.assertIn("12 minutos", describe_slices(one_hour))

        week = build_slices(NOW - timedelta(days=7), NOW)
        self.assertEqual(len(week), 7)
        self.assertEqual(week[-1].end, NOW)
        self.assertIn("1 dia", describe_slices(week))

        long = build_slices(NOW - timedelta(days=40), NOW)
        self.assertLessEqual(len(long), 31)
        self.assertEqual(long[0].start, NOW - timedelta(days=40))
        self.assertEqual(long[-1].end, NOW)


class AggregateTests(unittest.TestCase):
    def test_keeps_clients_low_in_two_intervals_at_the_same_location(self) -> None:
        first = datetime(2026, 10, 1, 0, 0)
        rows = aggregate_recurring(
            [
                _observation("aa:bb:cc:dd:ee:01", 4, "Global/Brasil/Loja Sul", first, rssi=-80),
                _observation(
                    "aa:bb:cc:dd:ee:01",
                    2,
                    "Global/Brasil/Loja Sul",
                    first + timedelta(hours=6),
                    rssi=-60,
                ),
                _observation("aa:bb:cc:dd:ee:02", 4, "Global/Brasil/Loja Sul", first),
                _observation("aa:bb:cc:dd:ee:03", 10, "Global/Brasil/Loja Norte", first),
                _observation("aa:bb:cc:dd:ee:03", 6, "Global/Brasil/Loja Norte", first + timedelta(hours=6)),
            ],
            minimum=2,
        )
        self.assertEqual([row["macAddress"] for row in rows], ["AA:BB:CC:DD:EE:01"])
        self.assertEqual(rows[0]["occurrences"], 2)
        self.assertEqual(rows[0]["worstConnectedScore"], 2)
        self.assertEqual(rows[0]["siteHierarchy"], "Global/Brasil/Loja Sul")
        self.assertEqual(rows[0]["worstRssi"], -80)
        self.assertNotIn("otherLocations", rows[0])

    def test_primary_location_is_the_most_frequent(self) -> None:
        first = datetime(2026, 10, 1, 0, 0)
        rows = aggregate_recurring(
            [
                _observation("aa", 4, "Global/Loja Sul", first),
                _observation("aa", 4, "Global/Loja Sul", first + timedelta(hours=6)),
                _observation("aa", 2, "Global/Loja Norte", first + timedelta(hours=12)),
            ],
            minimum=2,
        )
        self.assertEqual(rows[0]["siteHierarchy"], "Global/Loja Sul")
        self.assertEqual(rows[0]["otherLocations"], "Global/Loja Norte")
        self.assertEqual(rows[0]["occurrences"], 3)


class FetchTests(unittest.TestCase):
    def test_query_sends_period_and_groups_repeat_clients(self) -> None:
        class Stub:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def list_assurance_clients(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return [
                        {
                            "macAddress": "aa:bb:cc:00:00:01",
                            "connectedScore": 4,
                            "siteHierarchy": "Global/Brasil/Loja Sul",
                            "type": "Wireless",
                            "name": "caixa",
                        }
                    ]
                return [
                    {
                        "macAddress": "AA:BB:CC:00:00:01",
                        "connectedScore": 2,
                        "siteHierarchy": "Global/Brasil/Loja Sul",
                        "type": "Wireless",
                        "name": "caixa",
                    },
                    {
                        "macAddress": "AA:BB:CC:00:00:02",
                        "connectedScore": 10,
                        "siteHierarchy": "Global/Brasil/Loja Sul",
                        "type": "Wireless",
                    },
                ]

        stub = Stub()
        start = datetime(2026, 10, 1, 8, 0)
        end = start + timedelta(hours=12)
        mode, rows, columns = fetch_client_health(
            stub,  # type: ignore[arg-type]
            location="Loja Sul",
            client_type=None,
            period_start=start,
            period_end=end,
            recurring=True,
            minimum_occurrences=2,
        )
        self.assertEqual(mode, "recurring")
        self.assertEqual(len(stub.calls), 6)
        self.assertEqual(stub.calls[0]["site_hierarchy"], "Loja Sul")
        self.assertIsNotNone(stub.calls[0]["start_time"])
        self.assertEqual(stub.calls[0]["end_time"] - stub.calls[0]["start_time"], 2 * 60 * 60 * 1000)
        self.assertEqual([row["macAddress"] for row in rows], ["AA:BB:CC:00:00:01"])
        self.assertEqual(columns[0]["label"], "Ocorrências")

        with tempfile.TemporaryDirectory() as directory:
            csv_path, html_path = write_reports(
                mode=mode,
                rows=rows,
                columns=columns,
                host="https://dnac.example",
                location="Loja Sul",
                client_type=None,
                output_dir=Path(directory),
                now=NOW,
                period_label="01/10/2026 08:00 – 01/10/2026 20:00",
                minimum_occurrences=2,
            )
            html = html_path.read_text(encoding="utf-8")
        self.assertTrue(csv_path.name.startswith("clientes-reincidentes-"))
        self.assertIn("Clientes reincidentes com conexão baixa", html)
        self.assertIn("Loja Sul", html)
        self.assertIn("mínimo 2 ocorrências", html)
        self.assertIn("worstConnectedScore", html)
