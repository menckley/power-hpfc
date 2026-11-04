"""Grille semaine 24 h × 7 jours à cocher en glissant (rectangle type Excel) : composant Streamlit maison.

Le composant (components/week_grid/index.html, HTML et JavaScript sans dépendance) affiche la grille et renvoie la
grille complète après chaque modification. `version` sert à distinguer une grille imposée par Python (bouton de
raccourci) d'une modification faite à la souris : le composant n'accepte une nouvelle grille de Python que si la
version change, et Python n'accepte une grille du composant que si elle porte la version en cours.
"""
from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components

_component = components.declare_component("week_grid", path=str(Path(__file__).parent / "components" / "week_grid"))


def week_grid(grid: list[list[bool]], version: int, key: str = "week_grid") -> list[list[bool]] | None:
    """Affiche la grille ; renvoie la grille modifiée à la souris, ou None si rien n'a changé à cette version."""
    if len(grid) != 24 or any(len(row) != 7 for row in grid):
        raise ValueError("grille 24 heures × 7 jours attendue")
    value = _component(grid=[[bool(x) for x in row] for row in grid], version=version, key=key, default=None)
    if not value or value.get("version") != version:
        return None
    out = value.get("grid")
    if not isinstance(out, list) or len(out) != 24 or any(len(row) != 7 for row in out):
        return None
    return [[bool(x) for x in row] for row in out]
