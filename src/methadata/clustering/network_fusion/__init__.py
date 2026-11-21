from .config import DEFAULT_CONFIG  # noqa: F401
from .core import (METHOD_ID, METHOD_VERSION, SnfClusterer, SnfResult, compare_with_fusion, get_adapters,  # noqa: F401
                   resolve_config, snf_cluster, snf_matrix)

ADAPTERS = get_adapters()
