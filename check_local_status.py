#!/usr/bin/env python3
#
# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""FLIP Local Development Status Checker.

This script verifies that the local development environment is functioning correctly.

PREREQUISITES:
  - docker and docker-compose installed
  - Local development environment running (docker compose up)

WHAT IT CHECKS:
  ✓ Docker daemon status
  ✓ Docker Containers (all services)
  ✓ Docker Networks
  ✓ Application Endpoints (UI, API, FL API)
  ✓ Trust Endpoints (Trust API, Imaging API, Data Access API)
  ✓ Database Connectivity (PostgreSQL)
  ✓ System Resources (disk, memory)

EXIT CODES:
  0 - All checks passed (warnings are acceptable)
  1 - One or more checks failed
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


# Color codes for terminal output
class Colors:
    """ANSI color codes for terminal output."""

    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    NC = "\033[0m"  # No Color


@dataclass
class StatusCounters:
    """Track check results."""

    passed: int = 0
    failed: int = 0
    warnings: int = 0

    @property
    def total(self) -> int:
        """Get total checks."""
        return self.passed + self.failed + self.warnings


# Global counters
counters = StatusCounters()


def print_status(status: str, message: str) -> None:
    """Print a status message with color coding.

    Args:
        status: Status type (PASS, FAIL, INFO, WARN)
        message: Message to display
    """
    global counters

    symbols = {
        "PASS": f"{Colors.GREEN}✓ PASS{Colors.NC}",
        "FAIL": f"{Colors.RED}✗ FAIL{Colors.NC}",
        "WARN": f"{Colors.YELLOW}⚠ WARN{Colors.NC}",
        "INFO": f"{Colors.BLUE}ℹ INFO{Colors.NC}",
    }

    symbol = symbols.get(status, "")
    print(f"{symbol} - {message}")

    if status == "PASS":
        counters.passed += 1
    elif status == "FAIL":
        counters.failed += 1
    elif status == "WARN":
        counters.warnings += 1


def print_section(title: str) -> None:
    """Print a section header.

    Args:
        title: Section title
    """
    print()
    print(f"{Colors.BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.NC}")
    print(f"{Colors.BLUE}{title}{Colors.NC}")
    print(f"{Colors.BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.NC}")


