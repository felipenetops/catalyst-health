"""Página local para montar e executar a consulta de saúde dos clientes."""

from __future__ import annotations

import html
import json
import re
import threading
import webbrowser
from collections.abc import Mapping
from datetime import datetime, timedelta
from email import message_from_bytes
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from catalyst_center.client import AuthenticationError, CatalystCenterError
from catalyst_center.consultar import (
    DEFAULT_HOST,
    REPORT_DIR,
    _parse_client_type,
    run_ap_lookup,
    run_consultation,
    run_device_lookup,
)
from catalyst_center.lookup import classify_identifier, split_tokens

PAGE_HOST = "127.0.0.1"
PAGE_PORT = 8780
PAGE_FILE = Path(__file__).resolve().parent.parent / "Consulta.html"
_QUERY_LOCK = threading.Lock()
_CSV_NAME = re.compile(
    r"^clientes-(?:saude|reincidentes|dispositivo|ap)-\d{8}-\d{6}\.csv$"
)


class ConsultationForm:
    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool,
        client_type: str | None,
        ssids: tuple[str, ...],
        location: str,
        period_start: datetime,
        period_end: datetime,
        recurring: bool,
        minimum_occurrences: int,
        connection_low: int,
        connection_high: int,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.client_type = client_type
        self.ssids = ssids
        self.location = location
        self.period_start = period_start
        self.period_end = period_end
        self.recurring = recurring
        self.minimum_occurrences = minimum_occurrences
        self.connection_low = connection_low
        self.connection_high = connection_high
        self.tab = "clientes"


class DeviceLookupForm:
    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool,
        query: str,
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        self.tab = "dispositivo"
        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.query = query
        self.period_start = period_start
        self.period_end = period_end


