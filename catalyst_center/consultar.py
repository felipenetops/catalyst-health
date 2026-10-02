"""Consulta manual de Assurance › Health › Client e gera CSV e dashboard."""

from __future__ import annotations

import csv
import sys
import time
import webbrowser
from collections.abc import Callable
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import Any

from catalyst_center.client import (
    APIError,
    AuthenticationError,
    CatalystCenterClient,
    CatalystCenterError,
)
from catalyst_center.dashboard import render_dashboard
from catalyst_center.sites import matching_site_hierarchies, site_ids_under
from catalyst_center.health import (
    CLIENT_COLUMNS,
    CLIENT_LOCATION_FIELDS,
    SITE_COLUMNS,
    SITE_LOCATION_FIELDS,
    Column,
    filter_by_location,
    filter_by_ssid,
    normalize_client,
    normalize_site,
    select_columns,
)
from catalyst_center.recurrence import (
    aggregate_recurring,
    build_slices,
    describe_slices,
    is_low_connection,
    parse_minimum_occurrences,
    period_from_choice,
    recurrent_columns,
)

DEFAULT_HOST = "https://dnac.boticario.net"
REPORT_DIR = Path(__file__).resolve().parent.parent / "relatorios"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"--pagina", "--web"}:
        from catalyst_center.web import serve

        return serve()
    print("Catalyst Center — Assurance / Health / Client")
    print("A consulta pede usuário e senha e não grava essas credenciais.")
    print(f"Host padrão: {DEFAULT_HOST}")
    try:
        host = _prompt_host()
        username, password = _prompt_credentials()
        client_type = _prompt_client_type()
        ssids = _prompt_ssids() if client_type != "Wired" else ()
        location = input(
            "Localidade (parte do nome ou da hierarquia, Enter = todas): "
        ).strip()
        if not location and not _ask_yes(
            "Consultar todas as localidades pode demorar. Continuar? [s/N]: "
        ):
            print("Consulta cancelada.")
            return 0
        period_start, period_end = _prompt_period()
        recurring, minimum_occurrences = _prompt_query_mode()
        _silence_tls_warnings()
        while True:
            client = CatalystCenterClient(
                host,
                username,
                password,
                verify_ssl=False,
                timeout=120,
            )
            try:
                print("Autenticando...")
                client.authenticate()
                break
            except AuthenticationError as exc:
                print(exc)
                if not _ask_yes("Tentar outras credenciais? [s/N]: "):
                    return 1
                username, password = _prompt_credentials()
            except CatalystCenterError as exc:
                print(exc)
                return 1
        notes: list[str] = []
        try:
            mode, rows, columns = fetch_client_health(
                client,
                location=location,
                client_type=client_type,
                ssids=ssids,
                period_start=period_start,
                period_end=period_end,
                recurring=recurring,
                minimum_occurrences=minimum_occurrences,
                notes=notes,
            )
        except CatalystCenterError as exc:
            print(exc)
            return 1
    except KeyboardInterrupt:
        print("\nConsulta interrompida.")
        return 130
    except EOFError:
        print("Entrada encerrada antes de informar os dados.", file=sys.stderr)
        return 2

    csv_path, html_path = write_reports(
        mode=mode,
        rows=rows,
        columns=columns,
        host=host,
        location=location,
        client_type=client_type,
        ssids=ssids,
        period_label=_period_label(period_start, period_end),
        minimum_occurrences=minimum_occurrences if recurring else None,
        note=" ".join(notes),
    )
    print(f"{len(rows)} registro(s).")
    print(f"CSV: {csv_path}")
    print(f"Dashboard: {html_path}")
    open_dashboard(html_path)
    return 0


