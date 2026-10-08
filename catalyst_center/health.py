"""Normaliza a resposta de Assurance › Health › Client para tabela e CSV."""

from __future__ import annotations

from datetime import datetime
from typing import Any

Column = dict[str, str]

# key, rótulo, tipo de célula (text, score, percent, number)
CLIENT_COLUMNS: list[tuple[str, str, str]] = [
    ("macAddress", "MAC", "text"),
    ("name", "Nome", "text"),
    ("username", "Usuário", "text"),
    ("userId", "ID do usuário", "text"),
    ("type", "Tipo", "text"),
    ("connectionStatus", "Status", "text"),
    ("overallScore", "Saúde", "score"),
    ("onboardingScore", "Onboarding", "score"),
    ("connectedScore", "Conectividade", "score"),
    ("ipv4Address", "IPv4", "text"),
    ("ipv6Addresses", "IPv6", "text"),
    ("siteHierarchy", "Localidade", "text"),
    ("osType", "Sistema", "text"),
    ("osVersion", "Versão do SO", "text"),
    ("vendor", "Fabricante", "text"),
    ("deviceType", "Tipo de dispositivo", "text"),
    ("formFactor", "Formato", "text"),
    ("connectedNetworkDeviceName", "Dispositivo conectado", "text"),
    ("connectedNetworkDeviceManagementIp", "IP do dispositivo", "text"),
    ("connectedNetworkDeviceType", "Tipo do dispositivo", "text"),
    ("apMac", "MAC do AP", "text"),
    ("interfaceName", "Interface", "text"),
    ("ssid", "SSID", "text"),
    ("band", "Banda", "text"),
    ("rssi", "RSSI (dBm)", "number"),
    ("snr", "SNR (dB)", "number"),
    ("vlanId", "VLAN", "text"),
    ("channel", "Canal", "text"),
    ("protocol", "Protocolo", "text"),
    ("authType", "Autenticação", "text"),
    ("wlcName", "WLC", "text"),
    ("usage", "Uso (bytes)", "number"),
    ("txBytes", "Enviados (bytes)", "number"),
    ("rxBytes", "Recebidos (bytes)", "number"),
    ("sessionDuration", "Duração da sessão", "text"),
    ("lastUpdatedTime", "Atualizado em", "text"),
    ("tracked", "Rastreado", "text"),
    ("isPrivateMacAddress", "MAC privado", "text"),
]

SITE_COLUMNS: list[tuple[str, str, str]] = [
    ("siteName", "Localidade", "text"),
    ("parentSiteName", "Localidade pai", "text"),
    ("siteType", "Tipo", "text"),
    ("healthyClientsPercentage", "Clientes saudáveis (%)", "percent"),
    ("clientHealth", "Saúde dos clientes", "score"),
    ("clientHealthWired", "Saúde cabeados", "score"),
    ("clientHealthWireless", "Saúde wireless", "score"),
    ("numberOfClients", "Total de clientes", "number"),
    ("numberOfWiredClients", "Clientes cabeados", "number"),
    ("numberOfWirelessClients", "Clientes wireless", "number"),
    ("siteId", "ID da localidade", "text"),
]

CLIENT_LOCATION_FIELDS = ("siteHierarchy",)
SITE_LOCATION_FIELDS = ("siteName", "parentSiteName")

_STATUS_LABELS = {
    "connected": "Conectado",
    "disconnected": "Desconectado",
    "connecting": "Conectando",
    "unknown": "Desconhecido",
}


def select_columns(
    rows: list[dict[str, Any]],
    catalog: list[tuple[str, str, str]],
) -> list[Column]:
    """Mantém a ordem do catálogo e omite colunas vazias quando há dados."""
    if not rows:
        return [{"key": key, "label": label, "kind": kind} for key, label, kind in catalog]
    used = {
        key
        for row in rows
        for key, value in row.items()
        if value not in (None, "")
    }
    return [
        {"key": key, "label": label, "kind": kind}
        for key, label, kind in catalog
        if key in used
    ]


