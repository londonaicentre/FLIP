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

import time

import requests


def poll_until(fn, timeout_s=60, interval_s=5, description="condition"):
    """
    Poll fn() until it returns a truthy value, with timeout.

    Args:
        fn: Callable that returns a truthy value when the condition is met.
        timeout_s: Maximum time to wait in seconds.
        interval_s: Time between polls in seconds.
        description: Human-readable description for error messages.

    Returns:
        The truthy return value from fn().

    Raises:
        TimeoutError: If the condition is not met within the timeout.
    """
    deadline = time.time() + timeout_s
    last_exception = None

    while time.time() < deadline:
        try:
            result = fn()
            if result:
                return result
        except Exception as e:
            last_exception = e

        time.sleep(interval_s)

    msg = f"Timed out after {timeout_s}s waiting for: {description}"
    if last_exception:
        msg += f" (last error: {last_exception})"
    raise TimeoutError(msg)


def wait_for_service(url, timeout_s=30, interval_s=2):
    """
    Wait for an HTTP endpoint to return a 2xx status code.

    Args:
        url: The URL to check.
        timeout_s: Maximum time to wait in seconds.
        interval_s: Time between checks in seconds.

    Raises:
        TimeoutError: If the service does not respond within the timeout.
    """

    def check():
        try:
            resp = requests.get(url, timeout=5)
            return resp.status_code < 300
        except requests.ConnectionError:
            return False
        except requests.Timeout:
            return False

    poll_until(check, timeout_s=timeout_s, interval_s=interval_s, description=f"service at {url}")
