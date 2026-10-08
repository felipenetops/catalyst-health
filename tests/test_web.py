"""Formulário da página local, sem consultar o Catalyst Center."""

from __future__ import annotations

import unittest
from datetime import datetime

from pathlib import Path

from catalyst_center.web import parse_consultation_form, parse_page_form, parse_request_fields

NOW = datetime(2026, 10, 1, 17, 0)
PAGE = Path(__file__).resolve().parent.parent / "Consulta.html"


def _fields(**extra: str) -> dict[str, str]:
    base = {
        "host": "https://dnac.example",
        "username": "ana",
        "password": "segredo",
        "client_type": "Wireless",
        "ssids": "rfboti1",
        "location": "Serra",
        "inicio": "2026-10-01T14:00",
        "fim": "2026-10-01T17:00",
        "consulta": "reincidentes",
        "minimo": "3",
        "conexao_de": "2",
        "conexao_ate": "9",
    }
    base.update(extra)
    return base


class FormTests(unittest.TestCase):
    def test_reads_start_and_end(self) -> None:
        form = parse_consultation_form(_fields(), now=NOW)
        self.assertEqual(form.period_start, datetime(2026, 10, 1, 14, 0))
        self.assertEqual(form.period_end, datetime(2026, 10, 1, 17, 0))
        self.assertTrue(form.recurring)
        self.assertEqual(form.minimum_occurrences, 3)
        self.assertEqual(form.connection_low, 2)
        self.assertEqual(form.connection_high, 9)
        self.assertFalse(form.verify_ssl)
        self.assertEqual(form.ssids, ("rfboti1",))

    def test_rejects_end_before_start(self) -> None:
        with self.assertRaises(ValueError):
            parse_consultation_form(_fields(inicio="2026-10-01T18:00", fim="2026-10-01T17:00"), now=NOW)

    def test_wired_does_not_require_ssid(self) -> None:
        form = parse_consultation_form(
            _fields(client_type="Wired", ssids="", consulta="todos"),
            now=NOW,
        )
        self.assertEqual(form.client_type, "Wired")
        self.assertEqual(form.ssids, ())
        self.assertFalse(form.recurring)

    def test_page_has_datetime_fields_and_hides_password(self) -> None:
        page = PAGE.read_text(encoding="utf-8")
        self.assertIn('type="datetime-local"', page)
        self.assertIn('name="inicio"', page)
        self.assertIn('name="fim"', page)
        self.assertNotIn("password" + ' value=', page)
        self.assertIn('name="conexao_de"', page)
        self.assertIn('name="conexao_ate"', page)
        self.assertIn("Conexão de", page)
        self.assertIn("Tipo de conexão", page)
        self.assertIn('data-tab="clientes"', page)
        self.assertIn('data-tab="dispositivo"', page)
        self.assertIn('data-tab="ap"', page)
        self.assertIn("Access point", page)
        self.assertIn("Abrir no Windows", page)
        self.assertIn("Abrir no Mac", page)
        self.assertIn("Abrir no Linux", page)
        self.assertNotIn("certificado TLS", page)

    def test_reads_password_from_multipart_form(self) -> None:
        boundary = "----consulta"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="username"\r\n\r\n'
            "felipeps\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="password"\r\n\r\n'
            "segredo\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        fields = parse_request_fields(body, f"multipart/form-data; boundary={boundary}")
        self.assertEqual(fields["username"], "felipeps")
        self.assertEqual(fields["password"], "segredo")

    def test_device_tab_accepts_mac_and_ip(self) -> None:
        form = parse_page_form(
            _fields(aba="dispositivo", dispositivo="aa:bb:cc:dd:ee:ff, 10.1.1.10", ssids=""),
            now=NOW,
        )
        self.assertEqual(form.tab, "dispositivo")
        self.assertEqual(form.query, "aa:bb:cc:dd:ee:ff, 10.1.1.10")

    def test_device_tab_rejects_a_name(self) -> None:
        with self.assertRaises(ValueError):
            parse_page_form(_fields(aba="dispositivo", dispositivo="AP-LOJA", ssids=""), now=NOW)

    def test_ap_tab_requires_ap_or_location(self) -> None:
        with self.assertRaises(ValueError):
            parse_page_form(_fields(aba="ap", ap="", ap_location="", ssids=""), now=NOW)
        form = parse_page_form(
            _fields(aba="ap", ap="Loja-AP", ap_location="Serra", ssids=""),
            now=NOW,
        )
        self.assertEqual(form.tab, "ap")
        self.assertEqual(form.query, "Loja-AP")
        self.assertEqual(form.location, "Serra")
