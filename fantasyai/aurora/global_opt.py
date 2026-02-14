# fantasyai/aurora/global_opt.py
import numpy as np
from scipy.optimize import minimize

def multi_start_optimize(func, x0_list, bounds):
    results = []
    for x0 in x0_list:
        res = minimize(func, x0, bounds=bounds)
        results.append(res)
    best = min(results, key=lambda r: r.fun)
    return {"best_value": best.fun, "best_x": best.x, "all_runs": results}

def pareto_front(points):
    """Simple 2D Pareto front extractor."""
    front = []
    for p in points:
        dominated = False
        for q in points:
            if (q[0] <= p[0] and q[1] <= p[1]) and (q != p):
                dominated = True
                break
        if not dominated:
            front.append(p)
    return front
