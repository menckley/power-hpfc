# Contribuer à power-hpfc

Les contributions sont bienvenues (issues et Pull Requests). Les données de marché sont dans un dépôt privé :
les tests n'en ont pas besoin et ne doivent jamais faire d'appel réseau.

## Installation

```bash
pip install -e ".[dev]"      # ajouter ",app" pour l'application Streamlit
```

## Avant d'ouvrir une Pull Request

```bash
ruff check .                 # lint (configuration dans pyproject.toml, lignes de 120 caractères max)
python -m pytest -q          # tests
```

La CI (`.github/workflows/ci.yml`) lance les deux sur Python 3.11 et 3.12 ; elle doit être verte.

## Règles

- Un changement de comportement s'accompagne d'un test dans `tests/`.
- Aucune donnée inventée : une donnée manquante lève `DataNotFoundError`.
- Ne jamais committer de secret, de jeton ni de donnée EEX (leur rediffusion est soumise à licence).
- Messages de commit au format Conventional Commits (`fix:`, `feat:`, `docs:`, `test:`, `chore:`…), en français.
- Le détail des calculs est dans [docs/NOTICE.md](docs/NOTICE.md) : le mettre à jour si un calcul change.
