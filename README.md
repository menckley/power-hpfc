# power-hpfc

Hourly Price Forward Curve (HPFC) du marché français de l'électricité, construite
chaque jour à partir des settlements EEX et de l'historique day-ahead ENTSO-E.

## Organisation

- `src/marketdata/` : modules de données **réutilisables** (ENTSO-E, EEX, stockage Parquet).
- `src/hpfc/` : librairie HPFC (calendrier, contraintes, forme, calibration, archive, pipeline).
- `config/FR.yaml` : paramètres du marché et du modèle.
- `tests/` : tests automatiques (`python -m pytest`).
- `data/` : archive (settlements, spot, courbes), alimentée par le run quotidien.

## Commandes

La clé ENTSO-E est lue dans la variable d'environnement `ENTSOE_API_KEY`.

```bash
python -m hpfc.cli run                                   # courbe de la dernière séance EEX
python -m hpfc.cli run --date 2026-10-05                 # courbe d'une séance précise
python -m hpfc.cli backfill                              # séances encore disponibles (~45 jours)
python -m hpfc.cli list                                  # courbes archivées
python -m hpfc.cli curve --date 2026-10-05 --out c.csv   # extraction d'une courbe
python -m hpfc.cli settlements --contract Cal-27 --profile Base --out cal27.csv
```

En Python :

```python
from hpfc.archive import load_curve, load_settlements, load_spot
curve = load_curve("FR", "2026-10-05")
cal27 = load_settlements("FR", contract="Cal-27", profile="Base")
```

## Règle de données

Aucune donnée n'est inventée : toute donnée manquante ou incohérente lève
`DataNotFoundError` et arrête le traitement.
