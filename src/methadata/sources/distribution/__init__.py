from .adapter import (ADAPTERS_VERSION, DistributionComparator, DistributionExtractor, parse_ads_txt, parse_schain,
                      parse_sellers_json, seller_graph_edges)


def get_adapters():
    return [DistributionExtractor(), DistributionComparator()]


ADAPTERS = get_adapters()

__all__ = ["DistributionExtractor", "DistributionComparator", "parse_ads_txt", "parse_sellers_json", "parse_schain",
           "seller_graph_edges", "get_adapters", "ADAPTERS", "ADAPTERS_VERSION"]
