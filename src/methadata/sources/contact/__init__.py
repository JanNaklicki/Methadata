from .adapter import ADAPTERS_VERSION, ContactComparator, ContactExtractor, group_by_contact


def get_adapters():
    return [ContactExtractor(), ContactComparator()]


ADAPTERS = get_adapters()

__all__ = ["ContactExtractor", "ContactComparator", "group_by_contact", "get_adapters", "ADAPTERS", "ADAPTERS_VERSION"]