def run_consultation(
    *,
    host: str,
    username: str,
    password: str,
    client_type: str | None,
    ssids: tuple[str, ...],
    location: str,
    period_start: datetime,
    period_end: datetime,
    recurring: bool,
    minimum_occurrences: int = 3,
    connection_low: int = 1,
    connection_high: int = 7,
    verify_ssl: bool = False,
) -> tuple[Path, Path, int]:
    """Autentica, consulta e grava CSV e dashboard. A senha não entra nos arquivos."""
    if period_end <= period_start:
        raise CatalystCenterError("O fim do período precisa ser depois do início.")
    if not verify_ssl:
        _silence_tls_warnings()
    client = CatalystCenterClient(
        host,
        username,
        password,
        verify_ssl=verify_ssl,
        timeout=120,
    )
    client.authenticate()
    notes: list[str] = []
    mode, rows, columns = fetch_client_health(
        client,
        location=location,
        client_type=client_type,
        ssids=ssids,
        period_start=period_start,
        period_end=period_end,
        recurring=recurring,
        minimum_occurrences=minimum_occurrences,
        connection_low=connection_low,
        connection_high=connection_high,
        notes=notes,
    )
    csv_path, html_path = write_reports(
        mode=mode,
        rows=rows,
        columns=columns,
        host=host,
        location=location,
        client_type=client_type,
        ssids=ssids,
        period_label=_period_label(period_start, period_end),
        minimum_occurrences=minimum_occurrences if recurring else None,
        connection_low=connection_low,
        connection_high=connection_high,
        note=" ".join(notes),
    )
    return csv_path, html_path, len(rows)


def fetch_client_health(
    client: CatalystCenterClient,
    *,
    location: str,
    client_type: str | None,
    ssids: tuple[str, ...] = (),
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    recurring: bool = False,
    minimum_occurrences: int = 3,
    connection_low: int = 1,
    connection_high: int = 7,
    notes: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]], list[Column]]:
    """Busca os clientes da localidade. Se a API nova não existir, usa site-health."""
    if recurring:
        if period_start is None or period_end is None:
            raise CatalystCenterError("Informe o período para consultar reincidentes.")
        return _fetch_recurring(
            client,
            location=location,
            client_type=client_type,
            ssids=ssids,
            period_start=period_start,
            period_end=period_end,
            minimum_occurrences=minimum_occurrences,
            connection_low=connection_low,
            connection_high=connection_high,
            notes=notes,
        )

    print("Consultando Assurance / Health / Client...")
    rows = _load_clients(
        client,
        location=location,
        client_type=client_type,
        ssids=ssids,
        start=period_start,
        end=period_end,
        allow_site_fallback=True,
        notes=notes,
    )
    if rows is None:
        if ssids:
            raise CatalystCenterError(
                "A lista de clientes não está disponível nesta versão, "
                "então não dá para filtrar por SSID."
            )
        return _fetch_sites(client, location, _print_progress, timestamp=_epoch_ms(period_end))
    return "clients", rows, select_columns(rows, CLIENT_COLUMNS)


def _fetch_recurring(
    client: CatalystCenterClient,
    *,
    location: str,
    client_type: str | None,
    ssids: tuple[str, ...],
    period_start: datetime,
    period_end: datetime,
    minimum_occurrences: int,
    connection_low: int = 1,
    connection_high: int = 7,
    notes: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]], list[Column]]:
    scope = _resolve_location(client, location)
    windows = build_slices(period_start, period_end)
    print(
        "Consultando clientes reincidentes com conexão "
        f"de {min(connection_low, connection_high)} a {max(connection_low, connection_high)} "
        f"({describe_slices(windows)})."
    )
    print("Score 0 fica de fora.")
    observations: list[dict[str, Any]] = []
    for index, window in enumerate(windows, start=1):
        print(f"Intervalo {index}/{len(windows)}: {window.label()}", flush=True)
        rows = _load_clients(
            client,
            location=location,
            client_type=client_type,
            ssids=ssids,
            start=window.start,
            end=window.end,
            allow_site_fallback=False,
            notes=notes,
            scope=scope,
        )
        if rows is None:
            raise CatalystCenterError(
                "A lista individual de clientes não está disponível nesta versão, "
                "então não dá para identificar reincidentes."
            )
        seen: set[str] = set()
        low_in_window = 0
        for row in rows:
            mac = str(row.get("macAddress") or "").strip().upper()
            if not mac or mac in seen or not is_low_connection(
                row.get("connectedScore"),
                low=connection_low,
                high=connection_high,
            ):
                continue
            seen.add(mac)
            low_in_window += 1
            observations.append(
                {
                    **row,
                    "macAddress": mac,
                    "sliceLabel": window.label(),
                    "sliceStart": window.start,
                    "sliceEnd": window.end,
                }
            )
        print(f"  {low_in_window} cliente(s) com conexão baixa neste intervalo.", flush=True)
    rows = aggregate_recurring(
        observations,
        minimum=minimum_occurrences,
        connection_low=connection_low,
        connection_high=connection_high,
    )
    _print_location_summary(rows)
    return "recurring", rows, recurrent_columns(rows)


