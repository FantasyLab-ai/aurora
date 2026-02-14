# fantasyai/aurora/bayes.py

import numpy as np
from dataclasses import dataclass

@dataclass
class NormalPosterior:
    mean: float
    std: float
    n: int

def normal_update(prior_mean, prior_std, observed, obs_std):
    """
    Bayesian update for normal distribution with known variance.
    Returns NormalPosterior.
    """
    prior_var = prior_std ** 2
    obs_var = obs_std ** 2

    post_var = 1.0 / (1.0/prior_var + 1.0/obs_var)
    post_mean = post_var * (prior_mean/prior_var + observed/obs_var)

    return NormalPosterior(mean=post_mean, std=np.sqrt(post_var), n=1)
