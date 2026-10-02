"""Cliente HTTP da API Intent do Cisco Catalyst Center."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

import requests

# O token da plataforma costuma valer 60 minutos. Renova um pouco antes.
TOKEN_TTL_SECONDS = 50 * 60
DEFAULT_PAGE_SIZE = 500
# A API de site-health rejeita limit acima de 50.
SITE_HEALTH_PAGE_SIZE = 50


class CatalystCenterError(Exception):
    """Erro ao consultar o Catalyst Center."""


class AuthenticationError(CatalystCenterError):
    """Falha ao obter ou renovar o token de API."""


class APIError(CatalystCenterError):
    """A API respondeu com um status de erro."""

    def __init__(self, message: str, status_code: int, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class CatalystCenterClient:
    """Executa consultas autenticadas na API Intent.

    O cliente obtém o token em ``POST /dna/system/api/v1/auth/token`` e envia
    ``X-Auth-Token`` nas chamadas seguintes. Um HTTP 401 dispara uma nova
    autenticação e uma única repetição da consulta.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = True,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        if not host or not str(host).strip():
            raise ValueError("host é obrigatório")
        normalized = str(host).strip()
        if not normalized.startswith(("http://", "https://")):
            normalized = f"https://{normalized}"
        self.base_url = normalized.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = session or requests.Session()
        self._token: str | None = None
        self._token_obtained_at = 0.0

        if not verify_ssl:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def authenticate(self) -> str:
        if not self.username or not self.password:
            raise AuthenticationError(
                "Usuário e senha são obrigatórios para autenticar no Catalyst Center"
            )
        url = urljoin(self.base_url, "dna/system/api/v1/auth/token")
        try:
            response = self.session.post(
                url,
                auth=(self.username, self.password),
                headers={"Content-Type": "application/json"},
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CatalystCenterError(f"Falha de rede ao autenticar: {exc}") from exc

        if response.status_code == 401:
            raise AuthenticationError("Usuário ou senha recusados pelo Catalyst Center")
        if not response.ok:
            raise APIError(
                f"Falha ao obter token ({response.status_code})",
                response.status_code,
                _safe_body(response),
            )
        payload = _json_or_raise(response)
        token = payload.get("Token") if isinstance(payload, dict) else None
        if not token:
            raise AuthenticationError("A resposta de autenticação não trouxe o campo Token")
        self._token = str(token)
        self._token_obtained_at = time.monotonic()
        return self._token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        retry_auth: bool = True,
    ) -> Any:
        token = self._ensure_token()
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.session.request(
                method.upper(),
                url,
                params=_clean_params(params),
                json=json,
                headers={
                    "X-Auth-Token": token,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                verify=self.verify_ssl,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CatalystCenterError(f"Falha de rede em {method.upper()} {path}: {exc}") from exc

        if response.status_code == 401 and retry_auth:
            self._token = None
            return self.request(method, path, params=params, json=json, retry_auth=False)
        if response.status_code == 401:
            raise AuthenticationError("Token recusado pelo Catalyst Center")
        if not response.ok:
            raise APIError(
                f"{method.upper()} {path} retornou {response.status_code}",
                response.status_code,
                _safe_body(response),
            )
        if not response.content:
            return None
        return _json_or_raise(response)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        on_page: Callable[[int], None] | None = None,
    ) -> list[Any]:
        """Percorre ``limit``/``offset`` até a página voltar incompleta.

        O offset da API começa em 1. Se uma página repetir a anterior, ou vier
        com tamanho diferente do pedido, a paginação é interrompida.
        """
        if page_size < 1:
            raise ValueError("page_size deve ser maior que zero")
        items: list[Any] = []
        offset = 1
        previous_page: list[Any] | None = None
        base = dict(params or {})
        while True:
            payload = self.get(path, {**base, "limit": page_size, "offset": offset})
            page = extract_list(payload)
            if not page or page == previous_page:
                return items
            items.extend(page)
            if on_page is not None:
                on_page(len(items))
            previous_page = page
            if len(page) != page_size:
                return items
            offset += page_size

    def list_devices(self, **filters: Any) -> list[dict[str, Any]]:
        """Lista dispositivos. Filtros seguem a API, por exemplo hostname e family."""
        return self.get_all("dna/intent/api/v1/network-device", filters or None)

    def get_device(self, device_id: str) -> dict[str, Any]:
        payload = self.get(f"dna/intent/api/v1/network-device/{device_id}")
        data = unwrap_response(payload)
        if not isinstance(data, dict):
            raise CatalystCenterError("Resposta de dispositivo em formato inesperado")
        return data

    def count_devices(self, **filters: Any) -> int:
        payload = self.get("dna/intent/api/v1/network-device/count", filters or None)
        response = unwrap_response(payload)
        if isinstance(response, int):
            return response
        if isinstance(response, dict) and "count" in response:
            return int(response["count"])
        raise CatalystCenterError("Resposta de contagem de dispositivos em formato inesperado")

    def list_sites(self, **filters: Any) -> list[dict[str, Any]]:
        return self.get_all("dna/intent/api/v1/site", filters or None)

    def network_health(self, timestamp: int | None = None) -> Any:
        params = {"timestamp": timestamp} if timestamp is not None else None
        return self.get("dna/intent/api/v1/network-health", params)

    def client_health(self, timestamp: int | None = None) -> Any:
        params = {"timestamp": timestamp} if timestamp is not None else None
        return self.get("dna/intent/api/v1/client-health", params)

    def list_assurance_clients(
        self,
        *,
        site_hierarchy: str | None = None,
        client_type: str | None = None,
        ssid: str | list[str] | None = None,
        site_id: str | list[str] | None = None,
        view: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        on_page: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Lista clientes de Assurance (Health › Client), com filtro de localidade.

        ``site_hierarchy`` aceita um trecho do caminho do site, por exemplo
        ``Global/Brasil/Loja``. ``ssid`` restringe a rede sem fio. ``start_time``
        e ``end_time`` são epoch em milissegundos. Sem intervalo, a API usa as
        últimas 24 horas.
        """
        params: dict[str, Any] = {}
        if site_hierarchy:
            params["siteHierarchy"] = site_hierarchy
        if client_type:
            params["type"] = client_type
        if ssid:
            params["ssid"] = ssid
        if site_id:
            params["siteId"] = site_id
        if view:
            params["view"] = view
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self.get_all(
            "dna/data/api/v1/clients",
            params or None,
            on_page=on_page,
        )

    def list_client_health_by_location(
        self,
        *,
        site_type: str | None = None,
        timestamp: int | None = None,
        on_page: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Saúde de clientes por localidade, a partir de site-health.

        Cada item é um site (área, prédio ou andar) com os contadores e os
        scores de clientes cabeados e sem fio.
        """
        params: dict[str, Any] = {}
        if site_type:
            params["siteType"] = site_type
        if timestamp is not None:
            params["timestamp"] = timestamp
        return self.get_all(
            "dna/intent/api/v1/site-health",
            params or None,
            page_size=SITE_HEALTH_PAGE_SIZE,
            on_page=on_page,
        )

    def device_health(self, **params: Any) -> Any:
        return self.get("dna/intent/api/v1/device-health", params or None)

    def list_issues(
        self,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Lista issues. Sem intervalo, usa as últimas 24 horas em epoch milissegundos."""
        now_ms = int(time.time() * 1000)
        query = dict(params)
        query.setdefault("startTime", start_time if start_time is not None else now_ms - 24 * 60 * 60 * 1000)
        query.setdefault("endTime", end_time if end_time is not None else now_ms)
        return self.get_all("dna/intent/api/v1/issues", query)

    def list_interfaces(self, device_id: str | None = None) -> list[dict[str, Any]]:
        params = {"deviceId": device_id} if device_id else None
        return self.get_all("dna/intent/api/v1/interface", params)

    def _ensure_token(self) -> str:
        expired = (time.monotonic() - self._token_obtained_at) >= TOKEN_TTL_SECONDS
        if self._token is None or expired:
            return self.authenticate()
        return self._token


def extract_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        response = payload.get("response", payload)
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            return [response]
    return []


def unwrap_response(payload: Any) -> Any:
    if isinstance(payload, dict) and "response" in payload:
        return payload["response"]
    return payload


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    return {key: value for key, value in params.items() if value is not None}


def _safe_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _json_or_raise(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise CatalystCenterError("A API respondeu com um corpo que não é JSON") from exc
