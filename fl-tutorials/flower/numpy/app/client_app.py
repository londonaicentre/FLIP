# Copyright (c) 2026 Flower Labs GmbH
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

"""quickstart-numpy: A Flower / NumPy app."""

import numpy as np
from flwr.app import ArrayRecord, Context, Message, MetricRecord, RecordDict
from flwr.clientapp import ClientApp

# Flower ClientApp
app = ClientApp()


@app.train()
def train(msg: Message, context: Context):
    """Train the model on local data."""

    # The model is the global arrays
    ndarrays = msg.content["arrays"].to_numpy_ndarrays()

    # Simulate local training (here we just add random noise to model parameters)
    model = [m + np.random.rand(*m.shape) for m in ndarrays]

    # Construct and return reply Message
    model_record = ArrayRecord(model)
    metrics = {
        "random_metric": np.random.rand(),
        "num-examples": 1,
    }
    metric_record = MetricRecord(metrics)
    content = RecordDict({"arrays": model_record, "metrics": metric_record})
    return Message(content=content, reply_to=msg)


@app.evaluate()
def evaluate(msg: Message, context: Context):
    """Evaluate the model on local data."""

    # The model is the global arrays
    ndarrays = msg.content["arrays"].to_numpy_ndarrays()

    # Return reply Message
    metrics = {
        "random_metric": np.random.rand(3).tolist(),
        "num-examples": 1,
    }
    metric_record = MetricRecord(metrics)
    content = RecordDict({"metrics": metric_record})
    return Message(content=content, reply_to=msg)
