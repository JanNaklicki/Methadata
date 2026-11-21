"""E17: regresja logistyczna na jawnych podobieństwach + aktywne uczenie (czysty Python, bez extras)."""
from .adapters import METHOD_ID, LearnedPairComparator, get_adapters
from .core import (LearnedPairsError, active_queue, build_dataset, check_frozen, explain, freeze_split, labels_needed,
                   run, write_report)
from .model import Logit, auc, feature_names, featurize

__all__ = ["METHOD_ID", "LearnedPairComparator", "get_adapters", "LearnedPairsError", "active_queue", "build_dataset",
           "check_frozen", "explain", "freeze_split", "labels_needed", "run", "write_report", "Logit", "auc",
           "feature_names", "featurize"]
