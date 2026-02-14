import numpy as np
from typing import List, Dict, Any, Tuple


def pareto_mask(objective_matrix: np.ndarray, minimize: bool = True) -> np.ndarray:
    """
    Compute a boolean mask for the Pareto front of a set of points in objective space.

    Parameters
    ----------
    objective_matrix : np.ndarray
        Shape (n_candidates, n_objectives). Each row is one candidate,
        each column is one objective.
    minimize : bool, default True
        If True, "better" means lower objective values. If False, invert logic.

    Returns
    -------
    mask : np.ndarray of bool
        mask[i] == True if candidate i is on the Pareto front (non-dominated).
    """
    M = np.array(objective_matrix, dtype=float)
    n, m = M.shape

    if not minimize:
        M = -M  # flip sign so smaller is better

    is_efficient = np.ones(n, dtype=bool)
    for i in range(n):
        if not is_efficient[i]:
            continue
        # Candidate i is efficient only if no other candidate j is strictly better in all objectives
        dominates = np.all(M <= M[i, :], axis=1) & np.any(M < M[i, :], axis=1)
        # Any j that dominates i -> i is not efficient
        if np.any(dominates & (np.arange(n) != i)):
            is_efficient[i] = False

    return is_efficient


def scalarize_objectives(
    objectives: Dict[str, float],
    weights: Dict[str, float],
    minimize: bool = True,
) -> float:
    """
    Convert a multi-objective dictionary into a single scalar score via weighted sum.

    Parameters
    ----------
    objectives : dict
        Mapping objective_name -> value.
    weights : dict
        Mapping objective_name -> weight. Missing keys default to 0.
    minimize : bool, default True
        If True, lower scores are better. If False, invert sign.

    Returns
    -------
    scalar_score : float
        Single scalar used for ranking candidates.
    """
    # Align weights to objectives
    w = []
    v = []
    for k, val in objectives.items():
        v.append(val)
        w.append(weights.get(k, 0.0))
    w = np.array(w, dtype=float)
    v = np.array(v, dtype=float)

    if np.all(w == 0.0):
        w = np.ones_like(v, dtype=float)
    w = w / w.sum()

    score = float(np.dot(w, v))
    if not minimize:
        score = -score
    return score


def rank_candidates_pareto(
    candidates: List[Dict[str, Any]],
    objective_keys: List[str],
    minimize: bool = True,
) -> List[Tuple[int, Dict[str, Any], int]]:
    """
    Rank candidates by Pareto level (non-dominated sorting).

    Parameters
    ----------
    candidates : list of dict
        Each dict represents a candidate solution; must contain the given objective_keys.
    objective_keys : list of str
        Keys to use from each candidate for objectives, e.g. ["error", "complexity"].
    minimize : bool, default True
        If True, lower objective values are better for all objectives.

    Returns
    -------
    ranked : list of (index, candidate_dict, pareto_layer)
        Lower layer index means closer to the Pareto front (layer 0 = front).
    """
    if not candidates:
        return []

    M = []
    for c in candidates:
        M.append([c[k] for k in objective_keys])
    M = np.array(M, dtype=float)

    remaining_indices = np.arange(len(candidates))
    layers = np.full(len(candidates), fill_value=-1, dtype=int)

    current_layer = 0
    while remaining_indices.size > 0:
        mask = pareto_mask(M[remaining_indices], minimize=minimize)
        layer_indices = remaining_indices[mask]
        layers[layer_indices] = current_layer
        remaining_indices = remaining_indices[~mask]
        current_layer += 1

    ranked = []
    for idx, c in enumerate(candidates):
        ranked.append((idx, c, int(layers[idx])))

    # Sort primarily by layer, then by index
    ranked.sort(key=lambda t: (t[2], t[0]))
    return ranked
