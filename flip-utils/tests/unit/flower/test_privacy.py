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
#

"""Tests for flip.flower.privacy — the client-side (epsilon, delta) update filter.

The counterpart of ``tests/unit/nvflare/components/test_custom_percentile_privacy.py``. As there,
the DP maths itself belongs to the framework (Flower's ``compute_clip_model_update`` /
``add_gaussian_noise_inplace``) and is not re-tested here. What is pinned is FLIP's contribution:
the config toggle, the config-driven parameters, the train-only scope, and the integer-buffer
carve-out that keeps the mod from crashing on real BatchNorm models.

Unlike the other ``flip.flower`` test modules, these run against a real ``flwr`` — it is a
flip-utils dev dependency precisely so this module's API assumptions are checked against the
installed Flower rather than against mocks.
"""

import math

import numpy as np
import pytest
from flwr.app import Array, ArrayRecord, ConfigRecord, Context, Error, Message, MetricRecord, RecordDict
from flwr.common import MessageType

from flip.flower.privacy import (
    CONFIG_CLIPPING_NORM,
    CONFIG_DELTA,
    CONFIG_ENABLED,
    CONFIG_EPSILON,
    CONFIG_SENSITIVITY,
    LocalDpConfig,
    flip_local_dp_mod,
)

# A config that leaves the mechanism on but noise-free, so clipping can be asserted exactly.
NOISELESS = {CONFIG_SENSITIVITY: 0.0}


def _context(run_config: dict | None = None) -> Context:
    """A Context carrying only the run_config the mod reads."""
    return Context(
        run_id=1,
        node_id=1,
        node_config={},
        state=RecordDict(),
        run_config=dict(run_config or {}),
    )


def _record(arrays: dict[str, np.ndarray]) -> ArrayRecord:
    """An ArrayRecord from plain ndarrays (the constructor wants Arrays, not raw ndarrays)."""
    return ArrayRecord({name: Array(value) for name, value in arrays.items()})


def _message(arrays: dict[str, np.ndarray], message_type: str = MessageType.TRAIN) -> Message:
    """A ServerApp-style message carrying one ArrayRecord."""
    return Message(
        content=RecordDict({"arrays": _record(arrays)}),
        dst_node_id=1,
        message_type=message_type,
    )


def _reply_with(arrays: dict[str, np.ndarray], msg: Message) -> Message:
    """A ClientApp-style reply carrying one ArrayRecord plus unrelated records."""
    content = RecordDict(
        {
            "arrays": _record(arrays),
            "metrics": MetricRecord({"num-examples": 8}),
            "config": ConfigRecord({"site": "Trust_1"}),
        }
    )
    return Message(content=content, reply_to=msg)


def _call_next(arrays: dict[str, np.ndarray]):
    """A stand-in ClientApp that returns ``arrays`` as its trained update."""

    def call_next(msg: Message, context: Context) -> Message:
        return _reply_with(arrays, msg)

    return call_next


def _arrays_of(msg: Message) -> dict[str, np.ndarray]:
    """The reply's single ArrayRecord as a plain name -> ndarray dict."""
    record = msg.content["arrays"]
    return dict(zip(record.keys(), record.to_numpy_ndarrays(), strict=True))


def _released(
    global_arrays: dict[str, np.ndarray],
    trained: dict[str, np.ndarray],
    run_config: dict | None = None,
) -> dict[str, np.ndarray]:
    """Run the mod over one train round trip and return the arrays it releases."""
    reply = flip_local_dp_mod(_message(global_arrays), _context(run_config), _call_next(trained))
    return _arrays_of(reply)


