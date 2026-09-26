import json
from aurora.inference.config import InferenceConfigError, load_model_specs

def test_load_model_specs(tmp_path):
    p=tmp_path/"policy.json"
    p.write_text(json.dumps({"models":[{"id":"m","provider":"nvidia_api","endpoint":"https://example.invalid/v1","capabilities":["coding"],"enabled":False}]}), encoding="utf-8")
    specs=load_model_specs(p)
    assert specs[0].id=="m"
    assert specs[0].capabilities==("coding",)
    assert specs[0].enabled is False

def test_invalid_policy_rejected(tmp_path):
    p=tmp_path/"policy.json"
    p.write_text(json.dumps({"models":{}}), encoding="utf-8")
    try:
        load_model_specs(p)
    except InferenceConfigError:
        pass
    else:
        raise AssertionError("expected InferenceConfigError")
