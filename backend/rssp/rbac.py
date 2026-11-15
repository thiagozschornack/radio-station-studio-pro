"""Papéis e permissões (RBAC) por emissora.

O superadministrador é um atributo global do usuário; os demais papéis valem por emissora
(tabela station_members). Toda rota de emissora passa por `require(station_id, perm)`.
"""
from __future__ import annotations

ROLES = ["owner", "director", "programmer", "announcer", "commercial", "analyst", "operator"]

ROLE_LABELS = {
    "superadmin": "Superadministrador",
    "owner": "Proprietário",
    "director": "Diretor",
    "programmer": "Programador",
    "announcer": "Locutor",
    "commercial": "Comercial",
    "analyst": "Analista",
    "operator": "Operador",
}

PERMISSIONS: dict[str, set[str]] = {
    "station.view": set(ROLES),
    "station.edit": {"owner", "director"},
    "members.manage": {"owner"},
    "stream.view": {"owner", "director", "operator", "programmer"},
    "stream.edit": {"owner", "director"},
    "stream.control": {"owner", "director", "operator"},
    "library.view": {"owner", "director", "programmer", "announcer", "operator", "commercial"},
    "library.upload": {"owner", "director", "programmer"},
    "library.edit": {"owner", "director", "programmer"},
    "library.license": {"owner", "director"},
    "queue.view": {"owner", "director", "programmer", "announcer", "operator"},
    "queue.edit": {"owner", "director", "programmer", "operator"},
    "playout.skip": {"owner", "director", "programmer", "operator", "announcer"},
    "reports.view": {"owner", "director", "programmer", "analyst", "commercial"},
    "audit.view": {"owner", "director"},
}


def role_can(role: str | None, perm: str) -> bool:
    return role is not None and role in PERMISSIONS.get(perm, set())


def permissions_for(role: str | None, superadmin: bool) -> list[str]:
    if superadmin:
        return sorted(PERMISSIONS)
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)
