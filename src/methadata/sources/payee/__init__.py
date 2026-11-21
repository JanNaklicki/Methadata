from .adapter import (ADAPTERS_VERSION, PayeeComparator, PayeeExtractor, find_payees, group_by_payee, iban_valid, mask,
                      pii_token)


def get_adapters():
    return [PayeeExtractor(), PayeeComparator()]


ADAPTERS = get_adapters()

__all__ = ["PayeeExtractor", "PayeeComparator", "find_payees", "group_by_payee", "iban_valid", "mask", "pii_token",
           "get_adapters", "ADAPTERS", "ADAPTERS_VERSION"]
