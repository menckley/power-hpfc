# power-hpfc

Hourly Price Forward Curve (HPFC) du marché français de l'électricité, construite
chaque jour à partir des settlements EEX et de l'historique day-ahead ENTSO-E.

## Organisation

- `src/marketdata/` : modules de données **réutilisables** (ENTSO-E, EEX, stockage Parquet).
  Ils ne dépendent pas de la HPFC et peuvent être importés dans d'autres projets.
- `src/hpfc/` : librairie HPFC (étape 2).
- `scripts/` : points d'entrée (vérification des sources, run quotidien).
- `tests/` : tests automatiques (`python -m pytest`).
- `notebooks/` : notebook de recherche.
- `data/` : archive alimentée par le workflow quotidien.

## Installation locale

```bash
/chemin/vers/envir_lib/bin/python -m pip install -e ".[dev]"
/chemin/vers/envir_lib/bin/python -m pytest
```

## Règle de données

Aucune donnée n'est inventée : toute donnée manquante ou incohérente lève
`DataNotFoundError` et arrête le traitement.