def _load_clients(
    client: CatalystCenterClient,
    *,
    location: str,
    client_type: str | None,
    ssids: tuple[str, ...],
    start: datetime | None,
    end: datetime | None,
    allow_site_fallback: bool,
    notes: list[str] | None = None,
    scope: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
) -> list[dict[str, Any]] | None:
    """Devolve as linhas normalizadas. None pede o fallback de site-health."""
    hierarchies, site_ids = scope if scope is not None else _resolve_location(client, location)
    attempts: list[tuple[str | None, list[str] | None, bool, bool]] = []
    for hierarchy in hierarchies or (None,):
        if ssids and client_type == "Wireless":
            attempts.append((hierarchy, None, True, True))
        attempts.append((hierarchy, None, False, True))
        attempts.append((hierarchy, None, False, False))
    if site_ids:
        attempts.append((None, list(site_ids), False, True))

    found: list[dict[str, Any]] | None = None
    announced_ssid = False
    announced_type = False
    for hierarchy, identifiers, use_ssid, use_type in attempts:
        if identifiers:
            raw_rows: list[dict[str, Any]] = []
            missing_api = False
            for chunk in _chunks(identifiers, 25):
                part = _fetch_client_page(
                    client,
                    client_type=client_type if use_type else None,
                    ssids=ssids if use_ssid else (),
                    site_hierarchy=None,
                    site_ids=chunk,
                    view="Wireless" if client_type == "Wireless" else None,
                    start=start,
                    end=end,
                    allow_site_fallback=allow_site_fallback,
                )
                if part is None:
                    missing_api = True
                    break
                raw_rows.extend(part)
            if missing_api:
                return None
            rows = _dedupe_clients(raw_rows)
        else:
            rows = _fetch_client_page(
                client,
                client_type=client_type if use_type else None,
                ssids=ssids if use_ssid else (),
                site_hierarchy=hierarchy,
                site_ids=None,
                view="Wireless" if client_type == "Wireless" else None,
                start=start,
                end=end,
                allow_site_fallback=allow_site_fallback,
            )
            if rows is None:
                return None
        scoped = _scope_rows(rows, location, client_type)
        if scoped:
            if ssids and not use_ssid and not announced_ssid:
                _note(
                    notes,
                    "A consulta direta pelo SSID não trouxe clientes. "
                    "O filtro foi aplicado nos clientes da localidade.",
                )
                announced_ssid = True
            if client_type and not use_type and not announced_type:
                _note(notes, "O tipo de cliente foi filtrado localmente.")
                announced_type = True
            found = scoped
            break
        found = []
    rows = _apply_client_filters(
        found or [],
        location=location,
        client_type=client_type,
        ssids=ssids,
        notes=notes,
    )
    return rows


