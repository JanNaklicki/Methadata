from .config import DEFAULT_CONFIG  # noqa: F401
from .core import (FusionComparator, FusionResult, METHOD_ID, METHOD_VERSION, PairFusion, cluster_config,  # noqa: F401
                   collect_pairs, fuse_and_cluster, fuse_pair, resolve_config)


def get_adapters():
    return [FusionComparator()]
