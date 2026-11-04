"""Ligne de commande : python -m hpfc.cli <commande> ...

  run          construit la courbe de la dernière séance EEX (ou de --date) et l'archive
  backfill     construit les courbes des séances encore disponibles sur EEX (~45 jours)
  list         liste les courbes archivées
  curve        extrait une courbe archivée (csv ou parquet)
  settlements  extrait des settlements archivés
  accuracy     précision des courbes archivées face au spot day-ahead réalisé (data/accuracy/)
  options      archive les settlements d'options EEX des ~45 derniers jours (data/eex/options/)
  estr         complète le journal des €STR de la BCE (data/ecb/estr.csv)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from hpfc import accuracy, archive, options, pipeline
from hpfc.config import load_config
from marketdata import DataNotFoundError


def _date(s: str) -> date:
    return date.fromisoformat(s)


def _write(df, out: str | None, fmt: str) -> None:
    if out is None:
        print(df.to_string())
    elif fmt == "csv":
        df.to_csv(out)
        print(f"Écrit : {out} ({len(df)} lignes)")
    else:
        df.to_parquet(out)
        print(f"Écrit : {out} ({len(df)} lignes)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hpfc", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run")
    p.add_argument("--market", default="FR")
    p.add_argument("--date", type=_date, help="séance de pricing (défaut : dernière publiée)")
    p.add_argument("--force", action="store_true", help="reconstruit une courbe déjà archivée")

    p = sub.add_parser("backfill")
    p.add_argument("--market", default="FR")
    p.add_argument("--lookback-days", type=int, default=60)

    p = sub.add_parser("list")
    p.add_argument("--market", default="FR")

    p = sub.add_parser("curve")
    p.add_argument("--market", default="FR")
    p.add_argument("--date", type=_date, required=True)
    p.add_argument("--out")
    p.add_argument("--format", choices=["csv", "parquet"], default="csv")

    p = sub.add_parser("settlements")
    p.add_argument("--market", default="FR")
    p.add_argument("--session", type=_date)
    p.add_argument("--start", type=_date)
    p.add_argument("--end", type=_date)
    p.add_argument("--contract", help='ex. "Cal-27", "M11-26", "Q1-27"')
    p.add_argument("--profile", choices=["Base", "Peak"])
    p.add_argument("--out")
    p.add_argument("--format", choices=["csv", "parquet"], default="csv")

    p = sub.add_parser("accuracy")
    p.add_argument("--market", default="FR")

    p = sub.add_parser("estr")

    p = sub.add_parser("options")
    p.add_argument("--market", default="FR")
    p.add_argument("--lookback-days", type=int, default=45)
    p.add_argument("--force", action="store_true", help="réécrit les séances déjà archivées")

    for sp in sub.choices.values():
        sp.add_argument("--root", default=str(archive.DATA_ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")

    if args.cmd == "run":
        res = pipeline.run(args.market, args.date, force=args.force, root=root)
        if res.status == "exists":
            print(f"Rien de nouveau : la courbe {args.market} du {res.pricing_date} est déjà archivée")
        else:
            m = res.metadata
            print(f"Courbe {args.market} du {res.pricing_date} créée : {res.path}")
            print(f"  {m['products']['quoted']} produits cotés, {m['constraints']['retained']} contraintes, "
                  f"{m['calibration']['iterations']} itérations, "
                  f"écart max {m['checks']['max_gap_products']:.4f} {m['unit']}")
        return 0

    if args.cmd == "backfill":
        report = pipeline.backfill(args.market, lookback_days=args.lookback_days, root=root)
        print(report.to_string(index=False))
        return 1 if (report["status"] == "failed").any() else 0

    if args.cmd == "list":
        for d in archive.list_curves(args.market, root):
            print(d)
        return 0

    if args.cmd == "curve":
        df = archive.load_curve(args.market, args.date, root)
        if args.format == "csv":
            df = df.assign(local=df["local"].astype(str))     # heure locale lisible dans Excel
        _write(df, args.out, args.format)
        return 0

    if args.cmd == "settlements":
        df = archive.load_settlements(args.market, session=args.session, start=args.start, end=args.end,
                                      contract=args.contract, profile=args.profile, root=root)
        cols = ["session", "profile", "kind", "name", "code", "maturity", "start", "end", "settlement"]
        _write(df[cols].set_index("session"), args.out, args.format)
        return 0
    if args.cmd == "estr":
        from marketdata import ecb
        try:
            last = max(archive.load_estr(root).index)
            start = last + timedelta(days=1)
        except DataNotFoundError:
            start = ecb.ESTR_START
        end = date.today()
        if start > end:
            print("Journal €STR déjà à jour")
            return 0
        added = archive.save_estr(ecb.fetch_estr(start, end), root)
        s = archive.load_estr(root)
        print(f"€STR : {added} valeur(s) ajoutée(s) ; journal du {s.index[0]} au {s.index[-1]} "
              f"(dernière : {s.iloc[-1]:.3f} %)")
        return 0

    if args.cmd == "options":
        report = options.collect(args.market, lookback_days=args.lookback_days, root=root, force=args.force)
        print(report.to_string(index=False))
        return 0

    if args.cmd == "accuracy":
        try:
            df, summ = accuracy.run(load_config(args.market), root)
        except DataNotFoundError as e:          # pas encore de jour livré : rien à écrire, pas une erreur
            print(f"Précision non calculée : {e}")
            return 0
        print(f"Précision HPFC {args.market} : {len(df)} couples (courbe, jour livré), "
              f"{df['curve_date'].nunique()} courbes, jusqu'au {df['delivery_date'].max()}")
        print(summ.drop(columns=["first_delivery", "last_delivery"], errors="ignore").round(2).to_string())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
