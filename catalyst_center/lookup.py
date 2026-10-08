"""Consulta um dispositivo pelo MAC ou IP, e a saúde de um access point."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from catalyst_center.client import APIError, CatalystCenterClient, CatalystCenterError
from catalyst_center.health import Column, _first, _number, _text, select_columns

AP_VIEWS = ["configuration", "metrics", "apConfiguration", "apRadio"]
AP_FAMILY = "Unified AP"

AP_COLUMNS: list[tuple[str, str, str]] = [
    ("name", "AP", "text"),
    ("managementIpAddress", "IP", "text"),
    ("macAddress", "MAC", "text"),
    ("ethernetMac", "MAC Ethernet", "text"),
    ("siteHierarchy", "Localidade", "text"),
    ("apOperationalState", "Estado", "text"),
    ("connectedWlcName", "WLC", "text"),
    ("overallHealthScore", "Saúde", "score"),
    ("cpuScore", "Score de CPU", "score"),
    ("cpuUtilization", "CPU (%)", "number"),
    ("memoryScore", "Score de memória", "score"),
    ("memoryUtilization", "Memória (%)", "number"),
    ("wirelessClientCount", "Clientes", "number"),
    ("noiseScore", "Score de ruído", "score"),
    ("airQualityScore", "Score de qualidade do ar", "score"),
    ("interferenceScore", "Score de interferência", "score"),
    ("utilizationScore", "Score de utilização", "score"),
    ("radioSlot", "Rádio", "text"),
    ("radioBand", "Banda", "text"),
    ("noise", "Ruído do rádio (dBm)", "number"),
    ("airQuality", "Qualidade do ar do rádio (%)", "number"),
    ("interference", "Interferência do rádio (%)", "number"),
    ("utilization", "Utilização do rádio (%)", "number"),
    ("trafficUtil", "Tráfego do canal (%)", "number"),
    ("radioClientCount", "Clientes no rádio", "number"),
    ("platformId", "Modelo", "text"),
    ("softwareVersion", "Versão", "text"),
]

_MAC = re.compile(
    r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}$"
)
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def split_tokens(raw: str) -> list[str]:
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def classify_identifier(raw: str) -> tuple[str, str]:
    """Devolve ``mac``, ``ip`` ou ``name`` e o valor normalizado."""
    text = raw.strip()
    if _MAC.fullmatch(text):
        return "mac", text.upper()
    if _IPV4.fullmatch(text) and all(0 <= int(part) <= 255 for part in text.split(".")):
        return "ip", text
    return "name", text


def parse_device_identifiers(raw: str) -> tuple[list[str], list[str]]:
    tokens = split_tokens(raw)
    if not tokens:
        raise CatalystCenterError("Informe o MAC ou o IP do dispositivo.")
    macs: list[str] = []
    ips: list[str] = []
    for token in tokens:
        kind, value = classify_identifier(token)
        if kind == "mac":
            macs.append(value)
        elif kind == "ip":
            ips.append(value)
        else:
            raise CatalystCenterError(f"{token} não é um MAC nem um IPv4.")
    return macs, ips


def fetch_device_connection(
    client: CatalystCenterClient,
    *,
    query: str,
    period_start: datetime,
    period_end: datetime,
) -> tuple[list[dict[str, Any]], list[Column]]:
    """Saúde da conexão dos clientes cujo MAC ou IPv4 foi informado."""
    from catalyst_center.health import CLIENT_COLUMNS, normalize_client

    macs, ips = parse_device_identifiers(query)
    start = _epoch_ms(period_start)
    end = _epoch_ms(period_end)
    raw: list[dict[str, Any]] = []
    if macs:
        print(f"Consultando dispositivo pelo MAC {', '.join(macs)}...", flush=True)
        raw.extend(
            _client_rows(
                client,
                mac_address=macs,
                start_time=start,
                end_time=end,
            )
        )
    if ips:
        print(f"Consultando dispositivo pelo IP {', '.join(ips)}...", flush=True)
        raw.extend(
            _client_rows(
                client,
                ipv4_address=ips,
                start_time=start,
                end_time=end,
            )
        )
    wanted_macs = {_mac_key(item) for item in macs}
    wanted_ips = {item for item in ips}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = normalize_client(item)
        mac = _mac_key(str(row.get("macAddress") or ""))
        ip = str(row.get("ipv4Address") or "").strip()
        if wanted_macs or wanted_ips:
            if mac not in wanted_macs and ip not in wanted_ips:
                continue
        key = mac or ip
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        rows.append(row)
    if not rows:
        print("Nenhum dispositivo com esse MAC ou IP no período.", flush=True)
    return rows, select_columns(rows, CLIENT_COLUMNS)


def fetch_access_points(
    client: CatalystCenterClient,
    *,
    query: str,
    location: str,
    period_start: datetime,
    period_end: datetime,
    notes: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[Column]]:
    """Saúde do access point e de cada rádio no período."""
    text = query.strip()
    place = location.strip()
    if not text and not place:
        raise CatalystCenterError("Informe o access point ou uma localidade.")
    kind, value = classify_identifier(text) if text else ("", "")
    start = _epoch_ms(period_start)
    end = _epoch_ms(period_end)
    print("Consultando a saúde do access point...", flush=True)
    if kind == "mac":
        raw = _device_rows(client, mac_address=value, start_time=start, end_time=end, notes=notes)
    elif kind == "ip":
        raw = _device_rows(client, management_ip=value, start_time=start, end_time=end, notes=notes)
    else:
        raw = _device_rows(
            client,
            family=AP_FAMILY,
            site_hierarchy=place or None,
            start_time=start,
            end_time=end,
            notes=notes,
        )
        if place and not raw:
            _note(notes, "A localidade não veio na consulta da API. O filtro foi aplicado no resultado.")
            raw = _device_rows(
                client,
                family=AP_FAMILY,
                start_time=start,
                end_time=end,
                notes=notes,
            )
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not is_access_point(item):
            continue
        if not _matches_ap_query(item, kind, value, place):
            continue
        rows.extend(access_point_rows(item))
    rows.sort(
        key=lambda row: (
            str(row.get("name") or ""),
            str(row.get("radioBand") or ""),
            str(row.get("radioSlot") or ""),
        )
    )
    if not rows:
        print("Nenhum access point encontrado para essa consulta.", flush=True)
    return rows, select_columns(rows, AP_COLUMNS)


def is_access_point(record: dict[str, Any]) -> bool:
    family = str(_first(record, "deviceFamily") or "").casefold()
    device_type = str(_first(record, "deviceType", "type") or "").casefold()
    if "access point" in family or "access point" in device_type or "unified ap" in family or family.endswith(" ap"):
        return True
    if _first(record, "apDetails.apOperationalState", "apOperationalState", "apDetails.connectedWlcName"):
        return True
    radios = _first(record, "apDetails.radios", "radios")
    return isinstance(radios, list) and bool(radios)


def access_point_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Uma linha por rádio. Sem rádio, uma linha com a saúde do AP."""
    base = {
        "name": _text(_first(record, "name")),
        "managementIpAddress": _text(_first(record, "managementIpAddress", "ipv4Address")),
        "macAddress": _text(_first(record, "macAddress")),
        "ethernetMac": _text(_first(record, "apDetails.ethernetMac", "ethernetMac")),
        "siteHierarchy": _text(_first(record, "siteHierarchy")),
        "apOperationalState": _text(_first(record, "apDetails.apOperationalState", "apOperationalState")),
        "connectedWlcName": _text(_first(record, "apDetails.connectedWlcName", "connectedWlcName")),
        "overallHealthScore": _score(_first(record, "metricsDetails.overallHealthScore", "overallHealthScore")),
        "cpuScore": _score(_first(record, "metricsDetails.cpuScore", "cpuScore")),
        "cpuUtilization": _number(_first(record, "metricsDetails.cpuUtilization", "cpuUtilization")),
        "memoryScore": _score(_first(record, "metricsDetails.memoryScore", "memoryScore")),
        "memoryUtilization": _number(
            _first(record, "metricsDetails.memoryUtilization", "metricsDetails.memory", "memoryUtilization")
        ),
        "wirelessClientCount": _number(
            _first(record, "wirelessClientCount", "metricsDetails.clientCount", "clientCount")
        ),
        "noiseScore": _score(_first(record, "metricsDetails.noiseScore", "noiseScore")),
        "airQualityScore": _score(_first(record, "metricsDetails.airQualityScore", "airQualityScore")),
        "interferenceScore": _score(_first(record, "metricsDetails.interferenceScore", "interferenceScore")),
        "utilizationScore": _score(_first(record, "metricsDetails.utilizationScore", "utilizationScore")),
        "platformId": _text(_first(record, "platformId")),
        "softwareVersion": _text(_first(record, "softwareVersion")),
    }
    radios = _first(record, "apDetails.radios", "radios")
    if not isinstance(radios, list) or not radios:
        row = {key: value for key, value in base.items() if value is not None}
        return [row] if row else []
    built: list[dict[str, Any]] = []
    for radio in radios:
        if not isinstance(radio, dict):
            continue
        row = {
            **base,
            "radioSlot": _text(_first(radio, "slot", "id")),
            "radioBand": _text(_first(radio, "radioBand", "band")),
            "noise": _number(_first(radio, "noise")),
            "airQuality": _number(_first(radio, "airQuality")),
            "interference": _number(_first(radio, "interference")),
            "utilization": _number(_first(radio, "utilization")),
            "trafficUtil": _number(_first(radio, "trafficUtil")),
            "radioClientCount": _number(_first(radio, "clientCount")),
        }
        built.append({key: value for key, value in row.items() if value is not None})
    return built


