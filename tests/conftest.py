import sys
from pathlib import Path

# Permet de lancer les tests sans installation préalable du paquet
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
