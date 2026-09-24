# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The autoencoder's optimiser bookkeeping, which fails silently in both directions.

Adversarial training with gradient accumulation has three ways to go wrong that no exception and no
loss curve will tell you about — the run simply trains at a learning rate you did not choose, or
trains one of the two networks on a fraction of the data:

1. **Summed instead of averaged micro-batches.** ``.grad`` accumulates, so omitting the
   ``/ accumulation_step`` makes every step ``accumulation_step`` times larger than the configured
   learning rate implies — 4x at ``BATCH_SIZE: 2``. Nothing looks wrong; ``LR_G`` just stops meaning
   what it says, and stops being comparable to any published recipe.
2. **Zeroing a gradient that is still being accumulated.** Calling ``zero_grad`` once per iteration
   while stepping once per *accumulation window* leaves only the final micro-batch's gradient to be
   stepped on. That network then trains at the raw batch size on ``1/accumulation_step`` of the data.
3. **Letting the generator's objective update the discriminator.** The discriminator is inside the
   generator's graph, so a generator backward deposits gradients on the discriminator's parameters —
   gradients that want the discriminator to be *fooled*. They must be kept off it, and the way to do
   that without destroying its own accumulation is to freeze its parameters for that pass.

The first part of this file pins the arithmetic with a runnable example; the second asserts the
trainer's source actually implements it.
"""

from __future__ import annotations

import ast

import torch
from torch import nn
from tutorial_apps import TUTORIALS_ROOT

TRAINER = TUTORIALS_ROOT / "nvflare/image_synthesis/autoencoder/app_files/trainer.py"
ACCUMULATION = 4  # what batch_accumulation_step gives for the shipped BATCH_SIZE of 2


def _step_norm(*, divide: bool, zero_every_iteration: bool) -> torch.Tensor:
    """Gradient norm at step time, under a given accumulation discipline."""
    torch.manual_seed(0)
    net = nn.Linear(4, 1, bias=False)
    optimiser = torch.optim.SGD(net.parameters(), lr=0.0)
    batches = [torch.randn(2, 4, generator=torch.Generator().manual_seed(i)) for i in range(ACCUMULATION)]
    grad = torch.zeros(1)
    for index, batch in enumerate(batches):
        if zero_every_iteration:
            optimiser.zero_grad(set_to_none=True)
        loss = net(batch).pow(2).mean()
        (loss / ACCUMULATION if divide else loss).backward()
        if (index + 1) % ACCUMULATION == 0:
            grad = net.weight.grad.clone()
            optimiser.step()
            optimiser.zero_grad(set_to_none=True)
    return grad


def test_summing_micro_batches_inflates_the_step() -> None:
    """Omitting the divisor multiplies the step by ``accumulation_step`` — the bug, demonstrated."""
    averaged = _step_norm(divide=True, zero_every_iteration=False).norm()
    summed = _step_norm(divide=False, zero_every_iteration=False).norm()
    assert summed / averaged == torch.tensor(float(ACCUMULATION))


def test_zeroing_every_iteration_discards_the_accumulation() -> None:
    """Zeroing per iteration but stepping per window keeps only the last micro-batch."""
    accumulated = _step_norm(divide=True, zero_every_iteration=False)
    zeroed = _step_norm(divide=True, zero_every_iteration=True)
    assert not torch.allclose(accumulated, zeroed)


def _calls(tree: ast.AST, dotted: str) -> list[ast.Call]:
    """Every call whose callee spells ``dotted`` (e.g. ``scaler_g.scale``)."""
    wanted = dotted.split(".")[-1]
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == wanted
    ]


def _divides_by_accumulation(call: ast.Call) -> bool:
    """True when the scaled loss is divided by ``accumulation_step``."""
    if not call.args:
        return False
    arg = call.args[0]
    return (
        isinstance(arg, ast.BinOp)
        and isinstance(arg.op, ast.Div)
        and isinstance(arg.right, ast.Name)
        and arg.right.id == "accumulation_step"
    )


def test_both_scaled_losses_are_averaged_over_the_accumulation_window() -> None:
    """``scaler_g.scale`` and ``scaler_d.scale`` both divide by ``accumulation_step``."""
    tree = ast.parse(TRAINER.read_text())
    scaled = [call for call in _calls(tree, "scale") if call.args]
    assert len(scaled) == 2, "expected one scaled loss for the generator and one for the discriminator"
    for call in scaled:
        assert _divides_by_accumulation(call), ast.unparse(call)


def test_every_zero_grad_sits_inside_a_step_branch() -> None:
    """No optimiser is zeroed unconditionally each iteration.

    An unconditional ``zero_grad`` in the loop body is exactly failure mode 2 — and it reads as
    completely ordinary, which is why it needs a test rather than a reviewer.
    """
    tree = ast.parse(TRAINER.read_text())
    train = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "train"
    )
    guarded = {
        id(call)
        for branch in ast.walk(train)
        if isinstance(branch, ast.If)
        for call in _calls(branch, "zero_grad")
    }
    for call in _calls(train, "zero_grad"):
        assert id(call) in guarded, f"unguarded zero_grad at line {call.lineno} discards the accumulation"


def test_the_discriminator_is_frozen_for_the_generator_pass() -> None:
    """``requires_grad_(False)`` then ``(True)`` bracket the generator's backward.

    Without the freeze the generator's backward deposits gradients on the discriminator that want it
    to lose, and the only way the old code kept them off was the per-iteration ``zero_grad`` that
    destroyed accumulation. The two fixes are therefore one fix.
    """
    tree = ast.parse(TRAINER.read_text())
    toggles = [
        (call.lineno, call.args[0].value)
        for call in _calls(tree, "requires_grad_")
        if call.args and isinstance(call.args[0], ast.Constant)
    ]
    assert [value for _, value in toggles] == [False, True], toggles

    backward = next(
        call.lineno
        for call in _calls(tree, "backward")
        if isinstance(call.func.value, ast.Call) and _divides_by_accumulation(call.func.value)
    )
    freeze, unfreeze = toggles[0][0], toggles[1][0]
    assert freeze < backward < unfreeze, (freeze, backward, unfreeze)
