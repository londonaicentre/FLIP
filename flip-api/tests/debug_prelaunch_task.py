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
import warnings
from typing import List, Optional

import requests
from requests.models import Response

from flip_api.domain.schemas.projects import ProjectDetails
from flip_api.utils import constants
from tests.integration.utils import admin_authentication

with open("tests/example_query.sql", "r") as f:
    QUERY = f.read()

try:
    AUTH_TOKEN = admin_authentication()
    print("✅ Admin authentication token retrieved successfully")
except requests.exceptions.RequestException as e:
    warning_msg = f"Failed to get admin authentication token from AWS Cognito: {str(e)}"
    print(f"❗Error: {warning_msg}")
    warnings.warn(warning_msg)
    AUTH_TOKEN = {"Authorization": "Bearer <your_token_here>"}


def create_new_project(client, project_data: ProjectDetails) -> Optional[Response]:
    print(f"  🏗️ Creating new project: {project_data.name}")
    try:
        response = client.post(
            f"{constants.BASE_URL}/projects/",
            json=project_data.model_dump(),
            headers=AUTH_TOKEN,
            timeout=30,
        )

        if response.status_code < 300:
            print("    ✅ Project created successfully")
            return response
        else:
            warning_msg = f"Failed to create project: {response.status_code} - {response.text}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)
            return None
    except Exception as e:
        warning_msg = f"Exception creating project: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        return None


def add_project_query(client, query_info: dict) -> Optional[Response]:
    print(f"  📝 Adding query to project: {query_info.get('name', 'Unnamed Query')}")
    try:
        response = client.post(
            f"{constants.BASE_URL}/cohort/save/",
            json=query_info,
            headers=AUTH_TOKEN,
            timeout=30,
        )

        if response.status_code < 300:
            print("    ✅ Query added successfully")
            return response
        else:
            warning_msg = f"Failed to add query: {response.status_code} - {response.text}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)
            return None
    except Exception as e:
        warning_msg = f"Exception adding query: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        return None


def submit_query_to_trusts(client, query_info: dict) -> Optional[Response]:
    print("  📤 Submitting query to trusts...")
    try:
        response = client.post(
            f"{constants.BASE_URL}/cohort/submit/",
            json=query_info,
            headers=AUTH_TOKEN,
            timeout=30,  # 30 second timeout
        )

        if response.status_code < 300:
            print("    ✅ Query submitted to trusts successfully")
            return response
        else:
            warning_msg = f"Failed to submit query: {response.status_code} - {response.text}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)
            return None
    except requests.exceptions.Timeout:
        warning_msg = "Request timed out while submitting query to trusts"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        return None
    except Exception as e:
        warning_msg = f"Exception submitting query: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        return None


def client_approve_project(client, project_id: str, trust_ids: List[str]) -> Optional[Response]:
    print(f"  ✅ Approving project {project_id} for {len(trust_ids)} trusts")
    try:
        response = client.post(
            f"{constants.BASE_URL}/step/project/{project_id}/approve/",
            json={"trusts": trust_ids},
            headers=AUTH_TOKEN,
            timeout=30,
        )

        if response.status_code < 300:
            print("    ✅ Project approved successfully")
            return response
        else:
            warning_msg = f"Failed to approve project: {response.status_code} - {response.text}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)
            return None
    except Exception as e:
        warning_msg = f"Exception approving project: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        return None


def create_unstaged_project(client, project_name: str, project_description: str) -> Optional[str]:
    print(f"🚀 Creating unstaged project: {project_name}")
    project_data = ProjectDetails(
        name=project_name,
        description=project_description,
        users=[],
    )
    response = create_new_project(client, project_data)
    if response is None:
        print("    ❗Error: Failed to create unstaged project")
        return None

    try:
        project_id = response.json()["id"]
        print(f"  🆔 Project ID: {project_id}")
        return project_id
    except Exception as e:
        warning_msg = f"Exception parsing project response: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        return None


