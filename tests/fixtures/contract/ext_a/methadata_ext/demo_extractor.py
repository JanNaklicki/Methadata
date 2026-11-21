from methadata.contracts import AdapterKind, AdapterSpec


class DemoExtractor:
    spec = AdapterSpec(id="demo-extractor-v1", kind=AdapterKind.extractor, version="1")

    def extract(self, observation, *, reader, optional=None):
        return [], []


ADAPTERS = [DemoExtractor()]
