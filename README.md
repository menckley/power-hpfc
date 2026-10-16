# power-hpfc

Hourly Price Forward Curve (HPFC) du marché français de l'électricité, construite
chaque jour à partir des settlements EEX et de l'historique day-ahead ENTSO-E.

## Organisation

- `src/marketdata/` : modules de données **réutilisables** (ENTSO-E, EEX, stockage Parquet).
- `src/hpfc/` : librairie HPFC (calendrier, contraintes, forme, calibration, archive, pipeline).
- `config/FR.yaml` : paramètres du marché et du modèle.
- `tests/` : tests automatiques (`python -m pytest`).
- `data/` : archive (settlements, spot, courbes, précision), alimentée par le run quotidien.

## Commandes

La clé ENTSO-E est lue dans la variable d'environnement `ENTSOE_API_KEY`.

```bash
python -m hpfc.cli run                                   # courbe de la dernière séance EEX
python -m hpfc.cli run --date 2026-10-05                 # courbe d'une séance précise
python -m hpfc.cli backfill                              # séances encore disponibles (~45 jours)
python -m hpfc.cli list                                  # courbes archivées
python -m hpfc.cli curve --date 2026-10-05 --out c.csv   # extraction d'une courbe
python -m hpfc.cli settlements --contract Cal-27 --profile Base --out cal27.csv
python -m hpfc.cli accuracy                              # précision des courbes face au day-ahead réalisé
```

En Python :

```python
from hpfc.archive import load_curve, load_settlements, load_spot
curve = load_curve("FR", "2026-10-05")
cal27 = load_settlements("FR", contract="Cal-27", profile="Base")
```

## Précision de la HPFC

Chaque jour, après la courbe, le workflow lance `python -m hpfc.cli accuracy` : toutes les courbes archivées
sont comparées au spot day-ahead ENTSO-E réellement fixé, pour chaque jour déjà livré et entièrement archivé.
Erreur = HPFC − day-ahead (positive : la courbe était trop haute).

| Fichier | Contenu |
|---|---|
| `data/accuracy/market=FR/daily.parquet` | une ligne par (courbe, jour livré) : horizon en jours, Base / Peak / Offpeak prévus et réalisés, MAE et RMSE horaires, corrélation du profil horaire du jour |
| `data/accuracy/market=FR/summary.csv` | par horizon (D+1, D+2-7, D+8-30, D+31-90, D+91-365, au-delà) : biais, MAE, RMSE et MAPE Base, MAE Peak et Offpeak, MAE et RMSE horaires, corrélation du profil |

- Rien n'est extrapolé : un jour incomplet d'un côté ou de l'autre n'est pas compté. Tant qu'aucun jour n'est
  livré, la commande l'indique et n'écrit rien (le workflow continue).
- Le calcul est refait en entier à chaque lancement, à partir de l'archive seule : il est reproductible.
- Lecture : sur les premiers jours, le niveau de la courbe est ancré sur les settlements EEX Day / Weekend /
  Week ; plus loin sur les mois, trimestres et années. L'erreur de niveau mesure donc d'abord l'écart entre le
  marché à terme et le day-ahead ; la forme horaire (MAE horaire, corrélation du profil) est l'apport propre de
  la HPFC.
- Les horizons longs ne se remplissent qu'avec le temps (une courbe à D+91 n'est mesurable qu'après 3 mois) :
  le nombre d'observations est affiché à côté de chaque statistique.
- Usage dans le pricer : quand l'historique sera assez long, ces erreurs remplaceront, horizon par horizon,
  l'approximation par persistance de la prime de déviation day-ahead.

Page **HPFC accuracy** de l'application : tableau par horizon, période choisie (tout, 365, 90, 30 derniers jours),
Base prévue face au réalisé à un horizon donné, dispersion des erreurs par horizon, export CSV.

## Application web

```bash
streamlit run app/streamlit_app.py      # en local, s'ouvre dans le navigateur
```

En ligne : Streamlit Community Cloud, dépôt `power-hpfc`, branche `main`,
fichier principal `app/streamlit_app.py` (dépendances : `app/requirements.txt`).
L'application lit directement le dossier `data/` du dépôt : chaque courbe poussée par
le workflow quotidien y apparaît automatiquement.

## Règle de données

Aucune donnée n'est inventée : toute donnée manquante ou incohérente lève
`DataNotFoundError` et arrête le traitement.