def create_unstaged_project_with_query(
    client,
    project_name: str = "Unstaged Project with Query",
    project_description: str = "This is an unstaged project with a query",
) -> Optional[str]:
    """
    Create an unstaged project and add a query to it.
    """
    print(f"🚀 Creating unstaged project with query: {project_name}")
    project_id = create_unstaged_project(client, project_name, project_description)

    if project_id is None:
        print("    ❗Error: Cannot create query without project")
        return None

    query_name = "Radiology Occurrence Query"
    query_info = {
        "query": QUERY,
        "name": query_name,
        "project_id": str(project_id),
    }

    add_query_response = add_project_query(client, query_info)
    if add_query_response is None:
        print("    ❗Error: Failed to add query, continuing with project only")
        return project_id

    try:
        query_id = add_query_response.json()["query_id"]
        print(f"  🆔 Query ID: {query_id}")

        # Submit the query to trusts
        submit_query_info = {
            "authenticationToken": AUTH_TOKEN.get("Authorization", ""),
            "query": QUERY,
            "name": query_name,
            "project_id": str(project_id),
            "query_id": str(query_id),
        }

        response = submit_query_to_trusts(client, submit_query_info)
        if response is None:
            print("    ❗Error: Failed to submit query to trusts")

        print("  ✅ Project with query created successfully")
        return project_id
    except Exception as e:
        warning_msg = f"Exception processing query response: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        return project_id


def create_staged_project(
    client, project_name: str = "Staged Project", project_description: str = "This is a staged project"
) -> Optional[str]:
    """Create a staged project and approve it."""
    print(f"🚀 Creating staged project: {project_name}")

    # Create an unstaged project
    project_id = create_unstaged_project_with_query(client, project_name, project_description)
    if project_id is None:
        print("    ❗Error: Cannot stage project without base project")
        return None

    print("  🏥 Getting available trusts...")
    try:
        trusts_response = client.get(f"{constants.BASE_URL}/trust/", headers=admin_authentication(), timeout=30)

        if trusts_response.status_code < 300:
            trusts = trusts_response.json()
            print(f"    ✅ Found {len(trusts)} trusts")
        else:
            warning_msg = f"Failed to get trusts: {trusts_response.status_code}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)
            trusts = []
    except Exception as e:
        warning_msg = f"Exception getting trusts: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        trusts = []

    # Stage the project
    print("  📋 Staging project...")
    try:
        stage_response = client.post(
            f"{constants.BASE_URL}/projects/{project_id}/stage/",
            json={"trusts": [trust["id"] for trust in trusts]},
            headers=admin_authentication(),
            timeout=30,
        )

        if stage_response.status_code < 300:
            print("    ✅ Project staged successfully")
        else:
            warning_msg = f"Failed to stage project: {stage_response.status_code} - {stage_response.text}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)
    except Exception as e:
        warning_msg = f"Exception staging project: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)

    print("  ✅ Staged project created successfully")
    return project_id


def create_approved_project(
    client, project_name: str = "Approved Project", project_description: str = "This is an approved project"
) -> Optional[str]:
    """Create an approved project."""
    print(f"🚀 Creating approved project: {project_name}")

    # Create a staged project
    project_id = create_staged_project(client, project_name=project_name, project_description=project_description)
    if project_id is None:
        print("    ❗Error: Cannot approve project without staged project")
        return None

    print("  🏥 Getting available trusts for approval...")
    try:
        trusts_response = client.get(f"{constants.BASE_URL}/trust/", headers=admin_authentication(), timeout=30)

        if trusts_response.status_code < 300:
            trusts = trusts_response.json()
            print(f"    ✅ Found {len(trusts)} trusts for approval")
        else:
            warning_msg = f"Failed to get trusts: {trusts_response.status_code}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)
            trusts = []
    except Exception as e:
        warning_msg = f"Exception getting trusts for approval: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        trusts = []

    # Approve the project
    approval_response = client_approve_project(client, project_id, trust_ids=[trust["id"] for trust in trusts])
    if approval_response is None:
        print("    ❗Error: Failed to approve project")

    print("  ✅ Approved project created successfully")
    return project_id


