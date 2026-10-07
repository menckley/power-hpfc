"""Modules de données de marché réutilisables (ENTSO-E, EEX).

Ce paquet ne dépend d'aucune logique HPFC : il peut être importé tel quel
dans n'importe quel autre projet. Règle commune : aucune donnée n'est
inventée ; toute donnée manquante ou incohérente lève DataNotFoundError.
"""
from marketdata.errors import DataNotFoundError

__all__ = ["DataNotFoundError"]
