"""Construct orchestration services from checked-in policy."""

import os
from pathlib import Path

from aurora.inference.config import load_model_specs
from aurora.inference.router import ModelRouter
from .engine import OrchestrationEngine

def build_orchestration_engine(policy_path: str | Path) -> OrchestrationEngine:
    """Build orchestration without storing provider credentials."""
    models = load_model_specs(policy_path)
    return OrchestrationEngine(ModelRouter(models=models, nvidia_api_key=os.getenv("NVIDIA_API_KEY")))