def _fetch_client_page(
    client: CatalystCenterClient,
    *,
    client_type: str | None,
    ssids: tuple[str, ...],
    site_hierarchy: str | None,
    site_ids: list[str] | None,
    view: str | None,
    start: datetime | None,
    end: datetime | None,
    allow_site_fallback: bool,
) -> list[dict[str, Any]] | None:
    target = site_hierarchy or ("sites da localidade" if site_ids else "todas as localidades")
    print(f"Consultando {target}...", flush=True)
    ssid_param: str | list[str] | None = None
    if ssids:
        ssid_param = ssids[0] if len(ssids) == 1 else list(ssids)
    try:
        raw_clients = _list_clients_with_retry(
            client,
            site_hierarchy=None if site_ids else site_hierarchy,
            site_id=site_ids,
            client_type=client_type,
            ssid=ssid_param,
            view=view,
            start_time=_epoch_ms(start),
            end_time=_epoch_ms(end),
            on_page=_print_progress,
        )
    except APIError as exc:
        if exc.status_code == 404 and allow_site_fallback:
            print(
                "A lista individual de clientes não está disponível nesta versão. "
                "Consultando a saúde de clientes por localidade..."
            )
            return None
        if exc.status_code == 404:
            return None
        raise
    return [normalize_client(item) for item in raw_clients if isinstance(item, dict)]