class TestLocalDpConfig:
    """The config object owns FLIP's defaults, validation, and the noise-scale formula."""

    def test_defaults_are_on_with_utility_first_parameters(self):
        config = LocalDpConfig()
        assert config.enabled is True
        assert config.clipping_norm == 1.0
        assert config.sensitivity == 1e-4
        assert config.epsilon == 10.0
        assert config.delta == 1e-5

    def test_from_run_config_reads_every_key(self):
        config = LocalDpConfig.from_run_config(
            {
                CONFIG_ENABLED: False,
                CONFIG_CLIPPING_NORM: 2.5,
                CONFIG_SENSITIVITY: 0.5,
                CONFIG_EPSILON: 3.0,
                CONFIG_DELTA: 1e-3,
            }
        )
        assert config == LocalDpConfig(
            enabled=False, clipping_norm=2.5, sensitivity=0.5, epsilon=3.0, delta=1e-3
        )

    def test_from_run_config_falls_back_per_key(self):
        """An app that declares only some keys still gets the defaults for the rest — DP stays on."""
        config = LocalDpConfig.from_run_config({CONFIG_EPSILON: 1.0})
        assert config.epsilon == 1.0
        assert config == LocalDpConfig(epsilon=1.0)

    def test_from_run_config_accepts_int_for_float_keys(self):
        """TOML writes `dp-epsilon = 5` as an int; that must not be a configuration error."""
        assert LocalDpConfig.from_run_config({CONFIG_EPSILON: 5}).epsilon == 5.0

    def test_noise_stddev_matches_the_analytic_gaussian_formula(self):
        config = LocalDpConfig(sensitivity=0.4, epsilon=2.0, delta=1e-5)
        expected = 0.4 * math.sqrt(2 * math.log(1.25 / 1e-5)) / 2.0
        assert config.noise_stddev == pytest.approx(expected)

    def test_zero_sensitivity_is_noise_free(self):
        assert LocalDpConfig(sensitivity=0.0).noise_stddev == 0.0

    @pytest.mark.parametrize(
        ("kwargs", "expected_key"),
        [
            ({"clipping_norm": 0.0}, CONFIG_CLIPPING_NORM),
            ({"clipping_norm": -1.0}, CONFIG_CLIPPING_NORM),
            ({"sensitivity": -0.1}, CONFIG_SENSITIVITY),
            ({"epsilon": 0.0}, CONFIG_EPSILON),
            ({"delta": 0.0}, CONFIG_DELTA),
            ({"delta": 1.0}, CONFIG_DELTA),
            ({"clipping_norm": math.nan}, CONFIG_CLIPPING_NORM),
            ({"clipping_norm": math.inf}, CONFIG_CLIPPING_NORM),
            ({"sensitivity": math.nan}, CONFIG_SENSITIVITY),
            ({"sensitivity": math.inf}, CONFIG_SENSITIVITY),
            ({"epsilon": math.nan}, CONFIG_EPSILON),
            ({"epsilon": math.inf}, CONFIG_EPSILON),
            ({"delta": math.nan}, CONFIG_DELTA),
            ({"delta": math.inf}, CONFIG_DELTA),
        ],
    )
    def test_invalid_parameters_are_rejected(self, kwargs, expected_key):
        """epsilon == 0 is rejected here though Flower's LocalDpMod allows it and then divides by it.

        NaN and inf must be rejected too: NaN compares False against every bound, so a plain range
        check would accept `dp-epsilon = nan` from a run config (valid TOML) and poison the noise
        stddev downstream.
        """
        with pytest.raises(ValueError, match=expected_key):
            LocalDpConfig(**kwargs)

    @pytest.mark.parametrize(
        ("run_config", "expected_key"),
        [
            ({CONFIG_ENABLED: "yes"}, CONFIG_ENABLED),
            ({CONFIG_EPSILON: "1.0"}, CONFIG_EPSILON),
            ({CONFIG_CLIPPING_NORM: True}, CONFIG_CLIPPING_NORM),
        ],
    )
    def test_wrong_types_are_rejected(self, run_config, expected_key):
        with pytest.raises(ValueError, match=expected_key):
            LocalDpConfig.from_run_config(run_config)


