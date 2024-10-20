from dataclasses import dataclass, field
from toolz.dicttoolz import valmap
from typing import (
    Callable,
    Dict,
    Hashable,
    Optional,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    Union,
    Any,
)
import numpy as np
import torch
from torch.nn import functional as F

from lightning import pytorch as pl
from lightning.pytorch.callbacks import LearningRateMonitor, ModelCheckpoint
from lightning.pytorch.callbacks.early_stopping import EarlyStopping

try:
    from lightning.pytorch.trainer.supporters import CombinedLoader
except ImportError:
    from lightning.pytorch.utilities import CombinedLoader

# from pytorch_lightning.cli import instantiate_class

import yaml

from swyft.lightning.data import *
import swyft.lightning.simulator

import scipy
from scipy.ndimage import gaussian_filter1d, gaussian_filter
import torchist


##################
# Parameter errors
##################


class SwyftParameterError(Exception):
    """General parameter error in Swyft."""

    pass


############################
# Weights, PDFs and coverage
############################


def _pdf_from_weighted_samples(v, w, bins=50, smooth=0, smooth_prior=False):
    """Take weighted samples and turn them into a pdf on a grid.

    Args:
        bins
    """
    ndim = v.shape[-1]
    if not smooth_prior:
        return _weighted_smoothed_histogramdd(v, w, bins=bins, smooth=smooth)
    else:
        h, xy = _weighted_smoothed_histogramdd(v, w * 0 + 1, bins=bins, smooth=smooth)
        if ndim == 2:
            X, Y = np.meshgrid(xy[:, 0], xy[:, 1])
            n = len(xy)
            out = scipy.interpolate.griddata(
                v, w, (X.flatten(), Y.flatten()), method="cubic", fill_value=0.0
            ).reshape(n, n)
            out = out * h.numpy()
            return out, xy
        elif ndim == 1:
            out = scipy.interpolate.griddata(
                v[:, 0], w, xy[:, 0], method="cubic", fill_value=0.0
            )
            out = out * h.numpy()
            return out, xy
        else:
            raise KeyError("Not supported")


def _weighted_smoothed_histogramdd(v, w, bins=50, smooth=0):
    ndim = v.shape[-1]
    if ndim == 1:
        low, upp = v.min(), v.max()
        h = torchist.histogramdd(v, bins, weights=w, low=low, upp=upp)
        h /= len(v) * (upp - low) / bins
        edges = torch.linspace(low, upp, bins + 1)
        x = (edges[1:] + edges[:-1]) / 2
        if smooth > 0:
            h = torch.tensor(gaussian_filter1d(h, smooth))
        return h, x.unsqueeze(-1)
    elif ndim == 2:
        low = v.min(axis=0).values
        upp = v.max(axis=0).values
        h = torchist.histogramdd(v, bins=bins, weights=w, low=low, upp=upp)
        h /= len(v) * (upp[0] - low[0]) * (upp[1] - low[1]) / bins ** 2
        x = torch.linspace(low[0], upp[0], bins + 1)
        y = torch.linspace(low[1], upp[1], bins + 1)
        x = (x[1:] + x[:-1]) / 2
        y = (y[1:] + y[:-1]) / 2
        xy = torch.vstack([x, y]).T
        if smooth > 0:
            h = torch.tensor(gaussian_filter(h * 1.0, smooth))
        return h, xy


def get_pdf(
    lrs_coll,
    params: Union[str, Sequence[str]],
    aux=None,
    bins: int = 50,
    smooth: float = 0.0,
    smooth_prior=False,
):
    """Generate binned PDF based on input

    Args:
        lrs_coll: Collection of LogRatioSamples objects.
        params: Parameter names
        bins: Number of bins
        smooth: Apply Gaussian smoothing
        smooth_prior: Smooth prior instead of posterior

    Returns:
        np.array, np.array: Returns densities and parameter grid.
    """
    z, w = get_weighted_samples(lrs_coll, params)
    if aux is not None:
        z_aux, _ = get_weighted_samples(aux, params)
    else:
        z_aux = None
    return _pdf_from_weighted_samples(
        z, w, bins=bins, smooth=smooth, smooth_prior=smooth_prior
    )


