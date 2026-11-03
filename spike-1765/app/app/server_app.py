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

"""Ask every SuperNode on this SuperLink which project it serves, for a few rounds."""

import time

from flwr.app import ConfigRecord, Context, Message, MessageType, RecordDict
from flwr.serverapp import Grid, ServerApp

app = ServerApp()


@app.main()
def main(grid: Grid, context: Context) -> None:
    for rnd in range(1, int(context.run_config["rounds"]) + 1):
        nodes = list(grid.get_node_ids())
        start = time.monotonic()
        messages = [
            Message(RecordDict({"q": ConfigRecord({"round": rnd})}), dst_node_id=n, message_type=MessageType.QUERY)
            for n in nodes
        ]
        for reply in grid.send_and_receive(messages, timeout=300):
            if reply.has_error():
                print(f"SPIKE round={rnd} node={reply.metadata.src_node_id} ERROR {reply.error}")
                continue
            r = reply.content["r"]
            print(
                f"SPIKE round={rnd} node={reply.metadata.src_node_id} project={r['project']} "
                f"key_set={r['key_set']} other_projects_visible={r['other']} rtt={time.monotonic() - start:.2f}s"
            )
