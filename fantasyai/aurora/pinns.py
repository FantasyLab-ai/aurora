# fantasyai/aurora/pinns.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional

try:
    import torch
    import torch.nn as nn
except ImportError:  # pragma: no cover
    torch = None     # type: ignore
    nn = None        # type: ignore


@dataclass
class PINNResult:
    success: bool
    model_state_dict: Dict[str, Any] | None
    diagnostics: Dict[str, Any]


class SimplePINN(nn.Module):  # type: ignore[misc]
    """
    Very small fully-connected network for PINN experiments.
    Assumes input is 1D or low-D vector and output is 1D.
    """

    def __init__(self, in_dim: int = 1, hidden: int = 64, out_dim: int = 1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
            nn.Linear(hidden, out_dim),
        )

    def forward(self, x):
        return self.net(x)


def train_pinn_1d(
    pde_residual_func: Callable[[torch.Tensor, torch.Tensor], torch.Tensor],
    x_collocation: "torch.Tensor",
    *,
    num_epochs: int = 2000,
    lr: float = 1e-3,
    device: str = "cpu",
) -> PINNResult:
    """
    Minimal 1D PINN trainer.

    pde_residual_func(x, u(x)) should return residual tensor.
    x_collocation: points where PDE residual is enforced.
    """

    if torch is None:
        raise RuntimeError("torch is not installed; cannot train PINNs.")

    model = SimplePINN(in_dim=x_collocation.shape[1], out_dim=1).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    x_collocation = x_collocation.to(device)

    for epoch in range(num_epochs):
        opt.zero_grad()
        u_pred = model(x_collocation)  # shape: (N, 1)
        residual = pde_residual_func(x_collocation, u_pred)
        loss = (residual**2).mean()
        loss.backward()
        opt.step()

        if (epoch + 1) % max(1, num_epochs // 10) == 0:
            print(f"[PINN] Epoch {epoch+1}/{num_epochs} loss={loss.item():.6f}")

    return PINNResult(
        success=True,
        model_state_dict=model.state_dict(),
        diagnostics={
            "final_loss": float(loss.item()),
            "num_epochs": num_epochs,
        },
    )