def _get_weights(logratios, normalize: bool = False):
    """Calculate weights based on ratios.

    Args:
        normalize: If true, normalize weights to sum to one.  If false, return weights = exp(logratios).
    """
    if normalize:
        logratio_max = logratios.max(axis=0).values
        weights = torch.exp(logratios - logratio_max)
        weights_total = weights.sum(axis=0)
        weights = weights / weights_total * len(weights)
    else:
        weights = torch.exp(logratios)
    return weights


def get_weighted_samples(lrs_coll, params: Union[str, Sequence[str]]):
    """Returns weighted samples for particular parameter combination.

    Args:
        params: (List of) parameter names

    Returns:
        (torch.Tensor, torch.Tensor): Parameter and weight tensors
    """
    params = params if isinstance(params, list) else [params]
    if not (isinstance(lrs_coll, list) or isinstance(lrs_coll, tuple)):
        lrs_coll = [lrs_coll]
    for l in lrs_coll:
        for i, pars in enumerate(l.parnames):
            if all(x in pars for x in params):
                idx = [list(pars).index(x) for x in params]
                params = l.params[:, i, idx]
                weights = _get_weights(l.logratios, normalize=True)[:, i]
                return params, weights
    raise SwyftParameterError("Requested parameters not available:", *params)


def get_class_probs(lrs_coll, params: str):
    """Return class probabilities for discrete parameters.

    Args:
        lrs_coll: Collection of LogRatioSamples objects
        params: Parameter of interest (must be (0, 1, ..., K-1) for K classes)

    Returns:
        np.Array: Vector of length K with class probabilities
    """
    params, weights = get_weighted_samples(lrs_coll, params)
    probs = np.array(
        [weights[params[:, 0] == k].sum() for k in range(int(params[:, 0].max()) + 1)]
    )
    probs /= probs.sum()
    return probs


# def weights_sample(N, values, weights, replacement = True):
#    """Weight-based sampling with or without replacement."""
#    sw = weights.shape
#    sv = values.shape
#    assert sw == sv[:len(sw)], "Overlapping left-handed weights and values shapes do not match: %s vs %s"%(str(sv), str(sw))
#
#    w = weights.view(weights.shape[0], -1)
#    idx = torch.multinomial(w.T, N, replacement = replacement).T
#    si = tuple(1 for _ in range(len(sv)-len(sw)))
#    idx = idx.view(N, *sw[1:], *si)
#    idx = idx.expand(N, *sv[1:])
#
#    samples = torch.gather(values, 0, idx)
#    return samples


def estimate_coverage(cs_coll, params, z_max=3.5, bins=50):
    """Estimate coverage from collection of coverage_samples objects."""
    return _collection_select(
        cs_coll,
        "Requested parameters not available: %s" % (params,),
        "estimate_coverage",
        params,
        z_max=z_max,
        bins=bins,
    )


######
# Misc
######


def best_from_yaml(filepath):
    """Get best model from tensorboard log. Useful for reloading trained networks.

    Args:
        filepath: Filename of yaml file (assumed to be saved with to_yaml from ModelCheckpoint)

    Returns:
        path to best model
    """
    try:
        with open(filepath) as f:
            best_k_models = yaml.load(f, Loader=yaml.FullLoader)
    except FileNotFoundError:
        return None
    val_loss = np.inf
    path = None
    for k, v in best_k_models.items():
        if v < val_loss:
            path = k
            val_loss = v
    return path


##################
# Collection utils
##################


def param_select(parnames, target_parnames, match_exactly: bool = False):
    """Find indices of parameters of interest.

    The output can be used to for instance select parameter from the LogRatioSamples object like

    obj.params[idx1][idx2]

    Args:
        parnames: :math:`(*logratios_shape, num_params)`
        target_parnames: List of parameter names of interest
        match_exactly: Only return exact matches (i.e. no partial matches)

    Returns:
        tuple, list: idx1 (logratio index), idx2 (parameter indices)
    """
    assert (
        len(parnames.shape) == 2
    ), "`param_select` is only implemented for 1-dim logratios_shape"
    for i, pars in enumerate(parnames):
        if all(target_parname in pars for target_parname in target_parnames):
            idx = [list(pars).index(tp) for tp in target_parnames]
            if not match_exactly or len(idx) == len(target_parnames):
                return (i,), idx
    raise swyft.lightning.utils.SwyftParameterError(
        "Requested parameters not found: %s" % target_parnames
    )