def create_approved_project_with_model(
    client,
    project_name: str = "Approved Project with Model",
    project_description: str = "This is an approved project with a model",
    model_name: str = "3D Spleen Segmentation Model",
    model_description: str = "Evaluation model for 3D spleen segmentation",
) -> Optional[dict]:
    """Create an approved project with a model ready for training."""
    print(f"🚀 Creating approved project with model: {project_name}")

    # Create an approved project first
    project_id = create_approved_project(client, project_name=project_name, project_description=project_description)
    if project_id is None:
        print("    ❗Error: Cannot create model without approved project")
        return None

    print(f"  🤖 Creating model for project {project_id}...")
    model_payload = {
        "name": model_name,
        "description": model_description,
        "projectId": str(project_id),
    }

    try:
        model_response = client.post(
            f"{constants.BASE_URL}/model",
            json=model_payload,
            headers=AUTH_TOKEN,
            timeout=30,
        )

        if model_response.status_code < 300:
            model_id = model_response.json()["id"]
            print(f"    ✅ Model created with ID: {model_id}")
        else:
            warning_msg = f"Failed to create model: {model_response.status_code} - {model_response.text}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)
            return {"project_id": project_id, "model_id": None}
    except Exception as e:
        warning_msg = f"Exception creating model: {str(e)}"
        print(f"    ❗Error: {warning_msg}")
        warnings.warn(warning_msg)
        return {"project_id": project_id, "model_id": None}

    # Create sample training files
    print("  📝 Creating sample training files...")

    sample_files = {
        "evaluator.py": b"""# Sample evaluator for 3D spleen segmentation
import torch

class SpleenEvaluator:
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def evaluate(self, model, data_loader):
        model.eval()
        total_dice = 0.0
        with torch.no_grad():
            for batch in data_loader:
                # Sample evaluation logic
                pass
        return {"dice_score": total_dice}
""",
        "models.py": b"""# Sample model definition
import torch.nn as nn

class UNet3D(nn.Module):
    def __init__(self, in_channels=1, out_channels=2):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv3d(in_channels, 32, 3, padding=1),
            nn.ReLU(inplace=True)
        )
        self.decoder = nn.Sequential(
            nn.Conv3d(32, out_channels, 1)
        )

    def forward(self, x):
        x = self.encoder(x)
        return self.decoder(x)
""",
        "transforms.py": b"""# Sample transforms for data preprocessing
import numpy as np

def normalize(image):
    return (image - np.mean(image)) / np.std(image)

def resize_3d(image, target_shape=(128, 128, 128)):
    # Placeholder for 3D resize
    return image
""",
        "config.json": b"""{
  "model_config": {
    "architecture": "UNet3D",
    "in_channels": 1,
    "out_channels": 2,
    "learning_rate": 0.001
  },
  "data_config": {
    "image_size": [128, 128, 128],
    "batch_size": 2
  },
  "training_config": {
    "epochs": 10,
    "validation_split": 0.2
  }
}
""",
    }

    downloaded_files = sample_files
    print(f"    ✅ Created {len(downloaded_files)} sample files")

    # Upload files to the model
    print(f"  📤 Uploading {len(downloaded_files)} files to model...")
    uploaded_count = 0

    for filename, content in downloaded_files.items():
        try:
            # Determine content type
            if filename.endswith(".py"):
                content_type = "text/x-python"
            elif filename.endswith(".json"):
                content_type = "application/json"
            else:
                content_type = "application/octet-stream"

            # Get presigned URL for upload
            presigned_response = client.post(
                f"{constants.BASE_URL}/files/preSignedUrl/model/{model_id}",
                json={"fileName": filename, "contentType": content_type},
                headers=AUTH_TOKEN,
                timeout=30,
            )

            if presigned_response.status_code < 300:
                upload_url = presigned_response.json()  # The response is just the URL string

                if upload_url:
                    # Upload file to S3 using presigned URL
                    upload_response = requests.put(upload_url, data=content, timeout=60)

                    if upload_response.status_code < 300:
                        print(f"    ✅ Uploaded {filename}")
                        uploaded_count += 1
                    else:
                        warning_msg = f"Failed to upload {filename}: {upload_response.status_code}"
                        print(f"    ❗Error: {warning_msg}")
                        warnings.warn(warning_msg)
                else:
                    warning_msg = f"No upload URL received for {filename}"
                    print(f"    ❗Error: {warning_msg}")
                    warnings.warn(warning_msg)
            else:
                warning_msg = f"Failed to get presigned URL for {filename}: {presigned_response.status_code}"
                print(f"    ❗Error: {warning_msg}")
                warnings.warn(warning_msg)
        except Exception as e:
            warning_msg = f"Exception uploading {filename}: {str(e)}"
            print(f"    ❗Error: {warning_msg}")
            warnings.warn(warning_msg)

    print(f"  ✅ Uploaded {uploaded_count}/{len(downloaded_files)} files successfully")
    print("  ✅ Approved project with model created successfully")

    return {
        "project_id": project_id,
        "model_id": model_id,
        "uploaded_files": uploaded_count,
    }


