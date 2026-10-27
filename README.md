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
python -m hpfc.cli options                               # archive les settlements d'options EEX (~45 jours)
```

En Python :

```python
from hpfc.archive import load_curve, load_settlements, load_spot
curve = load_curve("FR", "2026-10-05")
cal27 = load_settlements("FR", contract="Cal-27", profile="Base")
```

## Settlements d'options EEX

Chaque jour, le workflow archive aussi les settlements des **options EEX sur les futures Base France**
(`python -m hpfc.cli options`) dans `data/eex/options/market=FR/session=AAAA-MM-JJ/options.parquet` : une ligne
par (série, strike) avec le settlement du call et du put, les volumes et les open interests. L'API EEX ne garde
qu'environ 45 jours : la première exécution récupère tout ce qui est encore disponible, les suivantes complètent.

| Série | Code EEX | Maturités négociables |
|---|---|---|
| Mois | O7FM | 9 prochains mois |
| Trimestre | O7FQ | 6 prochains trimestres |
| Année | O7FY | 4 prochaines années |

- Codes et format vérifiés sur des réponses réelles le 27/10/2026 ; options sur futures Base uniquement (pas
  d'options Peak sur EEX).
- Une séance n'est archivée que si elle n'est pas plus récente que la dernière séance de futures archivée (futures
  et options sont publiés ensemble) ; une requête en échec n'écrit rien ; une séance archivée n'est pas réécrite.
- **Volumes et open interest nuls** à la date du relevé : ces settlements sont des valorisations EEX, pas des prix
  traités. Contrôles relevés sur le Cal-27 du 26/10/2026 : parité call − put = F − K exacte (sans actualisation,
  F = 121,35), calls décroissants et convexes au millième près ; volatilité implicite ≈ 31 % à la monnaie, très
  forte pente vers les bas strikes (149 % à K = 42) ; aucun strike au-dessus du future.

En Python :

```python
from hpfc.archive import load_options
cal27 = load_options("FR", name="Cal-27")             # toutes les séances archivées
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

Page **HPFC accuracy** de l'application :
- **vue d'un jour** : prix horaires prévus (courbe choisie : D-1, D-7…) face au day-ahead réalisé, précision du
  modèle (MAE horaire ÷ prix moyen du jour, échelle de lecture propre à la page : < 10 % very good, 10-20 % good,
  20-35 % fair, > 35 % poor), corrélation de forme, volatilité du marché (écart-type des prix horaires du jour,
  comparé à sa médiane des 30 jours précédents) ;
- tableau par horizon sur la période choisie (tout, 365, 90, 30 derniers jours), Base prévue face au réalisé à un
  horizon donné, dispersion des erreurs par horizon, export CSV.

À D+1, l'erreur de niveau est nulle par construction (le contrat EEX Day est réglé sur l'enchère day-ahead et la
courbe est calée dessus) : seule la forme horaire est mesurée.

## Application web

```bash
streamlit run app/streamlit_app.py      # en local, s'ouvre dans le navigateur
```

En ligne : Streamlit Community Cloud, dépôt `power-hpfc`, branche `main`,
fichier principal `app/streamlit_app.py` (dépendances : `app/requirements.txt`).
L'application lit directement le dossier `data/` du dépôt : chaque courbe poussée par
le workflow quotidien y apparaît automatiquement.

| Page | Contenu |
|---|---|
| **Pricer** (accueil) | Pricing d'un deal avec le paquet du dépôt privé `pricer` (téléchargé avec le secret `PRICER_TOKEN`, rechargé moins de 5 minutes après un push qui modifie `src/` ou `config/` de `pricer`) ; archivage automatique dans le journal de `pricer` |
| **Pricing history** | Tous les pricings du journal, détail de chacun, export de tous les pricings en CSV ou Excel (journal, construction du prix, scénarios, couverture, avertissements) |
| **HPFC curves** | Extraction des courbes archivées |
| **HPFC accuracy** | Précision des courbes face au day-ahead réalisé |

Secret de l'application (Streamlit Cloud → Settings → Secrets, jamais dans un dépôt) : `PRICER_TOKEN`, jeton GitHub
*fine-grained* limité à `menckley/pricer`, **Contents : Read and write**. En local : `.streamlit/secrets.toml`
(ignoré par git) ou paquet `pricer` installé en mode éditable.

## Règle de données

Aucune donnée n'est inventée : toute donnée manquante ou incohérente lève
`DataNotFoundError` et arrête le traitement.

## Feuille de route

La feuille de route du pricer (options, TBx, flexibilité, produits structurés…) est dans le README du dépôt `pricer`.

### Précision de la HPFC
1. **Référence naïve** à côté de la HPFC (persistance : moyenne des 30 derniers jours corrigée de la saisonnalité,
   et « même jour la semaine dernière ») et **score de compétence** = 1 − erreur HPFC ÷ erreur naïve : dit si la
   courbe apporte quelque chose, pas seulement de combien elle se trompe.
2. Erreur de forme **par heure de la journée** et **par saison** (où le profil se trompe : creux solaire, pointe du
   soir, week-ends, fériés).
3. Erreur de niveau par **horizon en mois** (Peak, Offpeak), au format attendu par la prime de déviation du pricer.
4. Rapport hebdomadaire (résumé de la semaine écrite dans `data/accuracy/`) et alerte si l'erreur dérive.
5. Comparaison au **quart d'heure** depuis le 01/10/2025.

### Modèle HPFC
6. Forme : tendance de la **cannibalisation solaire** (creux de midi qui se creuse d'année en année), ponts et
   fériés, heures à prix négatifs.
7. Météo gratuite (Open-Meteo) pour la forme des premiers jours (température, ensoleillement, vent).
8. Lissage de la calibration (contrainte de régularité entre mois) et courbe au **pas de 15 minutes**.
9. **Autres marchés** (Allemagne, Belgique, Espagne…) : nouveau `config/<marché>.yaml`, mêmes modules.

### Données et exploitation
10. **Alerte en cas d'échec du workflow** (notification GitHub ou e-mail) et contrôle qualité quotidien (séance
    complète, spot complet, écarts aux settlements).
11. Fusion avec `settlements-eex` (settlements et GO) dans l'application, puis une seule plateforme.
12. **Taille de l'archive** : environ 0,8 Mo par courbe, soit 200 à 300 Mo par an ; la taille conseillée d'un dépôt
    GitHub (de l'ordre de 1 Go, à vérifier) serait atteinte en 3 à 4 ans. À prévoir : ne garder en entier que les
    courbes récentes (les plus anciennes réduites aux jours déjà livrés, seuls utiles à la précision) ou un
    stockage externe.

## Reprise après une pause

- `git pull --rebase` avant toute modification : le workflow pousse un commit chaque jour.
- Onglet **Actions** du dépôt : vérifier que les runs quotidiens sont verts (courbe et précision).
- Vérifier que les secrets n'ont pas expiré : `ENTSOE_API_KEY` (secret GitHub du dépôt) et `PRICER_TOKEN`
  (secret Streamlit).
- Page **HPFC accuracy** : l'historique s'est allongé pendant la pause, regarder les horizons D+91 et au-delà.
