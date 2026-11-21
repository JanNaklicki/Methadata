from .adapter import ADAPTERS_VERSION, PayerComparator, PayerExtractor, group_by_payer, normalize_entity


def get_adapters():
    return [PayerExtractor(), PayerComparator()]


ADAPTERS = get_adapters()

__all__ = ["PayerExtractor", "PayerComparator", "group_by_payer", "normalize_entity", "get_adapters", "ADAPTERS",
           "ADAPTERS_VERSION"]