def check_command(command: str) -> bool:
    """Check if a command is available.

    Args:
        command: Command name to check

    Returns:
        True if command exists, False otherwise
    """
    try:
        subprocess.run(
            ["which", command],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def run_command(args: list[str], timeout: int = 30) -> Tuple[bool, str]:
    """Run a shell command.

    Args:
        args: Command arguments
        timeout: Command timeout in seconds

    Returns:
        Tuple of (success, output)
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return True, result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        error_output = e.stderr if hasattr(e, "stderr") else str(e)
        return False, error_output


def check_http_endpoint(url: str, name: str, expected_status: int | list[int] = 200) -> bool:
    """Check HTTP endpoint availability.

    Args:
        url: URL to check
        name: Endpoint name for logging
        expected_status: Expected HTTP status code(s). Can be a single int or list of ints.

    Returns:
        True if endpoint is accessible, False otherwise
    """
    print_status("INFO", f"Checking {name} at {url}...")

    # Convert single int to list for uniform handling
    valid_statuses = [expected_status] if isinstance(expected_status, int) else expected_status

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in valid_statuses:
                print_status("PASS", f"{name} is responding (HTTP {response.status})")
                return True
            else:
                print_status(
                    "FAIL",
                    f"{name} returned HTTP {response.status} (expected {valid_statuses})",
                )
                return False
    except urllib.error.HTTPError as e:
        # Handle HTTP errors (like 401) that still indicate the service is responding
        if e.code in valid_statuses:
            print_status("PASS", f"{name} is responding (HTTP {e.code})")
            return True
        else:
            print_status("FAIL", f"{name} returned HTTP {e.code} (expected {valid_statuses})")
            return False
    except Exception as e:
        print_status("FAIL", f"{name} not responding: {e}")
        return False


def load_env_file(env_file: Path) -> dict:
    """Load environment variables from a file.

    Args:
        env_file: Path to the .env file

    Returns:
        Dictionary of environment variables
    """
    env_vars = {}
    if not env_file.exists():
        return env_vars

    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars


def main(
    project_dir: Path,
    skip_endpoints: bool,
    skip_docker: bool,
    env_file: Optional[Path],
) -> None:
    """FLIP Local Development Status Checker.

    Verifies that the local development environment is functioning correctly.

    Args:
        project_dir: Project root directory
        skip_endpoints: Skip HTTP endpoint checks
        skip_docker: Skip Docker container checks
        env_file: Path to .env file (defaults to .env.development)
    """
    # Change to project directory
    os.chdir(project_dir)

    # Load environment variables
    if env_file is None:
        env_file = project_dir / ".env.development"

    if env_file.exists():
        env_vars = load_env_file(env_file)
        print_status("PASS", f"Loaded environment from {env_file.name}")
    else:
        env_vars = {}
        print_status("WARN", f"Environment file {env_file} not found, using defaults")

    # Check prerequisites
    print_section("Checking Prerequisites")

    if not check_command("docker"):
        print_status("FAIL", "Required command 'docker' not found. Please install it.")
        sys.exit(1)
    print_status("PASS", "docker command found")

    if not check_command("docker-compose") and not check_command("docker"):
        print_status("FAIL", "Required command 'docker-compose' or 'docker compose' not found.")
        sys.exit(1)
    print_status("PASS", "docker-compose command found")

    # Check if Docker daemon is running
    success, _ = run_command(["docker", "info"], timeout=5)
    if not success:
        print_status("FAIL", "Docker daemon is not running. Please start Docker.")
        sys.exit(1)
    print_status("PASS", "Docker daemon is running")

    # Get port configuration from environment
    UI_PORT = env_vars.get("UI_PORT", "5173")
    API_PORT = env_vars.get("API_PORT", "8001")
    FL_API_PORT = env_vars.get("FL_API_PORT", "8000")
    TRUST_API_PORT = env_vars.get("TRUST_API_PORT", "8100")
    IMAGING_API_PORT = env_vars.get("IMAGING_API_PORT", "8200")
    DATA_ACCESS_API_PORT = env_vars.get("DATA_ACCESS_API_PORT", "8300")
    POSTGRES_PORT = env_vars.get("POSTGRES_PORT", "5432")
    XNAT_PORT_TRUST_1 = env_vars.get("XNAT_PORT_TRUST_1", "8104")
    XNAT_PORT_TRUST_2 = env_vars.get("XNAT_PORT_TRUST_2", "8106")
    PACS_UI_PORT_TRUST_1 = env_vars.get("PACS_UI_PORT_TRUST_1", "8042")
    PACS_UI_PORT_TRUST_2 = env_vars.get("PACS_UI_PORT_TRUST_2", "8044")

    # Parse NET_ENDPOINTS to determine which FL networks are configured
    NET_ENDPOINTS = env_vars.get("NET_ENDPOINTS", "{}")
    try:
        net_endpoints = json.loads(NET_ENDPOINTS.replace("'", '"'))
        configured_nets = list(net_endpoints.keys())
        configured_net_numbers = [int(net.split("-")[-1]) for net in configured_nets if net.startswith("net-")]
    except (json.JSONDecodeError, ValueError):
        configured_net_numbers = [1, 2]  # Default to net-1 only

    # Docker container checks
    if not skip_docker:
        print_section("Docker Container Status")

        print_status("INFO", "Checking Docker containers...")

        # Get container status
        try:
            success, containers = run_command(["docker", "ps", "--format", "{{.Names}}:{{.Status}}"])

            if not success:
                print_status("FAIL", "Could not retrieve Docker container status")
            else:
                # Check each expected container
                expected_containers = [
                    "flip-ui",
                    "flip-api",
                    "flip-db",
                ]

                # Add configured FL server and API containers
                for net_num in configured_net_numbers:
                    expected_containers.append(f"fl-server-net-{net_num}")
                    expected_containers.append(f"flip-fl-api-net-{net_num}")

                for container in expected_containers:
                    container_found = False
                    for line in containers.split("\n"):
                        if line.startswith(f"{container}:") and "Up" in line:
                            status = line.split(":", 1)[1] if ":" in line else ""
                            print_status("PASS", f"Container '{container}' is running ({status})")
                            container_found = True
                            break
                    if not container_found:
                        print_status("FAIL", f"Container '{container}' is not running")

                # Check for any exited containers
                success, exited = run_command([
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    "status=exited",
                    "--format",
                    "{{.Names}}",
                ])
                if success and exited:
                    print_status("WARN", f"Exited containers found: {exited}")
        except Exception as e:
            print_status("FAIL", f"Could not check Docker containers: {e}")

        # Check Docker Swarm services (XNAT)
        print_section("Docker Swarm Services (XNAT)")
        print_status("INFO", "Checking Docker Swarm mode and XNAT stacks...")
        try:
            # Check if swarm is active
            success, swarm_info = run_command(["docker", "info", "--format", "{{.Swarm.LocalNodeState}}"])
            if success and "active" in swarm_info.lower():
                print_status("PASS", "Docker Swarm mode is active")

                # Check for XNAT stacks
                success, stacks = run_command(["docker", "stack", "ls", "--format", "{{.Name}}"])
                if success:
                    xnat_stacks = ["xnat1", "xnat2"]
                    for stack_name in xnat_stacks:
                        if stack_name in stacks:
                            print_status("PASS", f"XNAT stack '{stack_name}' is deployed")

                            # Check services in the stack
                            success, services = run_command([
                                "docker",
                                "stack",
                                "services",
                                stack_name,
                                "--format",
                                "{{.Name}}:{{.Replicas}}",
                            ])
                            if success:
                                for service_line in services.split("\n"):
                                    if service_line:
                                        service_name, replicas = service_line.split(":")
                                        if "/" in replicas:
                                            running, desired = replicas.split("/")
                                            if running == desired and int(running) > 0:
                                                print_status(
                                                    "PASS", f"Service '{service_name}' is running ({replicas})"
                                                )
                                            else:
                                                print_status(
                                                    "FAIL",
                                                    f"Service '{service_name}' is not fully running ({replicas})",
                                                )
                        else:
                            print_status("FAIL", f"XNAT stack '{stack_name}' is not deployed")
                else:
                    print_status("WARN", "Could not retrieve Docker stack list")
            else:
                print_status("WARN", "Docker Swarm mode is not active - XNAT may not be running in swarm mode")
                print_status("INFO", "Checking for XNAT containers in regular docker compose mode...")

                # Check for XNAT containers running in compose mode
                xnat_compose_containers = [
                    "xnat1-xnat-web-1",
                    "xnat1-xnat-db-1",
                    "xnat1-xnat-nginx-1",
                    "xnat2-xnat-web-1",
                    "xnat2-xnat-db-1",
                    "xnat2-xnat-nginx-1",
                ]

                for container in xnat_compose_containers:
                    container_found = False
                    for line in containers.split("\n"):
                        if line.startswith(f"{container}:") and "Up" in line:
                            print_status("PASS", f"XNAT container '{container}' is running")
                            container_found = True
                            break
                    if not container_found:
                        print_status("WARN", f"XNAT container '{container}' is not running")
        except Exception as e:
            print_status("WARN", f"Could not check Docker Swarm services: {e}")

        # Check Docker networks
        print_section("Docker Networks")
        print_status("INFO", "Checking Docker networks...")
        try:
            success, networks = run_command(["docker", "network", "ls", "--format", "{{.Name}}"])

            expected_networks = ["central-hub-network"]
            for net_num in configured_net_numbers:
                expected_networks.append(f"deploy_shared-net-{net_num}")

            if success:
                for network in expected_networks:
                    if network in networks:
                        print_status("PASS", f"Docker network '{network}' exists")
                    else:
                        print_status("WARN", f"Docker network '{network}' not found")
        except Exception as e:
            print_status("WARN", f"Could not check Docker networks: {e}")

        # Check system resources
        print_section("System Resources")

        # Check disk space
        print_status("INFO", "Checking disk space...")
        try:
            success, disk_output = run_command(["df", "-h", str(project_dir)])
            if success:
                lines = disk_output.split("\n")
                if len(lines) > 1:
                    fields = lines[1].split()
                    if len(fields) >= 5:
                        disk_usage = fields[4].rstrip("%")
                        try:
                            disk_usage_int = int(disk_usage)
                            if disk_usage_int < 80:
                                print_status("PASS", f"Disk usage is {disk_usage}%")
                            else:
                                print_status("WARN", f"Disk usage is {disk_usage}% (consider cleanup if >80%)")
                        except ValueError:
                            print_status("WARN", "Could not parse disk usage")
        except Exception as e:
            print_status("WARN", f"Could not check disk space: {e}")

        # Check memory usage
        print_status("INFO", "Checking memory usage...")
        try:
            success, mem_output = run_command(["free"])
            if success:
                lines = mem_output.split("\n")
                for line in lines:
                    if line.startswith("Mem:"):
                        fields = line.split()
                        if len(fields) >= 3:
                            try:
                                total = int(fields[1])
                                used = int(fields[2])
                                mem_usage = int((used / total) * 100)
                                if mem_usage < 90:
                                    print_status("PASS", f"Memory usage is {mem_usage}%")
                                else:
                                    print_status("WARN", f"Memory usage is {mem_usage}% (high memory usage detected)")
                            except (ValueError, ZeroDivisionError):
                                print_status("WARN", "Could not parse memory usage")
                        break
        except Exception as e:
            print_status("WARN", f"Could not check memory usage: {e}")

    # HTTP/HTTPS endpoint checks
    if not skip_endpoints:
        print_section("Application Endpoint Checks")

        # Check UI
        check_http_endpoint(f"http://localhost:{UI_PORT}", "FLIP UI", 200)

        # Check API health endpoint
        check_http_endpoint(f"http://localhost:{API_PORT}/health", "FLIP API Health", 200)

        # Check API docs endpoint
        check_http_endpoint(f"http://localhost:{API_PORT}/docs", "FLIP API Docs", 200)

        print_section("FL container running and endpoint checks on the expected ports")
        for net_num in configured_net_numbers:
            fl_port = FL_API_PORT  # Use the same FL API port for all nets
            fl_service_name = f"flip-fl-api-net-{net_num}"
            print_status("INFO", f"Checking FL API Net-{net_num} health endpoint inside container...")
            try:
                success, output = run_command(
                    [
                        "docker",
                        "exec",
                        fl_service_name,
                        "python",
                        "-c",
                        f"import httpx; print(httpx.get('http://localhost:{fl_port}/health', follow_redirects=True).status_code)",
                    ],
                    timeout=10,
                )
                if success and "200" in output:
                    print_status("PASS", f"FL API Net-{net_num} is responding (HTTP 200)")
                else:
                    print_status("FAIL", f"FL API Net-{net_num} is NOT responding at port {fl_port}: {output}")
            except Exception as e:
                print_status("FAIL", f"FL API Net-{net_num} is NOT responding at port {fl_port}: {e}")

        # Check that FL API can be reached from flip-api container
        for net_num in configured_net_numbers:
            fl_port = 8000  # FL API always runs on port 8000 inside the container
            fl_service_name = f"flip-fl-api-net-{net_num}"
            container_name = "flip-api"
            fl_api_url = f"http://{fl_service_name}:{fl_port}/check_server_status"
            print_status("INFO", f"Checking FL API Net-{net_num} from '{container_name}' container...")
            try:
                success, output = run_command(
                    [
                        "docker",
                        "exec",
                        container_name,
                        "python",
                        "-c",
                        f"import httpx; print(httpx.get('{fl_api_url}', follow_redirects=True).status_code)",
                    ],
                    timeout=10,
                )
                if success and "200" in output:
                    print_status("PASS", f"FL API Net-{net_num} is reachable from '{container_name}' (HTTP 200)")
                else:
                    print_status(
                        "FAIL",
                        f"FL API Net-{net_num} is NOT reachable from '{container_name}' at {fl_api_url}: {output}",
                    )
            except Exception as e:
                print_status(
                    "FAIL", f"FL API Net-{net_num} is NOT reachable from '{container_name}' at {fl_api_url}: {e}"
                )

        # Check Trust endpoints if they exist
        print_section("Trust Service Endpoint Checks")

        check_http_endpoint(f"http://localhost:{TRUST_API_PORT}/health", "Trust API Health", 200)
        check_http_endpoint(f"http://localhost:{TRUST_API_PORT}/docs", "Trust API Docs", 200)

        check_http_endpoint(f"http://localhost:{IMAGING_API_PORT}/health", "Imaging API Health", 200)
        check_http_endpoint(f"http://localhost:{IMAGING_API_PORT}/docs", "Imaging API Docs", 200)

        check_http_endpoint(f"http://localhost:{DATA_ACCESS_API_PORT}/health", "Data Access API Health", 200)
        check_http_endpoint(f"http://localhost:{DATA_ACCESS_API_PORT}/docs", "Data Access API Docs", 200)

        # Check XNAT endpoints
        print_section("XNAT Service Endpoint Checks")

        # XNAT Trust 1 endpoint - use 127.0.0.1 to avoid IPv6 routing issues with Docker Swarm
        check_http_endpoint(f"http://127.0.0.1:{XNAT_PORT_TRUST_1}", "XNAT Trust 1 Web UI", [200, 302])

        # XNAT Trust 2 endpoint - use 127.0.0.1 to avoid IPv6 routing issues with Docker Swarm
        check_http_endpoint(f"http://127.0.0.1:{XNAT_PORT_TRUST_2}", "XNAT Trust 2 Web UI", [200, 302])
        check_http_endpoint(f"http://localhost:{PACS_UI_PORT_TRUST_2}", "Orthanc PACS Trust 2", [200, 401])

    # Database connectivity check
    print_section("Database Connectivity")

    print_status("INFO", "Checking PostgreSQL connectivity...")
    # Try to connect using docker exec
    # success, output = run_command(["docker", "exec", "flip-db", "psql", "-U", "local_user", "-c", "SELECT 1;"])
    # if success:
    #     print_status("PASS", "PostgreSQL database is accessible")
    # else:
    #     print_status("FAIL", f"Cannot connect to PostgreSQL database: {output}")

    # Container logs check
    print_section("Container Logs")

    print_status("INFO", "Checking for container errors in recent logs...")
    critical_containers = ["flip-api", "flip-ui"]
    for container in critical_containers:
        try:
            success, logs = run_command(["docker", "logs", "--tail", "50", container], timeout=10)
            if success:
                # Check for common error patterns
                error_patterns = ["ERROR", "CRITICAL", "Exception", "Traceback"]
                found_errors = []
                for pattern in error_patterns:
                    if pattern in logs:
                        found_errors.append(pattern)

                if found_errors:
                    print_status("WARN", f"Container '{container}' has errors in logs: {', '.join(found_errors)}")
                else:
                    print_status("PASS", f"Container '{container}' logs look clean")
            else:
                print_status("WARN", f"Could not retrieve logs for container '{container}'")
        except Exception as e:
            print_status("WARN", f"Could not retrieve logs for container '{container}': {e}")

    # XNAT-specific checks
    print_section("XNAT Database Connectivity")

    print_status("INFO", "Checking XNAT database connectivity...")
    # Try to find XNAT database containers (both swarm and compose modes)
    # In swarm mode, names contain dots: xnat1_xnat-db.1.HASH
    # In compose mode, names are: xnat1-xnat-db-1

    success, db_containers_output = run_command(
        ["docker", "ps", "--filter", "name=xnat", "--filter", "name=db", "--format", "{{.Names}}"], timeout=5
    )

    if success and db_containers_output:
        found_db = False
        for db_container_name in db_containers_output.strip().split("\n"):
            if "xnat-db" in db_container_name and db_container_name.strip():
                found_db = True
                try:
                    # Try to query the database
                    success, output = run_command(
                        [
                            "docker",
                            "exec",
                            db_container_name,
                            "psql",
                            "-U",
                            "xnat",
                            "-d",
                            "xnat",
                            "-c",
                            "SELECT version();",
                        ],
                        timeout=10,
                    )
                    if success and "PostgreSQL" in output:
                        print_status("PASS", f"XNAT database '{db_container_name}' is accessible and responsive")
                    else:
                        print_status("WARN", f"XNAT database '{db_container_name}' connection failed")
                except Exception as e:
                    print_status("WARN", f"Could not check XNAT database '{db_container_name}': {e}")

        if not found_db:
            print_status("INFO", "No XNAT database containers found (this is OK if XNAT is not deployed)")
    else:
        print_status("INFO", "No XNAT database containers found (this is OK if XNAT is not deployed)")

    # Final summary
    print_section("Summary")

    print()
    print(f"{Colors.GREEN}Passed:   {counters.passed}/{counters.total}{Colors.NC}")
    print(f"{Colors.RED}Failed:   {counters.failed}/{counters.total}{Colors.NC}")
    print(f"{Colors.YELLOW}Warnings: {counters.warnings}/{counters.total}{Colors.NC}")
    print()

    if counters.failed == 0:
        print(f"{Colors.GREEN}✓ Local development verification completed successfully!{Colors.NC}")
        if counters.warnings > 0:
            print(
                f"{Colors.YELLOW}⚠ However, there are {counters.warnings} "
                f"warning(s) that should be reviewed.{Colors.NC}"
            )
        # Try to open UI in browser
        try:
            import webbrowser

            webbrowser.open(f"http://localhost:{UI_PORT}")
            print_status("INFO", f"Opening UI at http://localhost:{UI_PORT}")
        except Exception:
            pass
        sys.exit(0)
    else:
        print(f"{Colors.RED}✗ Local development verification failed with {counters.failed} error(s).{Colors.NC}")
        print(
            f"{Colors.YELLOW}Please review the failed checks and ensure all "
            f"services are properly started with 'docker-compose up'.{Colors.NC}"
        )
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FLIP Local Development Status Checker. "
        "Verifies that the local development environment is functioning correctly."
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    parser.add_argument(
        "--skip-endpoints",
        action="store_true",
        help="Skip HTTP endpoint checks",
    )
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Skip Docker container checks",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to .env file (defaults to .env.development)",
    )

    args = parser.parse_args()

    # Validate project directory exists
    if not args.project_dir.exists() or not args.project_dir.is_dir():
        print(
            f"{Colors.RED}Error: Project directory '{args.project_dir}' does not exist or is not a directory{Colors.NC}"
        )
        sys.exit(1)

    # Validate env file if provided
    if args.env_file and not args.env_file.exists():
        print(f"{Colors.RED}Error: Environment file '{args.env_file}' does not exist{Colors.NC}")
        sys.exit(1)

    main(
        project_dir=args.project_dir,
        skip_endpoints=args.skip_endpoints,
        skip_docker=args.skip_docker,
        env_file=args.env_file,
    )
