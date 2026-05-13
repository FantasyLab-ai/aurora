"""
Hidden Markov Models — Wave H, Method 4.

Pure-numpy Gaussian HMM:
* Forward-backward (α/β recursion) for likelihood + state posteriors.
* Baum-Welch (EM) for parameter estimation.
* Viterbi for the most-likely state sequence.
* BIC across a range of state counts to pick K.

For Aurora's typical needs (regime inference on univariate or low-d
time series with K ≤ 6 states), pure numpy is fast enough that we don't
need hmmlearn.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]


def _gaussian_log_pdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return -np.inf
    return -0.5 * math.log(2 * math.pi * sigma ** 2) \
            - (x - mu) ** 2 / (2 * sigma ** 2)


def _emission_logprobs(y: "np.ndarray", means: "np.ndarray",
                         sigmas: "np.ndarray") -> "np.ndarray":
    """log p(y_t | s_t = k) for each (t, k). Univariate Gaussian."""
    T = y.shape[0]; K = means.shape[0]
    log_b = np.empty((T, K))
    for k in range(K):
        log_b[:, k] = -0.5 * np.log(2 * math.pi * sigmas[k] ** 2) \
                       - (y - means[k]) ** 2 / (2 * sigmas[k] ** 2)
    return log_b


def _logsumexp(a: "np.ndarray", axis: Optional[int] = None) -> "np.ndarray":
    a_max = np.max(a, axis=axis, keepdims=True)
    out = a_max + np.log(np.sum(np.exp(a - a_max), axis=axis, keepdims=True))
    return out.squeeze(axis=axis) if axis is not None else out.squeeze()


def forward_backward(
    y: Sequence[float],
    pi: "np.ndarray",
    A: "np.ndarray",
    means: "np.ndarray",
    sigmas: "np.ndarray",
) -> Dict[str, Any]:
    """Forward-backward algorithm in log space.

    Returns log-likelihood + per-step state posteriors γ_t(k).
    """
    if np is None:
        raise RuntimeError("numpy required")
    y_arr = np.asarray(y, dtype=float)
    T = y_arr.shape[0]
    K = pi.shape[0]
    log_pi = np.log(np.maximum(pi, 1e-300))
    log_A  = np.log(np.maximum(A, 1e-300))
    log_b  = _emission_logprobs(y_arr, means, sigmas)
    log_alpha = np.empty((T, K))
    log_beta  = np.empty((T, K))
    log_alpha[0] = log_pi + log_b[0]
    for t in range(1, T):
        for k in range(K):
            log_alpha[t, k] = _logsumexp(log_alpha[t - 1] + log_A[:, k]) \
                                + log_b[t, k]
    log_beta[T - 1] = 0.0
    for t in range(T - 2, -1, -1):
        for k in range(K):
            log_beta[t, k] = _logsumexp(log_A[k, :] + log_b[t + 1] +
                                            log_beta[t + 1])
    log_gamma = log_alpha + log_beta
    log_gamma -= _logsumexp(log_gamma, axis=1)[:, None]
    gamma = np.exp(log_gamma)
    log_lik = float(_logsumexp(log_alpha[T - 1]))
    return {"log_alpha": log_alpha, "log_beta": log_beta,
             "gamma": gamma, "log_likelihood": log_lik}


def viterbi(
    y: Sequence[float],
    pi: "np.ndarray",
    A: "np.ndarray",
    means: "np.ndarray",
    sigmas: "np.ndarray",
) -> Tuple["np.ndarray", float]:
    """Viterbi most-likely state sequence."""
    y_arr = np.asarray(y, dtype=float)
    T = y_arr.shape[0]; K = pi.shape[0]
    log_pi = np.log(np.maximum(pi, 1e-300))
    log_A = np.log(np.maximum(A, 1e-300))
    log_b = _emission_logprobs(y_arr, means, sigmas)
    delta = np.empty((T, K))
    psi = np.zeros((T, K), dtype=int)
    delta[0] = log_pi + log_b[0]
    for t in range(1, T):
        for k in range(K):
            scores = delta[t - 1] + log_A[:, k]
            psi[t, k] = int(np.argmax(scores))
            delta[t, k] = float(scores[psi[t, k]] + log_b[t, k])
    states = np.empty(T, dtype=int)
    states[-1] = int(np.argmax(delta[-1]))
    for t in range(T - 2, -1, -1):
        states[t] = psi[t + 1, states[t + 1]]
    return states, float(delta[-1].max())


def baum_welch(
    y: Sequence[float],
    K: int,
    *,
    max_iter: int = 50,
    tol: float = 1e-4,
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """EM for Gaussian-HMM parameters."""
    if np is None:
        raise RuntimeError("numpy required")
    y_arr = np.asarray(y, dtype=float)
    T = y_arr.shape[0]
    if T < K * 5:
        return {"available": False,
                "skipped_reason": f"need_at_least_{K * 5}_obs"}
    rng = np.random.default_rng(seed)
    # Initialise: spread means across the data range, equal sigmas.
    sorted_y = np.sort(y_arr)
    quantiles = np.linspace(0.05, 0.95, K)
    means = np.array([sorted_y[int(q * (T - 1))] for q in quantiles])
    sigmas = np.full(K, float(np.std(y_arr)) + 1e-3)
    pi = np.full(K, 1.0 / K)
    A = np.full((K, K), 0.1 / max(1, K - 1))
    np.fill_diagonal(A, 0.9)
    last_ll = -np.inf
    for it in range(max_iter):
        fb = forward_backward(y_arr, pi, A, means, sigmas)
        log_alpha = fb["log_alpha"]; log_beta = fb["log_beta"]
        gamma = fb["gamma"]; log_lik = fb["log_likelihood"]
        # ξ_t(i, j) = α_t(i) A_ij b_j(y_{t+1}) β_{t+1}(j) / Σ
        log_b = _emission_logprobs(y_arr, means, sigmas)
        log_A = np.log(np.maximum(A, 1e-300))
        log_xi = (log_alpha[:-1, :, None] + log_A[None, :, :]
                    + log_b[1:, None, :] + log_beta[1:, None, :])
        log_xi -= _logsumexp(log_xi.reshape(T - 1, -1), axis=1)[:, None, None]
        xi_sum = np.exp(log_xi).sum(axis=0)
        # M-step.
        pi = gamma[0] / max(1e-12, gamma[0].sum())
        A = xi_sum / np.maximum(1e-12, xi_sum.sum(axis=1, keepdims=True))
        gamma_sum = gamma.sum(axis=0)
        means = (gamma * y_arr[:, None]).sum(axis=0) / np.maximum(1e-12, gamma_sum)
        sigmas = np.sqrt(np.maximum(1e-6,
                            (gamma * (y_arr[:, None] - means) ** 2).sum(axis=0)
                            / np.maximum(1e-12, gamma_sum)))
        if abs(log_lik - last_ll) < tol:
            break
        last_ll = log_lik
    n_params = K - 1 + K * (K - 1) + 2 * K  # π + A + (μ, σ) per state
    bic = -2 * last_ll + n_params * math.log(T)
    return {
        "available": True,
        "K": K,
        "pi": pi.tolist(),
        "A": A.tolist(),
        "means": means.tolist(),
        "sigmas": sigmas.tolist(),
        "log_likelihood": last_ll,
        "bic": float(bic),
        "n_iter": it + 1,
        "method": "baum_welch_gaussian_hmm",
    }


def fit_hmm_select_k(
    y: Sequence[float],
    *,
    k_range: Sequence[int] = (2, 3, 4, 5),
    seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Fit Gaussian HMM for each K in ``k_range``; pick the K with
    smallest BIC. Returns the best fit + BIC profile + Viterbi path
    + per-state expected dwell time.
    """
    if np is None:
        raise RuntimeError("numpy required")
    y_arr = np.asarray(y, dtype=float)
    profiles: List[Dict[str, Any]] = []
    best_K = None; best_bic = np.inf; best_fit = None
    for K in k_range:
        fit = baum_welch(y_arr, K, seed=seed)
        if not fit.get("available"):
            continue
        profiles.append({"K": K, "bic": fit["bic"],
                          "log_lik": fit["log_likelihood"]})
        if fit["bic"] < best_bic:
            best_bic = fit["bic"]; best_K = K; best_fit = fit
    if best_fit is None:
        return {"available": False, "skipped_reason": "no_fit_succeeded"}
    pi = np.array(best_fit["pi"])
    A = np.array(best_fit["A"])
    means = np.array(best_fit["means"])
    sigmas = np.array(best_fit["sigmas"])
    states, _ = viterbi(y_arr, pi, A, means, sigmas)
    # Expected dwell time of state k = 1 / (1 - A_kk).
    dwell = [1.0 / max(1e-9, 1 - A[k, k]) for k in range(best_K)]
    # Current state probability (last forward).
    fb = forward_backward(y_arr, pi, A, means, sigmas)
    final_post = fb["gamma"][-1].tolist()
    # Order states by emission mean for human readability.
    order = np.argsort(means)
    inv = np.argsort(order)
    relabelled = inv[states].tolist()
    return {
        "available": True,
        "best_K": best_K,
        "K_profile": profiles,
        "pi": [float(pi[order[k]]) for k in range(best_K)],
        "A": [[float(A[order[i], order[j]]) for j in range(best_K)]
                for i in range(best_K)],
        "means_sorted": [float(means[order[k]]) for k in range(best_K)],
        "sigmas_sorted": [float(sigmas[order[k]]) for k in range(best_K)],
        "viterbi_states": relabelled,
        "expected_dwell_time": [dwell[order[k]] for k in range(best_K)],
        "current_state_posterior":
            [float(final_post[order[k]]) for k in range(best_K)],
        "current_state":
            int(np.argmax([final_post[order[k]] for k in range(best_K)])),
        "log_likelihood": best_fit["log_likelihood"],
        "bic": best_fit["bic"],
        "n_obs": int(len(y_arr)),
        "method": "gaussian_hmm_bic_selection",
    }