def _matches_ap_query(record: dict[str, Any], kind: str, value: str, location: str) -> bool:
    if location:
        hierarchy = str(_first(record, "siteHierarchy") or "")
        if hierarchy and location.casefold() not in hierarchy.casefold():
            return False
    if not value:
        return True
    if kind == "mac":
        candidates = [
            _first(record, "macAddress"),
            _first(record, "apDetails.ethernetMac", "ethernetMac"),
        ]
        wanted = _mac_key(value)
        return any(_mac_key(str(item or "")) == wanted for item in candidates)
    if kind == "ip":
        ip = str(_first(record, "managementIpAddress", "ipv4Address") or "").strip()
        return ip == value
    name = str(_first(record, "name") or "")
    return value.casefold() in name.casefold()


def _client_rows(client: CatalystCenterClient, **kwargs: Any) -> list[dict[str, Any]]:
    try:
        rows = client.list_assurance_clients(**kwargs)
    except APIError as exc:
        if exc.status_code == 404:
            raise CatalystCenterError(
                "A lista individual de clientes não está disponível nesta versão, "
                "então não dá para consultar o dispositivo pelo MAC ou IP."
            ) from exc
        raise
    return [item for item in rows if isinstance(item, dict)]


def _device_rows(client: CatalystCenterClient, *, notes: list[str] | None = None, **kwargs: Any) -> list[dict[str, Any]]:
    try:
        rows = client.list_network_devices(views=AP_VIEWS, **kwargs)
    except APIError as exc:
        if exc.status_code == 400:
            _note(notes, "Esta versão não aceitou o detalhe do rádio. A consulta segue com a saúde do AP.")
            rows = client.list_network_devices(**kwargs)
        elif exc.status_code == 404:
            raise CatalystCenterError(
                "A consulta de access points não está disponível nesta versão do Catalyst Center."
            ) from exc
        else:
            raise
    return [item for item in rows if isinstance(item, dict)]


def _score(value: Any) -> int | float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return number


def _mac_key(value: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", value).upper()


def _epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _note(notes: list[str] | None, message: str) -> None:
    if notes is not None and message in notes:
        return
    print(message, flush=True)
    if notes is not None:
        notes.append(message)