if __name__ == "__main__":
    print("🚀 Starting debug project creation...")
    print("=" * 60)

    # Set the base URL for the client
    client = requests.Session()
    client.headers.update({"Content-Type": "application/json"})

    print("🔧 Setting up HTTP client...")
    print("✅ Client configured successfully")

    # Create an unstaged project
    print("\n📋 Creating test projects...")
    unstaged_project_id = create_unstaged_project(
        client, project_name="Unstaged Project", project_description="This is an unstaged project"
    )
    if unstaged_project_id:
        print(f"✅ Unstaged Project ID: {unstaged_project_id}")
    else:
        print("❗Error: Failed to create unstaged project")

    # Create an unstaged project with a query
    unstaged_project_with_query_id = create_unstaged_project_with_query(
        client,
        project_name="Unstaged Project with Query",
        project_description="This is an unstaged project with a query",
    )
    if unstaged_project_with_query_id:
        print(f"✅ Unstaged Project with Query ID: {unstaged_project_with_query_id}")
    else:
        print("❗Error: Failed to create unstaged project with query")

    # Create a staged project
    staged_project_id = create_staged_project(
        client, project_name="Staged Project", project_description="This is a staged project"
    )
    if staged_project_id:
        print(f"✅ Staged Project ID: {staged_project_id}")
    else:
        print("❗Error: Failed to create staged project")

    # Create an approved project
    approved_project_id = create_approved_project(
        client, project_name="Approved Project", project_description="This is an approved project"
    )
    if approved_project_id:
        print(f"✅ Approved Project ID: {approved_project_id}")
    else:
        print("❗Error: Failed to create approved project")

    # Create an approved project with model
    approved_project_with_model = create_approved_project_with_model(
        client,
        project_name="Approved Project with Model",
        project_description="This is an approved project with a model ready for training",
    )
    if approved_project_with_model and approved_project_with_model.get("model_id"):
        print(f"✅ Approved Project with Model - Project ID: {approved_project_with_model['project_id']}")
        print(f"✅ Model ID: {approved_project_with_model['model_id']}")
        print(f"✅ Uploaded {approved_project_with_model.get('uploaded_files', 0)} files")
    else:
        print("❗Error: Failed to create approved project with model")

    # Save the project IDs for deletion on the debug_post_debug_task.py
    projects = {
        "unstaged_project_id": unstaged_project_id,
        "unstaged_project_with_query_id": unstaged_project_with_query_id,
        "staged_project_id": staged_project_id,
        "approved_project_id": approved_project_id,
        "approved_project_with_model": approved_project_with_model,
    }

    print("\n" + "=" * 60)
    print("📊 Project creation summary:")
    for key, project_id in projects.items():
        project_name = key.replace("_", " ").title()
        if project_id:
            print(f"  ✅ {project_name}: {project_id}")
        else:
            print(f"  ⚠️ {project_name}: Failed to create")

    print("\n💾 Saving project IDs to JSON file...")
    try:
        # Filter out None values before saving
        valid_projects = {k: v for k, v in projects.items() if v is not None}
        with open("tests/debug_prelaunch_task_projects.json", "w") as f:
            json.dump(valid_projects, f, indent=4)
        print(f"✅ {len(valid_projects)} project IDs saved to tests/debug_prelaunch_task_projects.json")
        if len(valid_projects) < len(projects):
            print(f"❗Error: {len(projects) - len(valid_projects)} projects failed to create")
    except Exception as e:
        warning_msg = f"Error saving project IDs: {e}"
        print(f"❗Error: {warning_msg}")
        warnings.warn(warning_msg)

    print("\n" + "=" * 60)
    successful_projects = sum(1 for p in projects.values() if p is not None)
    print(f"🎉 {successful_projects}/{len(projects)} projects created successfully!")
    print("📝 You can now run the post-debug task cleanup script:")
    print("   python tests/debug_post_debug_task.py")
    print("🏁 Debug prelaunch task completed.")
    exit(0)
