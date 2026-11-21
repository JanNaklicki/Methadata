from .adapter import (ADAPTERS_VERSION, MediaProvenanceComparator, MediaProvenanceExtractor)
from .reader import Provenance, read_provenance, validator_info


def get_adapters():
    return [MediaProvenanceExtractor(), MediaProvenanceComparator()]


ADAPTERS = get_adapters()

__all__ = ["MediaProvenanceExtractor", "MediaProvenanceComparator", "Provenance", "read_provenance",
           "validator_info", "get_adapters", "ADAPTERS", "ADAPTERS_VERSION"]
