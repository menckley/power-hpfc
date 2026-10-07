"""Vérification des sources réelles (ENTSO-E et EEX) avec les modules marketdata.

Usage :
    export ENTSOE_API_KEY="ta-clé"
    python scripts/check_sources.py
"""
import os
from datetime import date, timedelta

from marketdata import DataNotFoundError
from marketdata.eex import fetch_session, latest_session, listed_candidates
from marketdata.entsoe import fetch_day_ahead, make_client

TZ = "Europe/Paris"


def main() -> None:
    # --- ENTSO-E : la veille, au pas de 15 min ---
    client = make_client(os.environ.get("ENTSOE_API_KEY", ""))
    day = date.today() - timedelta(days=1)
    s = fetch_day_ahead(client, "FR", day, day, TZ)
    print(f"ENTSO-E OK : {len(s)} pas pour le {day}, moyenne {s.mean():.2f} EUR/MWh")

    # --- EEX : dernière séance publiée et contrats cotés ---
    session = latest_session("FR", date.today())
    if session is None:
        raise DataNotFoundError("EEX : aucune séance publiée sur les 10 derniers jours")
    contracts = listed_candidates("FR", session, n_months=6, n_quarters=7, n_years=3)
    df = fetch_session(contracts, session)
    quoted = df["settlement"].notna()
    print(f"EEX OK : séance du {session}, {len(df)} contrats interrogés, "
          f"{int(quoted.sum())} avec settlement, {int((~quoted).sum())} sans")
    std = df["kind"].isin(["month", "quarter", "year"])
    if df.loc[std, "settlement"].isna().any():
        raise DataNotFoundError("EEX : un produit standard n'a pas de settlement")
    print(df.loc[std & (df["profile"] == "Base"), ["name", "settlement"]].head(4).to_string(index=False))


if __name__ == "__main__":
    main()
