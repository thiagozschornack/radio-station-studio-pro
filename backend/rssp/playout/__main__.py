"""python -m rssp.playout --station <slug|id>   |   python -m rssp.playout --all"""
import argparse
import logging

from .engine import run_all, run_station

ap = argparse.ArgumentParser(prog="rssp.playout")
g = ap.add_mutually_exclusive_group(required=True)
g.add_argument("--station", help="slug ou id da emissora")
g.add_argument("--all", action="store_true", help="supervisiona todas as emissoras ativas")
ap.add_argument("--log-level", default="INFO")
a = ap.parse_args()
logging.basicConfig(level=a.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
if a.all:
    run_all()
else:
    run_station(a.station)
