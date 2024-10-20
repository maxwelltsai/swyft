from swyft.lightning.core import *

# from swyft.lightning.stores import *
from swyft.lightning.estimators import *
from swyft.lightning.bounds import *
from swyft.lightning.simulator import *
from swyft.lightning.data import *
from swyft.lightning.utils import *
from swyft.lightning import xpu

__all__ = [
    "LogRatioSamples",
    "Simulator",
    "get_pdf",
    "get_weighted_samples",
    "get_class_probs",
    "best_from_yaml",
    "SwyftModule",
    "SwyftTrainer",
    "PosteriorMassSamples",
    "Sample",
    "Samples",
    "SwyftDataModule",
    "CoverageSamples",
    "estimate_coverage",
    "equalize_tensors",
    "LogRatioEstimator_1dim",
    "LogRatioEstimator_Ndim",
    "LogRatioEstimator_Autoregressive",
    "LogRatioEstimator_Gaussian",
    "RectBoundSampler",
    "LogRatioEstimator_1dim_Gaussian",
    "AdamW",
    "AdamWOneCycleLR",
    "AdamWReduceLROnPlateau",
]




from lightning.pytorch.strategies import StrategyRegistry

StrategyRegistry.register("xpu_single", xpu.SingleXPUStrategy)
StrategyRegistry.register("xpu_ddp", xpu.DDPXPUStrategy)