class ApLookupForm:
    def __init__(
        self,
        *,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool,
        query: str,
        location: str,
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        self.tab = "ap"
        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.query = query
        self.location = location
        self.period_start = period_start
        self.period_end = period_end


def parse_page_form(
    fields: Mapping[str, str], *, now: datetime | None = None
) -> ConsultationForm | DeviceLookupForm | ApLookupForm:
    """Lê a aba enviada pela página e valida só os campos dessa consulta."""
    tab = fields.get("aba", "clientes").strip() or "clientes"
    if tab == "clientes":
        return parse_consultation_form(fields, now=now)
    if tab == "dispositivo":
        return _parse_device_form(fields, now=now)
    if tab == "ap":
        return _parse_ap_form(fields, now=now)
    raise ValueError("Escolha uma das consultas: Clientes, Dispositivo ou Access point.")


def parse_consultation_form(fields: Mapping[str, str], *, now: datetime | None = None) -> ConsultationForm:
    """Lê o formulário de clientes. A senha só existe neste objeto e não é devolvida à página."""
    host, username, password, period_start, period_end = _common_fields(fields, now)
    try:
        client_type = _parse_client_type(fields.get("client_type", ""))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    ssids = tuple(part.strip() for part in fields.get("ssids", "").split(",") if part.strip())
    if client_type != "Wired" and not ssids:
        raise ValueError("Informe ao menos um SSID para a rede sem fio.")
    if client_type == "Wired":
        ssids = ()
    location = fields.get("location", "").strip()
    recurring = fields.get("consulta", "todos") == "reincidentes"
    minimum = 3
    if recurring:
        raw_minimum = fields.get("minimo", "").strip() or "3"
        if not raw_minimum.isdigit() or int(raw_minimum) < 1:
            raise ValueError("O mínimo de ocorrências precisa ser um número inteiro maior que zero.")
        minimum = int(raw_minimum)
    connection_low = _parse_connection_score(fields.get("conexao_de", ""), default=1)
    connection_high = _parse_connection_score(fields.get("conexao_ate", ""), default=7)
    return ConsultationForm(
        host=host,
        username=username,
        password=password,
        verify_ssl=False,
        client_type=client_type,
        ssids=ssids,
        location=location,
        period_start=period_start,
        period_end=period_end,
        recurring=recurring,
        minimum_occurrences=minimum,
        connection_low=connection_low,
        connection_high=connection_high,
    )


def _parse_device_form(fields: Mapping[str, str], *, now: datetime | None = None) -> DeviceLookupForm:
    host, username, password, period_start, period_end = _common_fields(fields, now)
    query = fields.get("dispositivo", "").strip()
    tokens = split_tokens(query)
    if not tokens:
        raise ValueError("Informe o MAC ou o IP do dispositivo.")
    for token in tokens:
        kind, _value = classify_identifier(token)
        if kind == "name":
            raise ValueError(f"{token} não é um MAC nem um IPv4.")
    return DeviceLookupForm(
        host=host,
        username=username,
        password=password,
        verify_ssl=False,
        query=query,
        period_start=period_start,
        period_end=period_end,
    )


def _parse_ap_form(fields: Mapping[str, str], *, now: datetime | None = None) -> ApLookupForm:
    host, username, password, period_start, period_end = _common_fields(fields, now)
    query = fields.get("ap", "").strip()
    location = fields.get("ap_location", "").strip()
    if not query and not location:
        raise ValueError("Informe o access point ou uma localidade.")
    return ApLookupForm(
        host=host,
        username=username,
        password=password,
        verify_ssl=False,
        query=query,
        location=location,
        period_start=period_start,
        period_end=period_end,
    )


def _common_fields(
    fields: Mapping[str, str], now: datetime | None
) -> tuple[str, str, str, datetime, datetime]:
    clock = now or datetime.now()
    host = fields.get("host", "").strip() or DEFAULT_HOST
    username = fields.get("username", "").strip()
    password = fields.get("password", "")
    if not username or not password:
        raise ValueError("Informe o usuário e a senha do Catalyst Center.")
    period_start = _parse_datetime(fields.get("inicio", ""), "início")
    period_end = _parse_datetime(fields.get("fim", ""), "fim")
    if period_end <= period_start:
        raise ValueError("O fim do período precisa ser depois do início.")
    if period_end > clock + timedelta(minutes=1):
        raise ValueError("O fim do período não pode ser no futuro.")
    return host, username, password, period_start, period_end


def serve(host: str = PAGE_HOST, port: int = PAGE_PORT, *, open_browser: bool = True) -> int:
    """Sobe a página só nesta máquina e abre o navegador."""
    server = None
    chosen = port
    for candidate in range(port, port + 10):
        try:
            server = ThreadingHTTPServer((host, candidate), ConsultationHandler)
            chosen = candidate
            break
        except OSError:
            continue
    if server is None:
        print(f"Não foi possível abrir a página nas portas {port}–{port + 9}.")
        return 1
    url = f"http://{host}:{chosen}/"
    print(f"Página local: {url}")
    print("Encerre com Ctrl+C.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPágina encerrada.")
    finally:
        server.server_close()
    return 0


class ConsultationHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/Consulta.html"}:
            if not PAGE_FILE.is_file():
                _send_json(self, {"error": "Arquivo Consulta.html não encontrado."}, status=500)
                return
            _send_html(self, PAGE_FILE.read_text(encoding="utf-8"))
            return
        if path.startswith("/csv/"):
            self._send_csv(unquote(path.removeprefix("/csv/")))
            return
        _send_json(self, {"error": "Página não encontrada."}, status=404)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/consultar":
            _send_json(self, {"error": "Página não encontrada."}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 100_000:
            _send_json(self, {"error": "Formulário grande demais."}, status=400)
            return
        raw = self.rfile.read(length)
        fields = parse_request_fields(raw, self.headers.get("Content-Type", ""))
        try:
            form = parse_page_form(fields)
        except ValueError as exc:
            _send_json(self, {"error": str(exc)}, status=400)
            return
        if not _QUERY_LOCK.acquire(blocking=False):
            _send_json(
                self,
                {"error": "Já existe uma consulta em andamento. Espere ela terminar."},
                status=409,
            )
            return
        try:
            if isinstance(form, DeviceLookupForm):
                csv_path, html_path, count = run_device_lookup(
                    host=form.host,
                    username=form.username,
                    password=form.password,
                    query=form.query,
                    period_start=form.period_start,
                    period_end=form.period_end,
                    verify_ssl=form.verify_ssl,
                )
            elif isinstance(form, ApLookupForm):
                csv_path, html_path, count = run_ap_lookup(
                    host=form.host,
                    username=form.username,
                    password=form.password,
                    query=form.query,
                    location=form.location,
                    period_start=form.period_start,
                    period_end=form.period_end,
                    verify_ssl=form.verify_ssl,
                )
            else:
                csv_path, html_path, count = run_consultation(
                    host=form.host,
                    username=form.username,
                    password=form.password,
                    client_type=form.client_type,
                    ssids=form.ssids,
                    location=form.location,
                    period_start=form.period_start,
                    period_end=form.period_end,
                    recurring=form.recurring,
                    minimum_occurrences=form.minimum_occurrences,
                    connection_low=form.connection_low,
                    connection_high=form.connection_high,
                    verify_ssl=form.verify_ssl,
                )
        except AuthenticationError:
            _send_json(self, {"error": "Usuário ou senha recusados pelo Catalyst Center."}, status=401)
            return
        except CatalystCenterError as exc:
            message = str(exc)
            if "SSL" in message.upper() or "CERTIFICATE" in message.upper() or "TLS" in message.upper():
                message += " Marque a opção de não verificar o certificado se o Catalyst Center usar um certificado interno."
            _send_json(self, {"error": message}, status=502)
            return
        finally:
            _QUERY_LOCK.release()
        page = html_path.read_text(encoding="utf-8")
        _send_html(self, _with_result_bar(page, csv_path.name, count))

    def _send_csv(self, name: str) -> None:
        if not _CSV_NAME.fullmatch(name):
            _send_json(self, {"error": "Arquivo não encontrado."}, status=404)
            return
        path = REPORT_DIR / name
        if not path.is_file():
            _send_json(self, {"error": "Arquivo não encontrado."}, status=404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[página] {self.command} {urlparse(self.path).path}", flush=True)


def parse_request_fields(raw: bytes, content_type: str) -> dict[str, str]:
    """Lê o formulário enviado pelo navegador, inclusive o formato multipart."""
    if "multipart/form-data" in content_type.lower():
        message = message_from_bytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw,
            policy=HTTP,
        )
        fields: dict[str, str] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                fields[str(name)] = payload.decode("utf-8", errors="replace")
            else:
                fields[str(name)] = "" if payload is None else str(payload)
        return fields
    parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


def _parse_connection_score(raw: str, *, default: int) -> int:
    text = raw.strip() or str(default)
    if not text.isdigit() or not 1 <= int(text) <= 10:
        raise ValueError("A conexão precisa ser um valor de 1 a 10.")
    return int(text)


def _parse_datetime(value: str, label: str) -> datetime:
    text = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Informe data e hora de {label}.")



def _with_result_bar(page: str, csv_name: str, count: int) -> str:
    safe_name = html.escape(csv_name, quote=True)
    bar = (
        '<div style="display:flex;gap:16px;align-items:center;padding:12px 24px;'
        'background:#0e5f73;color:#fff;font-family:Segoe UI,system-ui,sans-serif">'
        '<a href="/" style="color:#fff">Nova consulta</a>'
        f'<a href="/csv/{safe_name}" style="color:#fff">Baixar CSV</a>'
        f"<span>{count} registro(s)</span></div>"
    )
    return page.replace("<body>", "<body>" + bar, 1)


def _send_html(handler: BaseHTTPRequestHandler, page: str, status: int = 200) -> None:
    body = page.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)



def _send_json(handler: BaseHTTPRequestHandler, payload: dict[str, str], status: int = 400) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)
