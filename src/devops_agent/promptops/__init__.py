"""PromptOps – zarządzanie wariantami promptów, ewaluacje, detekcja regresji, benchmarki modeli."""

from devops_agent.promptops.registry import PromptRegistry, get_registry
from devops_agent.promptops.datasets import DatasetManager, get_dataset_manager
from devops_agent.promptops.evaluator import EvalRunner
from devops_agent.promptops.regression import RegressionDetector
from devops_agent.promptops.benchmarks import ModelBenchmark

__all__ = [
    "PromptRegistry",
    "get_registry",
    "DatasetManager",
    "get_dataset_manager",
    "EvalRunner",
    "RegressionDetector",
    "ModelBenchmark",
]
