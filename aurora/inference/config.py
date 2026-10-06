"""Load and validate the checked-in inference policy."""

import json
from pathlib import Path

from .models import ModelSpec

class InferenceConfigError(ValueError):
    """Raised when inference policy is missing or invalid."""

def load_model_specs(path: str | Path) -> list[ModelSpec]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InferenceConfigError(f"Unable to load inference policy: {source}") from exc
    models = data.get("models")
    if not isinstance(models, list):
        raise InferenceConfigError("inference policy 'models' must be a list")
    result=[]
    for item in models:
        if not isinstance(item, dict) or not all(k in item for k in ("id","provider","endpoint")):
            raise InferenceConfigError("each model requires id, provider, and endpoint")
        caps=item.get("capabilities", [])
        if not isinstance(caps, list) or not all(isinstance(x,str) for x in caps):
            raise InferenceConfigError(f"invalid capabilities for model {item.get('id')!r}")
        specialties=item.get("specialties", [])
        if not isinstance(specialties, list) or not all(isinstance(x, str) and x.strip() for x in specialties):
            raise InferenceConfigError(f"invalid specialties for model {item.get('id')!r}")
        scores = {
            "quality_score": item.get("quality_score", 0.5),
            "latency_score": item.get("latency_score", 0.5),
            "cost_score": item.get("cost_score", 0.5),
        }
        if any(not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0 for value in scores.values()):
            raise InferenceConfigError(f"invalid model routing scores for {item.get('id')!r}")
        pricing_tier = str(item.get("pricing_tier", "unknown")).strip().lower()
        if pricing_tier not in {"free", "paid", "unknown"}:
            raise InferenceConfigError(f"invalid pricing_tier for {item.get('id')!r}")
        result.append(ModelSpec(
            id=item["id"], provider=item["provider"], endpoint=item["endpoint"],
            capabilities=tuple(caps), context_window=item.get("context_window"),
            enabled=bool(item.get("enabled", True)), specialties=tuple(specialties),
            quality_score=float(scores["quality_score"]),
            latency_score=float(scores["latency_score"]),
            cost_score=float(scores["cost_score"]),
            pricing_tier=str(item.get("pricing_tier", "unknown")).strip().lower(),
        ))
    return result