def _collection_mask(coll, mask_fn):
    def mask(item):
        if isinstance(item, list) or isinstance(item, tuple) or isinstance(item, dict):
            return True
        return mask_fn(item)

    if isinstance(coll, list):
        return [_collection_mask(item, mask_fn) for item in coll if mask(item)]
    elif isinstance(coll, tuple):
        return tuple([_collection_mask(item, mask_fn) for item in coll if mask(item)])
    elif isinstance(coll, dict):
        return {
            k: _collection_mask(item, mask_fn) for k, item in coll.items() if mask(item)
        }
    else:
        return coll if mask(coll) else None


def _collection_map(coll, map_fn):
    if isinstance(coll, list):
        return [_collection_map(item, map_fn) for item in coll]
    elif isinstance(coll, tuple):
        return tuple([_collection_map(item, map_fn) for item in coll])
    elif isinstance(coll, dict):
        return {k: _collection_map(item, map_fn) for k, item in coll.items()}
    else:
        return map_fn(coll)


def _collection_flatten(coll, acc=None):
    """Flatten a nested list of collections by returning a list of all nested items."""
    if acc is None:
        acc = []
    if isinstance(coll, list) or isinstance(coll, tuple):
        for v in coll:
            _collection_flatten(v, acc)
    elif isinstance(coll, dict):
        for v in coll.values():
            _collection_flatten(v, acc)
    else:
        acc.append(coll)
    return acc


def _collection_select(coll, err, fn, *args, **kwargs):
    if isinstance(coll, list):
        for item in coll:
            try:
                return _collection_select(item, err, fn, *args, **kwargs)
            except SwyftParameterError:
                pass
    elif isinstance(coll, tuple):
        for item in coll:
            try:
                return _collection_select(item, err, fn, *args, **kwargs)
            except SwyftParameterError:
                pass
    elif isinstance(coll, dict):
        for item in coll.values():
            try:
                return _collection_select(item, err, fn, *args, **kwargs)
            except SwyftParameterError:
                pass
    else:
        try:
            bar = getattr(coll, fn) if fn else coll
            return bar(*args, **kwargs)
        except SwyftParameterError:
            pass
    raise SwyftParameterError(err)


##############
# Transformers
##############


def to_numpy(*args, single_precision=False):
    if len(args) > 1:
        result = []
        for arg in args:
            r = to_numpy(arg, single_precision=single_precision)
            result.append(r)
        return tuple(result)

    x = args[0]

    if isinstance(x, torch.Tensor):
        if not single_precision:
            return x.detach().cpu().numpy()
        else:
            x = x.detach().cpu()
            if x.dtype == torch.float64:
                x = x.float().numpy()
            else:
                x = x.numpy()
            return x
    elif isinstance(x, swyft.Samples):
        return swyft.Samples(
            {k: to_numpy(v, single_precision=single_precision) for k, v in x.items()}
        )
    elif isinstance(x, tuple):
        return tuple(to_numpy(v, single_precision=single_precision) for v in x)
    elif isinstance(x, list):
        return [to_numpy(v, single_precision=single_precision) for v in x]
    elif isinstance(x, dict):
        return {k: to_numpy(v, single_precision=single_precision) for k, v in x.items()}
    elif isinstance(x, np.ndarray):
        if not single_precision:
            return x
        else:
            if x.dtype == np.float64:
                x = np.float32(x)
            return x
    else:
        return x


def to_numpy32(*args):
    return to_numpy(*args, single_precision=True)


def to_torch(x):
    if isinstance(x, swyft.Samples):
        return swyft.Samples({k: to_torch(v) for k, v in x.items()})
    elif isinstance(x, dict):
        return {k: to_torch(v) for k, v in x.items()}
    else:
        return torch.as_tensor(x)