def _resolve_location(
    client: CatalystCenterClient,
    location: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not location:
        return (), ()
    if not hasattr(client, "list_sites"):
        return (location,), ()
    try:
        sites = client.list_sites()
    except CatalystCenterError as exc:
        print(f"Não foi possível listar as localidades ({exc}). Usando o texto informado.")
        return (location,), ()
    hierarchies = matching_site_hierarchies(sites, location)
    if not hierarchies:
        print(
            f"Nenhuma localidade cadastrada contém {location!r}. "
            "A consulta usa esse texto na API."
        )
        return (location,), ()
    print("Localidade: " + "; ".join(hierarchies), flush=True)
    return tuple(hierarchies), tuple(site_ids_under(sites, hierarchies))


def _scope_rows(
    rows: list[dict[str, Any]],
    location: str,
    client_type: str | None,
) -> list[dict[str, Any]]:
    scoped = filter_by_location(rows, location, CLIENT_LOCATION_FIELDS)
    if not client_type:
        return scoped
    wanted = client_type.casefold()
    return [
        row
        for row in scoped
        if str(row.get("type") or client_type).casefold() == wanted
    ]


def _apply_client_filters(
    rows: list[dict[str, Any]],
    *,
    location: str,
    client_type: str | None,
    ssids: tuple[str, ...],
    notes: list[str] | None,
) -> list[dict[str, Any]]:
    scoped = _scope_rows(rows, location, client_type)
    if not scoped:
        if rows:
            _note(notes, "Nenhum cliente ficou na localidade informada.")
        else:
            _note(notes, "A API não devolveu clientes para essa localidade.")
        return []
    if not ssids:
        return scoped
    if not any(str(row.get("ssid") or "").strip() for row in scoped):
        _note(
            notes,
            "A API não informou o SSID dos clientes, então o filtro de rede não pôde ser conferido.",
        )
    filtered = filter_by_ssid(scoped, ssids, keep_wired=client_type != "Wireless")
    if scoped and not filtered:
        found = sorted(
            {str(row.get("ssid")) for row in scoped if row.get("ssid")},
            key=str.casefold,
        )
        if found:
            preview = ", ".join(found[:12])
            if len(found) > 12:
                preview += "..."
            _note(
                notes,
                f"Nenhum cliente no SSID {', '.join(ssids)}. "
                f"SSIDs nessa localidade: {preview}",
            )
        else:
            _note(notes, f"Nenhum cliente no SSID {', '.join(ssids)}.")
    return filtered


def _dedupe_clients(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("macAddress") or "").upper()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(row)
    return unique


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _note(notes: list[str] | None, message: str) -> None:
    if notes is not None and message in notes:
        return
    print(message, flush=True)
    if notes is not None:
        notes.append(message)


def _list_clients_with_retry(client: CatalystCenterClient, **kwargs: Any) -> list[dict[str, Any]]:
    try:
        return client.list_assurance_clients(**kwargs)
    except APIError as exc:
        if exc.status_code == 429:
            print("A API limitou as consultas. Aguardando 60 segundos...", flush=True)
            time.sleep(60)
            return client.list_assurance_clients(**kwargs)
        if exc.status_code == 400 and kwargs.get("view"):
            print("Esta versão não aceitou o detalhe da rede sem fio. Consultando sem esse parâmetro.")
            without_view = {key: value for key, value in kwargs.items() if key != "view"}
            return client.list_assurance_clients(**without_view)
        raise


def _print_progress(total: int) -> None:
    print(f"  {total} registro(s)...", flush=True)


def _print_location_summary(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("Nenhum cliente reincidente no período.", flush=True)
        return
    counts: dict[str, int] = {}
    for row in rows:
        place = str(row.get("siteHierarchy") or "Sem localidade")
        counts[place] = counts.get(place, 0) + 1
    print("Reincidentes por localidade:", flush=True)
    for place, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {place}: {count}", flush=True)


def _epoch_ms(moment: datetime | None) -> int | None:
    if moment is None:
        return None
    return int(moment.timestamp() * 1000)


def _fetch_sites(
    client: CatalystCenterClient,
    location: str,
    progress: Callable[[int], None],
    timestamp: int | None = None,
) -> tuple[str, list[dict[str, Any]], list[Column]]:
    raw_sites = client.list_client_health_by_location(timestamp=timestamp, on_page=progress)
    rows = filter_by_location(
        [normalize_site(item) for item in raw_sites if isinstance(item, dict)],
        location,
        SITE_LOCATION_FIELDS,
    )
    return "sites", rows, select_columns(rows, SITE_COLUMNS)


def write_reports(
    *,
    mode: str,
    rows: list[dict[str, Any]],
    columns: list[Column],
    host: str,
    location: str,
    client_type: str | None,
    ssids: tuple[str, ...] = (),
    output_dir: Path = REPORT_DIR,
    now: datetime | None = None,
    period_label: str | None = None,
    minimum_occurrences: int | None = None,
    connection_low: int = 1,
    connection_high: int = 7,
    note: str = "",
) -> tuple[Path, Path]:
    moment = now or datetime.now()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    prefix = "clientes-reincidentes" if mode == "recurring" else "clientes-saude"
    stem = f"{prefix}-{stamp}"
    csv_path = output_dir / f"{stem}.csv"
    html_path = output_dir / f"{stem}.html"
    write_csv(csv_path, columns, rows)
    title = "Assurance — Saúde dos clientes"
    if mode == "sites":
        title = "Assurance — Saúde dos clientes por localidade"
    elif mode == "recurring":
        title = "Clientes reincidentes com conexão baixa"
    subtitle = _subtitle(
        host=host,
        location=location,
        client_type=client_type,
        moment=moment,
        csv_path=csv_path,
        period_label=period_label,
        recurring=mode == "recurring",
        minimum_occurrences=minimum_occurrences,
        connection_low=connection_low,
        connection_high=connection_high,
        ssids=ssids,
        note=note,
    )
    html_path.write_text(
        render_dashboard(title=title, subtitle=subtitle, columns=columns, rows=rows),
        encoding="utf-8",
    )
    return csv_path, html_path


def write_csv(path: Path, columns: list[Column], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([column["label"] for column in columns])
        for row in rows:
            writer.writerow([_csv_value(row.get(column["key"])) for column in columns])


def open_dashboard(path: Path) -> None:
    try:
        webbrowser.open(path.resolve().as_uri())
    except webbrowser.Error:
        print("Não foi possível abrir o navegador. Abra o arquivo HTML manualmente.")


def _subtitle(
    *,
    host: str,
    location: str,
    client_type: str | None,
    moment: datetime,
    csv_path: Path,
    period_label: str | None = None,
    recurring: bool = False,
    minimum_occurrences: int | None = None,
    connection_low: int = 1,
    connection_high: int = 7,
    ssids: tuple[str, ...] = (),
    note: str = "",
) -> str:
    kind = {"Wired": "cabeado", "Wireless": "sem fio"}.get(client_type or "", "todos os tipos")
    place = location or "todas as localidades"
    when = period_label or moment.strftime("%d/%m/%Y %H:%M")
    network = f" · SSID {', '.join(ssids)}" if ssids else ""
    focus = ""
    if recurring:
        low = min(connection_low, connection_high)
        high = max(connection_low, connection_high)
        times = minimum_occurrences if minimum_occurrences is not None else 3
        focus = f" · conexão ({low}–{high}), mínimo {times} ocorrências"
    detail = f" · {note}" if note else ""
    return f"{place} · {kind}{network}{focus} · {when}{detail} · {host} · CSV: {csv_path}"


def _prompt_period(
    input_fn: Callable[[str], str] = input,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    clock = (now or datetime.now()).replace(microsecond=0)
    print("Período da consulta:")
    print("  1) 1 hora")
    print("  2) 3 horas")
    print("  3) 6 horas")
    print("  4) 12 horas")
    print("  5) 24 horas")
    while True:
        try:
            start, end = period_from_choice(input_fn("Opção [5]: "), clock)
        except ValueError as exc:
            print(exc)
            continue
        print(f"Período: {_period_label(start, end)}")
        return start, end


def _prompt_query_mode(
    input_fn: Callable[[str], str] = input,
) -> tuple[bool, int]:
    print("O que consultar?")
    print("  1) Todos os clientes do período")
    print("  2) Reincidentes com saúde da conexão baixa, por localidade")
    while True:
        choice = input_fn("Opção [1]: ").strip() or "1"
        if choice == "1":
            return False, 3
        if choice == "2":
            print(
                "Reincidente: o mesmo cliente com conectividade de 1 a 7 "
                "em intervalos diferentes do período."
            )
            while True:
                try:
                    minimum = parse_minimum_occurrences(
                        input_fn("Mínimo de ocorrências no período [3]: ")
                    )
                except ValueError as exc:
                    print(exc)
                    continue
                return True, minimum
        print("Escolha 1 ou 2.")


def _period_label(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%d/%m/%Y %H:%M')} – {end.strftime('%d/%m/%Y %H:%M')}"


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _prompt_host(input_fn: Callable[[str], str] = input) -> str:
    typed = input_fn(f"Host [{DEFAULT_HOST}]: ").strip()
    return typed or DEFAULT_HOST


def _prompt_credentials(
    input_fn: Callable[[str], str] = input,
    password_fn: Callable[[str], str] = getpass,
) -> tuple[str, str]:
    while True:
        username = input_fn("Usuário: ").strip()
        password = password_fn("Senha: ")
        if username and password:
            return username, password
        print("Informe usuário e senha.")


def _prompt_client_type(input_fn: Callable[[str], str] = input) -> str | None:
    while True:
        typed = input_fn("Tipo de cliente [todos/cabeado/sem fio] (Enter = todos): ")
        try:
            return _parse_client_type(typed)
        except ValueError as exc:
            print(exc)


def _prompt_ssids(input_fn: Callable[[str], str] = input) -> tuple[str, ...]:
    while True:
        raw = input_fn("SSID da rede sem fio (vários separados por vírgula): ")
        names = tuple(part.strip() for part in raw.split(",") if part.strip())
        if names:
            return names
        print("Informe ao menos um SSID para a rede sem fio.")


def _parse_client_type(raw: str) -> str | None:
    value = raw.strip().casefold()
    if value in {"", "todos", "todas", "all"}:
        return None
    if value in {"cabeado", "cabeados", "wired"}:
        return "Wired"
    if value in {"sem fio", "wireless", "wifi", "wi-fi"}:
        return "Wireless"
    raise ValueError("Use todos, cabeado ou sem fio.")


def _ask_yes(prompt: str, input_fn: Callable[[str], str] = input) -> bool:
    answer = input_fn(prompt).strip().casefold()
    return answer in {"s", "sim", "y", "yes"}


def _silence_tls_warnings() -> None:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
