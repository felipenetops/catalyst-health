"""Clientes que repetem saúde de conexão baixa ao longo de um período."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from catalyst_center.health import Column

# Conexão baixa: score de conectividade de 1 a 7. Zero é inativo; 8, 9 e 10 ficam de fora.
LOW_CONNECTION_MAX = 7
DEFAULT_MINIMUM_OCCURRENCES = 3
MAX_SLICES = 31

RECURRENT_COLUMNS: list[tuple[str, str, str]] = [
    ("occurrences", "Ocorrências", "number"),
    ("worstConnectedScore", "Pior conectividade", "score"),
    ("macAddress", "MAC", "text"),
    ("name", "Nome", "text"),
    ("username", "Usuário", "text"),
    ("type", "Tipo", "text"),
    ("siteHierarchy", "Localidade", "text"),
    ("otherLocations", "Outras localidades", "text"),
    ("ssid", "SSID", "text"),
    ("connectedNetworkDeviceName", "Dispositivo conectado", "text"),
    ("worstRssi", "Pior RSSI (dBm)", "number"),
    ("worstSnr", "Pior SNR (dB)", "number"),
    ("overallScore", "Pior saúde", "score"),
    ("firstSeen", "Primeira ocorrência", "text"),
    ("lastSeen", "Última ocorrência", "text"),
    ("intervals", "Intervalos", "text"),
]


class TimeWindow:
    def __init__(self, start: datetime, end: datetime) -> None:
        self.start = start
        self.end = end

    def label(self) -> str:
        if self.start.date() == self.end.date():
            return f"{self.start.strftime('%d/%m/%Y %H:%M')}–{self.end.strftime('%H:%M')}"
        return (
            f"{self.start.strftime('%d/%m/%Y %H:%M')}–"
            f"{self.end.strftime('%d/%m/%Y %H:%M')}"
        )


def is_low_connection(score: Any, *, low: int = 1, high: int = LOW_CONNECTION_MAX) -> bool:
    """Score de conectividade dentro da faixa escolhida. Zero é inativo."""
    if isinstance(score, bool) or score is None or score == "":
        return False
    try:
        number = float(score)
    except (TypeError, ValueError):
        return False
    start = min(low, high)
    end = max(low, high)
    return start <= number <= end


def period_from_choice(choice: str, now: datetime) -> tuple[datetime, datetime]:
    """1 hora, 3 horas, 6 horas, 12 horas ou 24 horas. Enter usa 24 horas."""
    value = choice.strip() or "5"
    hours = {"1": 1, "2": 3, "3": 6, "4": 12, "5": 24}.get(value)
    if hours is None:
        raise ValueError("Escolha 1, 2, 3, 4 ou 5.")
    return now - timedelta(hours=hours), now


def parse_user_datetime(raw: str, *, end_of_day: bool = False) -> datetime:
    text = raw.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%d/%m/%Y" and end_of_day:
            return parsed.replace(hour=23, minute=59, second=59)
        return parsed
    raise ValueError("Use dd/mm/aaaa ou dd/mm/aaaa hh:mm.")


def parse_minimum_occurrences(raw: str) -> int:
    text = raw.strip()
    if not text:
        return DEFAULT_MINIMUM_OCCURRENCES
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError("Informe um número inteiro maior que zero.") from exc
    if value < 1:
        raise ValueError("O mínimo de ocorrências precisa ser pelo menos 1.")
    return value


def build_slices(start: datetime, end: datetime, *, max_slices: int = MAX_SLICES) -> list[TimeWindow]:
    """Divide o período para enxergar a mesma conexão baixa mais de uma vez.

    Até 1 hora usa fatias de 12 minutos; até 3 horas, de 30 minutos; até 6 horas,
    de 1 hora; até 12 horas, de 2 horas; até 24 horas, de 4 horas. Períodos
    longos são agrupados para não passar de ``max_slices``.
    """
    if end <= start:
        raise ValueError("O fim do período precisa ser depois do início.")
    duration = end - start
    if duration <= timedelta(hours=1):
        step = timedelta(minutes=12)
    elif duration <= timedelta(hours=3):
        step = timedelta(minutes=30)
    elif duration <= timedelta(hours=6):
        step = timedelta(hours=1)
    elif duration <= timedelta(hours=12):
        step = timedelta(hours=2)
    elif duration <= timedelta(hours=24):
        step = timedelta(hours=4)
    elif duration <= timedelta(days=3):
        step = timedelta(hours=12)
    else:
        step = timedelta(days=1)
    count = math.ceil(duration / step)
    if count > max_slices:
        step = duration / max_slices
    windows: list[TimeWindow] = []
    cursor = start
    while cursor < end:
        nxt = cursor + step
        if nxt >= end or (end - nxt) < timedelta(seconds=1):
            nxt = end
        windows.append(TimeWindow(cursor, nxt))
        cursor = nxt
    return windows


def aggregate_recurring(
    observations: list[dict[str, Any]],
    *,
    minimum: int = DEFAULT_MINIMUM_OCCURRENCES,
    connection_low: int = 1,
    connection_high: int = LOW_CONNECTION_MAX,
) -> list[dict[str, Any]]:
    """Agrupa por MAC e mantém quem teve conexão na faixa escolhida no mínimo de intervalos."""
    if minimum < 1:
        raise ValueError("O mínimo de ocorrências precisa ser pelo menos 1.")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        if not is_low_connection(
            observation.get("connectedScore"),
            low=connection_low,
            high=connection_high,
        ):
            continue
        mac = str(observation.get("macAddress") or "").strip().upper()
        if not mac:
            continue
        grouped.setdefault(mac, []).append(observation)

    rows: list[dict[str, Any]] = []
    for mac, items in grouped.items():
        if len(items) < minimum:
            continue
        latest = max(items, key=lambda item: item.get("sliceEnd") or datetime.min)
        locations = _locations_by_frequency(items)
        scores = [_number(item.get("connectedScore")) for item in items]
        scores = [score for score in scores if score is not None]
        overall = [
            score
            for score in (_number(item.get("overallScore")) for item in items)
            if score is not None
        ]
        rssi = [
            score for score in (_number(item.get("rssi")) for item in items) if score is not None
        ]
        snr = [
            score for score in (_number(item.get("snr")) for item in items) if score is not None
        ]
        row: dict[str, Any] = {
            "occurrences": len(items),
            "worstConnectedScore": min(scores) if scores else None,
            "macAddress": mac,
            "name": latest.get("name"),
            "username": latest.get("username"),
            "type": latest.get("type"),
            "siteHierarchy": locations[0] if locations else None,
            "otherLocations": " | ".join(locations[1:]) if len(locations) > 1 else None,
            "ssid": latest.get("ssid"),
            "connectedNetworkDeviceName": latest.get("connectedNetworkDeviceName"),
            "worstRssi": min(rssi) if rssi else None,
            "worstSnr": min(snr) if snr else None,
            "overallScore": min(overall) if overall else None,
            "firstSeen": _format_edge(items, earliest=True),
            "lastSeen": _format_edge(items, earliest=False),
            "intervals": "; ".join(
                str(item.get("sliceLabel"))
                for item in sorted(items, key=lambda item: item.get("sliceStart") or datetime.min)
                if item.get("sliceLabel")
            ),
        }
        rows.append({key: value for key, value in row.items() if value is not None})
    rows.sort(
        key=lambda item: (
            -int(item.get("occurrences") or 0),
            _number(item.get("worstConnectedScore")) or 0,
            str(item.get("siteHierarchy") or ""),
            str(item.get("macAddress") or ""),
        )
    )
    return rows


def recurrent_columns(rows: list[dict[str, Any]]) -> list[Column]:
    from catalyst_center.health import select_columns

    return select_columns(rows, RECURRENT_COLUMNS)


def describe_slices(windows: list[TimeWindow]) -> str:
    if not windows:
        return "nenhum intervalo"
    if len(windows) == 1:
        return f"1 intervalo ({windows[0].label()})"
    minutes = (windows[0].end - windows[0].start).total_seconds() / 60
    if abs(minutes - 12) < 1:
        size = "12 minutos"
    elif abs(minutes - 30) < 1:
        size = "30 minutos"
    elif 55 <= minutes <= 65:
        size = "1 hora"
    elif 115 <= minutes <= 125:
        size = "2 horas"
    elif 235 <= minutes <= 245:
        size = "4 horas"
    elif 23 * 60 <= minutes <= 25 * 60:
        size = "1 dia"
    elif 11 * 60 <= minutes <= 13 * 60:
        size = "12 horas"
    elif minutes >= 24 * 60 and abs(minutes % (24 * 60)) < 12:
        days = int(round(minutes / (24 * 60)))
        size = f"{days} dias"
    else:
        size = f"{minutes / 60:.1f} horas".replace(".", ",")
    return f"{len(windows)} intervalos de {size}"


def _locations_by_frequency(items: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    order: list[str] = []
    for item in items:
        location = str(item.get("siteHierarchy") or "").strip()
        if not location:
            continue
        if location not in counts:
            order.append(location)
        counts[location] = counts.get(location, 0) + 1
    return sorted(order, key=lambda name: (-counts[name], order.index(name)))


def _format_edge(items: list[dict[str, Any]], *, earliest: bool) -> str | None:
    moments = [item.get("sliceStart") if earliest else item.get("sliceEnd") for item in items]
    moments = [moment for moment in moments if isinstance(moment, datetime)]
    if not moments:
        return None
    moment = min(moments) if earliest else max(moments)
    return moment.strftime("%d/%m/%Y %H:%M")


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
