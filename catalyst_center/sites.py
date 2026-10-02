"""Localiza sites do Catalyst Center a partir de um texto informado pelo usuário."""

from __future__ import annotations

from typing import Any


def site_hierarchy(site: dict[str, Any]) -> str:
    for key in ("siteNameHierarchy", "groupNameHierarchy", "siteHierarchy"):
        value = site.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def site_record_name(site: dict[str, Any]) -> str:
    for key in ("name", "groupName", "siteName"):
        value = site.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def matching_site_hierarchies(sites: list[dict[str, Any]], query: str) -> list[str]:
    """Hierarquias que contêm o texto, sem repetir um site que já está sob outro."""
    needle = query.strip().casefold()
    if not needle:
        return []
    found: list[str] = []
    for site in sites:
        hierarchy = site_hierarchy(site)
        name = site_record_name(site)
        if needle in hierarchy.casefold() or needle in name.casefold():
            if hierarchy:
                found.append(hierarchy)
    ordered = sorted(set(found), key=lambda item: (item.count("/"), len(item), item.casefold()))
    kept: list[str] = []
    for item in ordered:
        if any(_is_under(item, parent) for parent in kept):
            continue
        kept.append(item)
    return kept


def site_ids_under(sites: list[dict[str, Any]], hierarchies: list[str]) -> list[str]:
    """Ids do site escolhido e dos sites abaixo dele."""
    if not hierarchies:
        return []
    identifiers: list[str] = []
    seen: set[str] = set()
    for site in sites:
        hierarchy = site_hierarchy(site)
        site_id = str(site.get("id") or site.get("siteId") or "").strip()
        if not site_id or site_id in seen or not hierarchy:
            continue
        if any(_is_under(hierarchy, parent) for parent in hierarchies):
            seen.add(site_id)
            identifiers.append(site_id)
    return identifiers


def _is_under(path: str, parent: str) -> bool:
    current = path.strip().strip("/").casefold()
    base = parent.strip().strip("/").casefold()
    if not current or not base:
        return False
    return current == base or current.startswith(base + "/")