class TestFlipLocalDpMod:
    """The mod's contract: privatise train updates, leave everything else alone."""

    def test_disabled_returns_the_apps_reply_untouched(self):
        """The off switch mirrors the NVFLARE filter's `off` flag: same app, no component removed."""
        trained = {"w": np.array([9.0, 9.0, 9.0], dtype=np.float32)}

        released = _released({"w": np.array([1.0, 2.0, 3.0], dtype=np.float32)}, trained, {CONFIG_ENABLED: False})

        np.testing.assert_array_equal(released["w"], trained["w"])

    def test_enabled_by_default_privatises_the_update(self):
        """An app declaring no DP keys still gets DP: absence must not mean off."""
        trained = {"w": np.array([9.0, 9.0, 9.0], dtype=np.float32)}

        released = _released({"w": np.array([0.0, 0.0, 0.0], dtype=np.float32)}, trained)

        assert not np.allclose(released["w"], trained["w"])

    def test_evaluate_messages_pass_through(self):
        """Training rounds only — evaluation carries no update to privatise."""
        arrays = {"w": np.array([5.0, 5.0], dtype=np.float32)}
        msg = _message({"w": np.array([1.0, 1.0], dtype=np.float32)}, message_type=MessageType.EVALUATE)

        reply = flip_local_dp_mod(msg, _context(NOISELESS), _call_next(arrays))

        np.testing.assert_array_equal(_arrays_of(reply)["w"], arrays["w"])

    def test_train_message_with_a_custom_action_is_still_privatised(self):
        """Message types are "<category>.<action>"; the scope test must compare the category."""
        global_arrays = {"w": np.array([0.0, 0.0], dtype=np.float32)}
        trained = {"w": np.array([9.0, 9.0], dtype=np.float32)}
        msg = _message(global_arrays, message_type=f"{MessageType.TRAIN}.custom")

        reply = flip_local_dp_mod(msg, _context(NOISELESS), _call_next(trained))

        assert not np.allclose(_arrays_of(reply)["w"], trained["w"])

    def test_error_replies_pass_through(self):
        """A crashed client has no update; the error must reach the server unchanged."""
        msg = _message({"w": np.array([1.0], dtype=np.float32)})
        error_reply = Message(Error(code=1, reason="boom"), reply_to=msg)

        reply = flip_local_dp_mod(msg, _context(), lambda m, c: error_reply)

        assert reply is error_reply

    def test_clips_the_update_to_the_configured_norm(self):
        """A large update is scaled so its L2 norm equals clipping_norm (noise-free config)."""
        global_arrays = {"w": np.zeros(4, dtype=np.float32)}
        trained = {"w": np.array([3.0, 4.0, 0.0, 0.0], dtype=np.float32)}  # update norm 5.0

        released = _released(global_arrays, trained, {**NOISELESS, CONFIG_CLIPPING_NORM: 1.0})

        update = released["w"] - global_arrays["w"]
        assert np.linalg.norm(update) == pytest.approx(1.0, rel=1e-5)
        np.testing.assert_allclose(update, np.array([0.6, 0.8, 0.0, 0.0]), rtol=1e-5)

    def test_small_updates_are_left_unclipped(self):
        """Below the clipping norm the update survives intact — clipping is a ceiling, not a rescale."""
        trained = {"w": np.array([0.03, 0.04], dtype=np.float32)}  # update norm 0.05

        released = _released({"w": np.zeros(2, dtype=np.float32)}, trained, {**NOISELESS, CONFIG_CLIPPING_NORM: 1.0})

        np.testing.assert_allclose(released["w"], trained["w"], rtol=1e-6)

    def test_noise_scale_matches_the_configured_epsilon_delta(self):
        """The released update's deviation tracks LocalDpConfig.noise_stddev."""
        rng = np.random.default_rng(0)
        trained = {"w": rng.normal(0, 1e-6, 20000).astype(np.float32)}
        run_config = {CONFIG_SENSITIVITY: 0.1, CONFIG_EPSILON: 5.0, CONFIG_DELTA: 1e-5}

        released = _released({"w": np.zeros(20000, dtype=np.float32)}, trained, run_config)

        expected = LocalDpConfig.from_run_config(run_config).noise_stddev
        assert np.std(released["w"]) == pytest.approx(expected, rel=0.05)

    def test_integer_buffers_survive_bit_identical(self):
        """Regression: Flower's clip_inputs_inplace raises UFuncTypeError on int arrays.

        ``clip_inputs_inplace`` does ``array *= scaling_factor``, which numpy refuses to write back
        into an int64 array once the factor drops below 1. Real models carry such arrays — MONAI's
        DenseNet121 state dict has 121 int64 ``num_batches_tracked`` counters and the spleen UNet is
        built with ``norm="batch"`` — so a bare LocalDpMod would crash the client on the first round
        where clipping engages. The counters must pass through untouched while the weights are
        privatised.
        """
        global_arrays = {
            "features.norm0.weight": np.zeros(4, dtype=np.float32),
            "features.norm0.num_batches_tracked": np.array(7, dtype=np.int64),
        }
        trained = {
            "features.norm0.weight": np.array([3.0, 4.0, 0.0, 0.0], dtype=np.float32),
            "features.norm0.num_batches_tracked": np.array(19, dtype=np.int64),
        }

        released = _released(global_arrays, trained, {**NOISELESS, CONFIG_CLIPPING_NORM: 1.0})

        assert released["features.norm0.num_batches_tracked"].dtype == np.int64
        assert released["features.norm0.num_batches_tracked"] == 19
        assert np.linalg.norm(released["features.norm0.weight"]) == pytest.approx(1.0, rel=1e-5)

    def test_default_config_preserves_a_realistic_update(self):
        """The shipped defaults must not gut a realistic fine-tuning update.

        The counterpart of the NVFLARE suite's ``test_recipe_default_config_preserves_headonly_update``,
        which pins the config that once discarded 95% of every head diff. Here the risk is the noise
        scale rather than sparsification: with per-step magnitudes as seen in the tutorials, the
        released update must still point the same way as the raw one and stay the same order of
        magnitude, or FedAvg would be averaging noise.
        """
        rng = np.random.default_rng(1)
        raw_update = rng.normal(0, 5e-4, 8192).astype(np.float32)
        global_arrays = {"head.weight": rng.normal(0, 0.02, 8192).astype(np.float32)}
        trained = {"head.weight": (global_arrays["head.weight"] + raw_update).astype(np.float32)}

        released_update = _released(global_arrays, trained)["head.weight"] - global_arrays["head.weight"]
        # Direction survives: the released update still correlates strongly with the real one.
        cosine = float(
            np.dot(released_update, raw_update) / (np.linalg.norm(released_update) * np.linalg.norm(raw_update))
        )
        assert cosine > 0.95
        # Magnitude survives: noise has not swamped the signal.
        assert np.linalg.norm(released_update) == pytest.approx(np.linalg.norm(raw_update), rel=0.2)

    def test_non_array_records_survive_privatisation(self):
        """Only the ArrayRecord is replaced; metrics (num-examples!) and config records must survive."""
        msg = _message({"w": np.array([1.0], dtype=np.float32)})

        reply = flip_local_dp_mod(msg, _context(), _call_next({"w": np.array([2.0], dtype=np.float32)}))

        assert reply.content["metrics"]["num-examples"] == 8
        assert reply.content["config"]["site"] == "Trust_1"

    def test_clipping_is_joint_across_float_arrays(self):
        """The update is clipped to ONE L2 norm over all float arrays, with counters interleaved.

        Per-array clipping would change the sensitivity bound (total norm sqrt(k), not C) and
        silently invalidate the (epsilon, delta) accounting; a counter between the floats also
        pins the index alignment between ``float_indices`` and the baselines.
        """
        global_arrays = {
            "a": np.zeros(2, dtype=np.float32),
            "counter": np.array(7, dtype=np.int64),
            "b": np.zeros(2, dtype=np.float32),
        }
        trained = {
            "a": np.array([3.0, 0.0], dtype=np.float32),
            "counter": np.array(9, dtype=np.int64),
            "b": np.array([0.0, 4.0], dtype=np.float32),
        }  # joint update norm 5.0

        released = _released(global_arrays, trained, {**NOISELESS, CONFIG_CLIPPING_NORM: 1.0})

        joint = np.concatenate([released["a"], released["b"]])
        assert np.linalg.norm(joint) == pytest.approx(1.0, rel=1e-5)
        np.testing.assert_allclose(released["a"], [0.6, 0.0], rtol=1e-5)
        np.testing.assert_allclose(released["b"], [0.0, 0.8], rtol=1e-5)
        assert released["counter"] == 9

    def test_rejects_a_reply_with_the_same_keys_in_a_different_order(self):
        """Alignment to the global model is positional; set-equality would difference a against b."""
        msg = _message({"a": np.array([1.0], dtype=np.float32), "b": np.array([2.0], dtype=np.float32)})
        reordered = {"b": np.array([2.0], dtype=np.float32), "a": np.array([1.0], dtype=np.float32)}

        with pytest.raises(ValueError, match="same keys"):
            flip_local_dp_mod(msg, _context(), _call_next(reordered))

    def test_malformed_message_fails_before_training_runs(self):
        """The docstring's promise: a malformed message must not cost a training epoch."""
        content = RecordDict(
            {
                "arrays": _record({"w": np.array([1.0], dtype=np.float32)}),
                "extra": _record({"w": np.array([1.0], dtype=np.float32)}),
            }
        )
        msg = Message(content=content, dst_node_id=1, message_type=MessageType.TRAIN)
        trained = False

        def call_next(m: Message, c: Context) -> Message:
            nonlocal trained
            trained = True
            return _reply_with({"w": np.array([1.0], dtype=np.float32)}, m)

        with pytest.raises(ValueError, match="message from the ServerApp"):
            flip_local_dp_mod(msg, _context(), call_next)
        assert not trained

    def test_rejects_an_update_carrying_unclassifiable_dtypes(self):
        """A bool mask (or complex weights) may be learned; only integer counters may pass raw."""
        global_arrays = {"w": np.array([0.0], dtype=np.float32), "mask": np.array([True, False])}
        trained = {"w": np.array([1.0], dtype=np.float32), "mask": np.array([True, True])}

        with pytest.raises(ValueError, match="cannot classify"):
            _released(global_arrays, trained)

    def test_rejects_a_reply_carrying_several_array_records(self):
        """Ambiguity must fail loudly — silently skipping DP would release a raw update."""
        msg = _message({"w": np.array([1.0], dtype=np.float32)})

        def call_next(m: Message, c: Context) -> Message:
            content = RecordDict(
                {
                    "arrays": _record({"w": np.array([2.0], dtype=np.float32)}),
                    "extra": _record({"w": np.array([3.0], dtype=np.float32)}),
                }
            )
            return Message(content=content, reply_to=m)

        with pytest.raises(ValueError, match="exactly one ArrayRecord"):
            flip_local_dp_mod(msg, _context(), call_next)

    def test_rejects_a_reply_whose_keys_do_not_match(self):
        """Misaligned keys mean the update cannot be differenced against the global model."""
        msg = _message({"w": np.array([1.0], dtype=np.float32)})

        with pytest.raises(ValueError, match="same keys"):
            flip_local_dp_mod(msg, _context(), _call_next({"other": np.array([2.0], dtype=np.float32)}))

    def test_rejects_an_update_with_no_floating_point_arrays(self):
        """Nothing to privatise is not the same as nothing to do."""
        msg = _message({"counter": np.array(1, dtype=np.int64)})

        with pytest.raises(ValueError, match="no floating-point arrays"):
            flip_local_dp_mod(msg, _context(), _call_next({"counter": np.array(2, dtype=np.int64)}))
