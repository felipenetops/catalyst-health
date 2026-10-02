"""Linha de comando para as consultas mais usadas no Catalyst Center."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from catalyst_center.client import CatalystCenterClient, CatalystCenterError
from catalyst_center.config import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catalyst",
        description="Consulta a API Intent do Cisco Catalyst Center.",
    )
    parser.add_argument("--host", help="Host ou URL. Padrão: CATALYST_CENTER_HOST")
    parser.add_argument("--username", help="Usuário. Padrão: CATALYST_CENTER_USERNAME")
    parser.add_argument("--password", help="Senha. Padrão: CATALYST_CENTER_PASSWORD")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Não verifica o certificado TLS. Use só com certificado autoassinado.",
    )
    parser.add_argument("--timeout", type=float, help="Tempo limite HTTP em segundos")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Imprime o JSON em uma linha",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    devices = subparsers.add_parser("devices", help="Lista dispositivos de rede")
    devices.add_argument("--hostname", help="Filtro parcial de hostname")
    devices.add_argument("--ip", dest="managementIpAddress", help="IP de gerência")
    devices.add_argument("--family", help="Família, por exemplo Switches and Hubs")
    devices.add_argument("--id", dest="device_id", help="Consulta um dispositivo pelo id")

    subparsers.add_parser("device-count", help="Conta dispositivos de rede")

    sites = subparsers.add_parser("sites", help="Lista sites")
    sites.add_argument("--name", help="Filtro de nome do site")

    network = subparsers.add_parser("network-health", help="Saúde da rede")
    network.add_argument("--timestamp", type=int, help="Epoch em milissegundos")

    clients = subparsers.add_parser("client-health", help="Saúde dos clientes")
    clients.add_argument("--timestamp", type=int, help="Epoch em milissegundos")

    device_health = subparsers.add_parser("device-health", help="Saúde dos dispositivos")
    device_health.add_argument("--site-id", dest="siteId")
    device_health.add_argument("--device-role", dest="deviceRole")
    device_health.add_argument("--health", help="healthy, warning, critical ou unknown")

    issues = subparsers.add_parser("issues", help="Lista issues das últimas 24 horas, ou do intervalo informado")
    issues.add_argument("--start-time", type=int, dest="start_time", help="Início em epoch milissegundos")
    issues.add_argument("--end-time", type=int, dest="end_time", help="Fim em epoch milissegundos")
    issues.add_argument("--site-id", dest="siteId")
    issues.add_argument("--device-id", dest="deviceId")
    issues.add_argument("--priority")

    interfaces = subparsers.add_parser("interfaces", help="Lista interfaces")
    interfaces.add_argument("--device-id", dest="device_id")

    raw = subparsers.add_parser("get", help="GET arbitrário em um path da API")
    raw.add_argument("path", help="Ex.: dna/intent/api/v1/network-device")
    raw.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="CHAVE=VALOR",
        help="Query param. Pode repetir a opção.",
    )
    raw.add_argument(
        "--all-pages",
        action="store_true",
        help="Percorre limit/offset e devolve a lista completa",
    )
    return parser


def execute(client: CatalystCenterClient, args: argparse.Namespace) -> Any:
    if args.command == "devices":
        if args.device_id:
            return client.get_device(args.device_id)
        return client.list_devices(
            hostname=args.hostname,
            managementIpAddress=args.managementIpAddress,
            family=args.family,
        )
    if args.command == "device-count":
        return {"count": client.count_devices()}
    if args.command == "sites":
        return client.list_sites(name=args.name)
    if args.command == "network-health":
        return client.network_health(timestamp=args.timestamp)
    if args.command == "client-health":
        return client.client_health(timestamp=args.timestamp)
    if args.command == "device-health":
        return client.device_health(
            siteId=args.siteId,
            deviceRole=args.deviceRole,
            health=args.health,
        )
    if args.command == "issues":
        return client.list_issues(
            start_time=args.start_time,
            end_time=args.end_time,
            siteId=args.siteId,
            deviceId=args.deviceId,
            priority=args.priority,
        )
    if args.command == "interfaces":
        return client.list_interfaces(device_id=args.device_id)
    if args.command == "get":
        params = _parse_params(args.param)
        if args.all_pages:
            return client.get_all(args.path, params)
        return client.get(args.path, params)
    raise CatalystCenterError(f"Comando desconhecido: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(
        host=args.host,
        username=args.username,
        password=args.password,
        verify_ssl=False if args.insecure else None,
        timeout=args.timeout,
    )
    missing = settings.missing_fields()
    if missing:
        print(
            "Configuração incompleta. Defina: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2
    client = CatalystCenterClient(
        settings.host,
        settings.username,
        settings.password,
        verify_ssl=settings.verify_ssl,
        timeout=settings.timeout,
    )
    try:
        result = execute(client, args)
    except CatalystCenterError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.compact:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _parse_params(items: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise CatalystCenterError(f"Parâmetro inválido {item!r}. Use CHAVE=VALOR")
        params[key] = value
    return params
