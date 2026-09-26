import json
from aurora.autonomy.registry import RegistryError, load_registry

def test_load_registry(tmp_path):
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"tools": [{"name": "rg", "kind": "system", "source": "ubuntu-apt", "executable": "rg"}]}), encoding="utf-8")
    assert load_registry(p)["rg"].source == "ubuntu-apt"

def test_registry_rejects_duplicates(tmp_path):
    p = tmp_path / "registry.json"
    item = {"name": "rg", "kind": "system", "source": "ubuntu-apt", "executable": "rg"}
    p.write_text(json.dumps({"tools": [item, item]}), encoding="utf-8")
    try:
        load_registry(p)
    except RegistryError:
        pass
    else:
        raise AssertionError("expected RegistryError")
