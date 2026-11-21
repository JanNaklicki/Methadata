from .adapter import ADAPTERS_VERSION, HistoryComparator, HistoryExtractor, compare_versions, save_timeline
from .analyze import analyze_html, normalize_url
from .archive import ArchiveSnapshot, RecordedArchive, WaybackArchive
from .build import add_archive, from_store, make_version
from .cluster import cluster_as_of
from .diff import diff_snapshots
from .model import PageVersion
from .timeline import History


def get_adapters():
    return [HistoryExtractor(), HistoryComparator()]


ADAPTERS = get_adapters()

__all__ = ["HistoryExtractor", "HistoryComparator", "History", "PageVersion", "make_version", "add_archive", "from_store",
           "cluster_as_of", "diff_snapshots", "analyze_html", "normalize_url", "RecordedArchive", "WaybackArchive",
           "ArchiveSnapshot", "save_timeline", "compare_versions", "get_adapters", "ADAPTERS", "ADAPTERS_VERSION"]
