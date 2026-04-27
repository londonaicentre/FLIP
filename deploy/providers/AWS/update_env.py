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

import json
import re
import subprocess
import sys


def get_terraform_outputs():
    """Run terraform output -json and return parsed object"""
    try:
        result = subprocess.run(["terraform", "output", "-json"], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running terraform output: {e}")
        sys.exit(1)


def update_env_file(env_path, updates):
    """Update specific keys in the env file"""
    try:
        with open(env_path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"Error: File {env_path} not found.")
        sys.exit(1)

    new_lines = []
    updated_keys = set()

    for line in lines:
        # Check if line is a variable definition (handling optional export)
        # Matches KEY=VALUE or export KEY=VALUE
        match = re.match(r"^(?:export\s+)?([A-Za-z0-9_]+)=(.*)$", line.strip())

        if match:
            key = match.group(1)
            if key in updates:
                new_value = updates[key]
                # Preserve export if present in original line
                prefix = "export " if line.strip().startswith("export ") else ""
                new_lines.append(f"{prefix}{key}={new_value}\n")
                updated_keys.add(key)
                print(f"   Updated {key} = {new_value}")
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    with open(env_path, "w") as f:
        f.writelines(new_lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 update_env.py <path_to_env_file>")
        sys.exit(1)

    env_file = sys.argv[1]
    print("🔄 Fetching Terraform outputs...")
    outputs = get_terraform_outputs()

    # Extract values safely
    try:
        db_endpoint = outputs["DbEndpoint"]["value"]
        db_secret_arn = outputs["DbSecretArn"]["value"]
        cognito_user_pool_id = outputs["CognitoUserPoolId"]["value"]
        cognito_app_client_id = outputs["CognitoAppClientId"]["value"]
    except KeyError as e:
        print(f"❌ Error: Missing output {e} in Terraform state. Did you run 'make apply'?")
        sys.exit(1)

    # Define updates based on infrastructure outputs
    # NOTE Ports are assumed based on standard project configuration
    # TODO Set variable ports in Terraform and output them if they differ from defaults
    updates = {
        "DB_HOST": db_endpoint,
        # NOTE: CENTRAL_HUB_API_URL and FLIP_API_INTERNAL_URL are intentionally NOT updated here.
        # CENTRAL_HUB_API_URL is the public CloudFront URL consumed by flip-ui and trust-api;
        # in staging/production it should be the canonical Route53 domain (e.g. https://stag.flip.aicentre.co.uk/api).
        # FLIP_API_INTERNAL_URL is the Docker-network URL consumed only by fl-server on the hub
        # (e.g. http://flip-api:8000/api) — it must not cross CloudFront because that strips
        # X-Internal-Service-Key at the edge.
        # Set both values manually in the env file.
        "POSTGRES_SECRET_ARN": db_secret_arn,
        "AWS_COGNITO_USER_POOL_ID": cognito_user_pool_id,
        "AWS_COGNITO_APP_CLIENT_ID": cognito_app_client_id,
    }

    print(f"📝 Updating {env_file} with infrastructure values...")
    update_env_file(env_file, updates)
    print("✅ Environment file updated successfully.")


if __name__ == "__main__":
    main()
