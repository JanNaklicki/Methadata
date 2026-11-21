from methadata.contracts import AdapterKind, AdapterSpec, OptionalInput


class DemoComparator:
    spec = AdapterSpec(id="demo-comparator-v1", kind=AdapterKind.comparator, version="1",
                       optional_inputs=[OptionalInput(name="clip", kind="vector", model="clip", dim=512)])

    def compare(self, a, b, features, *, run_id, optional=None):
        return []


def get_adapters():
    return [DemoComparator()]