def filter_by_ssid(
    rows: list[dict[str, Any]],
    ssids: tuple[str, ...] | list[str],
    *,
    keep_wired: bool = False,
) -> list[dict[str, Any]]:
    """Mantém clientes sem fio do SSID informado.

    A comparação ignora maiúsculas. ``*`` no nome funciona como curinga.
    Com ``keep_wired``, clientes cabeados permanecem no resultado.
    """
    patterns = tuple(item.strip() for item in ssids if item and item.strip())
    if not patterns:
        return rows
    wireless = [row for row in rows if _is_wireless(row)]
    if wireless and not any(str(row.get("ssid") or "").strip() for row in wireless):
        return rows if keep_wired else wireless
    selected: list[dict[str, Any]] = []
    for row in rows:
        if _is_wireless(row):
            if _ssid_matches(str(row.get("ssid") or ""), patterns):
                selected.append(row)
        elif keep_wired:
            selected.append(row)
    return selected


def _is_wireless(row: dict[str, Any]) -> bool:
    return str(row.get("type") or "").casefold() == "wireless"


def _ssid_matches(ssid: str, patterns: tuple[str, ...]) -> bool:
    value = ssid.casefold()
    if not value:
        return False
    for pattern in patterns:
        token = pattern.casefold()
        if "*" not in token:
            if value == token:
                return True
            continue
        parts = token.split("*")
        if len(parts) == 2 and parts[0] == "" and parts[1] == "":
            return True
        cursor = 0
        matched = True
        for index, part in enumerate(parts):
            if part == "":
                continue
            found = value.find(part, cursor)
            if found < 0 or (index == 0 and not token.startswith("*") and found != 0):
                matched = False
                break
            cursor = found + len(part)
        if matched and (token.endswith("*") or cursor == len(value)):
            return True
    return False