def collate_output(out):
    """Turn list of tensors/arrays-value dicts into dict of collated tensors or arrays"""
    keys = out[0].keys()
    result = {}
    for key in keys:
        if isinstance(out[0][key], torch.Tensor):
            result[key] = torch.stack([x[key] for x in out])
        else:
            result[key] = np.stack([x[key] for x in out])
    return result


############
# Optimizers
############


class AdamW:
    """AdamW with early stopping.

    Attributes:
    - learning_rate (default 1e-3)
    - weight_decay (default 0.01)
    - amsgrad (default False)
    - early_stopping_patience (optional, default 5)
    """

    learning_rate = 1e-3  # Required for learning rate tuning

    def configure_callbacks(self):
        esp = getattr(self, "early_stopping_patience", 5)
        early_stop = EarlyStopping(
            monitor="val_loss", patience=getattr(self, "early_stopping_patience", esp)
        )
        checkpoint = ModelCheckpoint(monitor="val_loss")
        return [early_stop, checkpoint]

    def configure_optimizers(self):
        weight_decay = getattr(self, "weight_decay", 0.01)
        amsgrad = getattr(self, "amsgrad", False)
        optimizer = torch.optim.AdamW(
            self.parameters(),
            lr=self.learning_rate,
            weight_decay=weight_decay,
            amsgrad=amsgrad,
        )
        return dict(optimizer=optimizer)


class AdamWOneCycleLR:
    """AdamW with early stopping and OneCycleLR scheduler.

    Attributes:
    - learning_rate (default 1e-3)
    - early_stopping_patience (optional, default 5)
    """

    learning_rate = 1e-3

    def configure_callbacks(self):
        esp = getattr(self, "early_stopping_patience", 5)
        early_stop = EarlyStopping(
            monitor="val_loss", patience=getattr(self, "early_stopping_patience", esp)
        )
        checkpoint = ModelCheckpoint(monitor="val_loss")
        return [early_stop, checkpoint]

    def configure_optimizers(self):
        optimizer = torch.optim.AdamW(self.parameters(), lr=self.learning_rate)
        total_steps = self.trainer.estimated_stepping_batches
        lr_scheduler = {
            "scheduler": torch.optim.lr_scheduler.OneCycleLR(
                optimizer, max_lr=self.learning_rate, total_steps=total_steps
            )
        }
        return dict(optimizer=optimizer, lr_scheduler=lr_scheduler)


class AdamWReduceLROnPlateau:
    """AdamW with early stopping and ReduceLROnPlateau scheduler.

    Attributes:
    - learning_rate (default 1e-3)
    - early_stopping_patience (optional, default 5)
    - lr_scheduler_factor (optional, default 0.1)
    - lr_scheduler_patience (optional, default 3)
    """

    learning_rate = 1e-3

    def configure_callbacks(self):
        esp = getattr(self, "early_stopping_patience", 5)
        early_stop = EarlyStopping(
            monitor="val_loss", patience=getattr(self, "early_stopping_patience", esp)
        )
        checkpoint = ModelCheckpoint(monitor="val_loss")
        return [early_stop, checkpoint]

    def configure_optimizers(self):
        optimizer = torch.optim.AdamW(self.parameters(), lr=self.learning_rate)
        lrsf = getattr(self, "lr_scheduler_factor", 0.1)
        lrsp = getattr(self, "lr_scheduler_patience", 3)
        lr_scheduler = {
            "scheduler": torch.optim.lr_scheduler.ReduceLROnPlateau(
                optimizer,
                factor=getattr(self, "lr_scheduler_factor", lrsf),
                patience=getattr(self, "lr_scheduler_patience", lrsp),
            ),
            "monitor": "val_loss",
        }
        return dict(optimizer=optimizer, lr_scheduler=lr_scheduler)


class OnFitEndLoadBestModel:
    best_model_path = ""

    def on_fit_end(self):
        self.best_model_path = self.trainer.checkpoint_callback.best_model_path
        checkpoint = torch.load(self.best_model_path)
        print("Reloading best model:", self.best_model_path)
        self.load_state_dict(checkpoint["state_dict"])