def filter_by_location(
    rows: list[dict[str, Any]],
    query: str | None,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Filtra por trecho do nome da localidade.

    Se a API não trouxe nenhum campo de localidade, as linhas são mantidas:
    o filtro já foi aplicado na consulta.
    """
    needle = (query or "").strip().casefold()
    if not needle:
        return rows
    if not any(row.get(field) not in (None, "") for row in rows for field in fields):
        return rows
    selected: list[dict[str, Any]] = []
    for row in rows:
        haystack = " ".join(str(row.get(field) or "") for field in fields).casefold()
        if needle in haystack:
            selected.append(row)
    return selected


def normalize_client(record: dict[str, Any]) -> dict[str, Any]:
    """Achata um cliente da API de dados, com ou sem objetos aninhados."""
    row: dict[str, Any] = {
        "macAddress": _text(_first(record, "macAddress", "id")),
        "name": _text(_first(record, "name")),
        "username": _text(_first(record, "username")),
        "userId": _text(_first(record, "userId")),
        "type": _text(_first(record, "type")),
        "connectionStatus": _status(_first(record, "connectionStatus")),
        "overallScore": _score(
            _first(record, "overallScore", "health.overallScore"),
            _score_from_list(record, "OVERALL"),
        ),
        "onboardingScore": _score(
            _first(record, "onboardingScore", "health.onboardingScore"),
            _score_from_list(record, "ONBOARDING"),
        ),
        "connectedScore": _score(
            _first(record, "connectedScore", "health.connectedScore"),
            _score_from_list(record, "CONNECTED"),
        ),
        "ipv4Address": _text(_first(record, "ipv4Address")),
        "ipv6Addresses": _text(_first(record, "ipv6Addresses")),
        "siteHierarchy": _text(_first(record, "siteHierarchy", "location")),
        "osType": _text(_first(record, "osType")),
        "osVersion": _text(_first(record, "osVersion")),
        "vendor": _text(_first(record, "vendor")),
        "deviceType": _text(_first(record, "deviceType")),
        "formFactor": _text(_first(record, "formFactor")),
        "connectedNetworkDeviceName": _text(
            _first(
                record,
                "connectedNetworkDeviceName",
                "connectedNetworkDevice.connectedNetworkDeviceName",
            )
        ),
        "connectedNetworkDeviceManagementIp": _text(
            _first(
                record,
                "connectedNetworkDeviceManagementIp",
                "connectedNetworkDevice.connectedNetworkDeviceManagementIp",
            )
        ),
        "connectedNetworkDeviceType": _text(
            _first(
                record,
                "connectedNetworkDeviceType",
                "connectedNetworkDevice.connectedNetworkDeviceType",
            )
        ),
        "apMac": _text(
            _first(
                record,
                "apMac",
                "connectedNetworkDeviceMac",
                "connectedNetworkDevice.connectedNetworkDeviceMac",
            )
        ),
        "interfaceName": _text(
            _first(record, "interfaceName", "connectedNetworkDevice.interfaceName")
        ),
        "ssid": _text(_first(record, "ssid", "SSID", "connection.ssid", "wlanSsid")),
        "band": _text(_first(record, "band", "connection.band")),
        "rssi": _number(_first(record, "rssi", "connection.rssi")),
        "snr": _number(_first(record, "snr", "connection.snr")),
        "vlanId": _text(_first(record, "vlanId", "connection.vlanId")),
        "channel": _text(_first(record, "channel", "connection.channel")),
        "protocol": _text(_first(record, "protocol", "connection.protocol")),
        "authType": _text(_first(record, "authType", "connection.authType")),
        "wlcName": _text(_first(record, "wlcName", "connection.wlcName")),
        "usage": _number(_first(record, "usage", "traffic.usage")),
        "txBytes": _number(_first(record, "txBytes", "traffic.txBytes")),
        "rxBytes": _number(_first(record, "rxBytes", "traffic.rxBytes")),
        "sessionDuration": format_duration(
            _first(record, "sessionDuration", "connection.sessionDuration")
        ),
        "lastUpdatedTime": format_timestamp(_first(record, "lastUpdatedTime")),
        "tracked": _text(_first(record, "tracked")),
        "isPrivateMacAddress": _text(_first(record, "isPrivateMacAddress")),
    }
    return {key: value for key, value in row.items() if value is not None}


def normalize_site(record: dict[str, Any]) -> dict[str, Any]:
    """Uma linha de saúde de clientes para um site."""
    row: dict[str, Any] = {
        "siteName": _text(record.get("siteName")),
        "parentSiteName": _text(record.get("parentSiteName")),
        "siteType": _text(record.get("siteType")),
        "healthyClientsPercentage": _score(record.get("healthyClientsPercentage")),
        "clientHealth": _score(record.get("clientHealth")),
        "clientHealthWired": _score(record.get("clientHealthWired")),
        "clientHealthWireless": _score(record.get("clientHealthWireless")),
        "numberOfClients": _number(record.get("numberOfClients")),
        "numberOfWiredClients": _number(record.get("numberOfWiredClients")),
        "numberOfWirelessClients": _number(record.get("numberOfWirelessClients")),
        "siteId": _text(record.get("siteId")),
    }
    return {key: value for key, value in row.items() if value is not None}


def format_timestamp(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1_000_000_000_000:
        seconds = number / 1000
    elif number > 1_000_000_000:
        seconds = number
    else:
        return None
    try:
        moment = datetime.fromtimestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%d/%m/%Y %H:%M")


def format_duration(value: Any) -> str | None:
    """A API informa a sessão em milissegundos."""
    try:
        milliseconds = int(float(value))
    except (TypeError, ValueError):
        return None
    if milliseconds < 0:
        return None
    total_seconds = milliseconds // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}min"
    if minutes:
        return f"{minutes}min {seconds:02d}s"
    return f"{seconds}s"


def _first(record: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = record
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return None


def _score_from_list(record: dict[str, Any], health_type: str) -> Any:
    raw = record.get("healthScore")
    if not isinstance(raw, list):
        return None
    wanted = health_type.upper()
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("healthType") or item.get("scoreCategory") or "").upper()
        if kind == wanted or kind.endswith(wanted):
            return item.get("score")
    return None


def _score(*values: Any) -> int | float | None:
    for value in values:
        number = _number(value)
        if number is None:
            continue
        if number < 0:
            continue
        return number
    return None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if isinstance(value, list):
        parts = [str(item) for item in value if item not in (None, "")]
        return ", ".join(parts) if parts else None
    if isinstance(value, dict):
        return None
    return str(value)


def _status(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return _STATUS_LABELS.get(text.casefold(), text)
