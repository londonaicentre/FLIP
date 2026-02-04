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
import os
from typing import Dict, List

from sqlmodel import Session, col, delete, select

from flip_api.db.database import engine
from flip_api.db.models.main_models import (
    FLJob,
    FLLogs,
    FLMetrics,
    Image,
    Model,
    ModelsAudit,
    ModelTrustIntersect,
    Projects,
    ProjectsAudit,
    ProjectTrustIntersect,
    ProjectUserAccess,
    Queries,
    QueryResult,
    QueryStats,
    UploadedFiles,
    XNATProjectStatus,
)


def delete_projects_models(session: Session, project_ids: List[str]) -> bool:
    """Delete models associated with the projects"""
    print(f"  🔍 Deleting models for {len(project_ids)} projects...")
    models = session.exec(select(Model).where(col(Model.project_id).in_(project_ids)))

    model_ids = [str(m.id) for m in models.all()]
    print(f"  Found {len(model_ids)} models to delete")
    print(f"  Model IDs: {model_ids}")
    if not model_ids:
        print("  ℹ️ No models found to delete")
        return True

    try:
        # Now delete FL logs, metrics, and jobs
        result = session.execute(delete(FLLogs).where(col(FLLogs.model_id).in_(model_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} FL logs records")
    except Exception as e:
        print(f"  ❌ Error deleting FL logs records: {e}")
        return False
    try:
        result = session.execute(delete(FLMetrics).where(col(FLMetrics.model_id).in_(model_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} FL metrics records")
    except Exception as e:
        print(f"  ❌ Error deleting FL metrics records: {e}")
        return False
    try:
        result = session.execute(delete(FLJob).where(col(FLJob.model_id).in_(model_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} FL job records")
    except Exception as e:
        print(f"  ❌ Error deleting FL job records: {e}")
        return False
    try:
        result = session.execute(delete(UploadedFiles).where(col(UploadedFiles.model_id).in_(model_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} uploaded files records")
    except Exception as e:
        print(f"  ❌ Error deleting uploaded files records: {e}")
        return False
    try:
        # Finally delete models
        result = session.execute(delete(ModelsAudit).where(col(ModelsAudit.model_id).in_(model_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} models audit records")
    except Exception as e:
        print(f"  ❌ Error deleting models audit records: {e}")
        return False
    try:
        # Now delete images and uploaded files
        result = session.execute(delete(Image).where(col(Image.model_id).in_(model_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} image records")
    except Exception as e:
        print(f"  ❌ Error deleting image records: {e}")
        return False
    try:
        # delete model trust intersects
        result = session.execute(delete(ModelTrustIntersect).where(col(ModelTrustIntersect.model_id).in_(model_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} model trust intersect records")
    except Exception as e:
        print(f"  ❌ Error deleting model trust intersect records: {e}")
        return False
    try:
        # delete models
        result = session.execute(delete(Model).where(col(Model.id).in_(model_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} model records")
    except Exception as e:
        print(f"  ❌ Error deleting model records: {e}")
        print(f"Trying to delete {result}")
        return False
    return True


def delete_xnat_project_status(session: Session, project_ids: List[str]) -> bool:
    """Delete XNAT project status for the projects"""
    try:
        result = session.execute(delete(XNATProjectStatus).where(col(XNATProjectStatus.project_id).in_(project_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} XNAT project status records")
        return True
    except Exception as e:
        print(f"  ❌ Error deleting XNAT project status: {e}")
        return False


def load_project_ids() -> Dict[str, str]:
    """Load project IDs from the JSON file created by debug_prelaunch_task.py"""
    json_file_path = "tests/debug_prelaunch_task_projects.json"

    if not os.path.exists(json_file_path):
        print(f"❌ Project IDs file not found: {json_file_path}")
        print("Please run debug_prelaunch_task.py first to create projects.")
        return {}

    try:
        with open(json_file_path, "r") as f:
            projects = json.load(f)
        print(f"✅ Loaded project IDs from {json_file_path}")
        return projects
    except json.JSONDecodeError as e:
        print(f"❌ Error reading JSON file: {e}")
        return {}
    except Exception as e:
        print(f"❌ Error loading project IDs: {e}")
        return {}


def delete_query_results(session: Session, project_ids: List[str]) -> bool:
    """Delete query results for queries belonging to the projects"""
    try:
        # First get all query IDs for the projects
        query_ids_result = session.exec(select(Queries.id).where(col(Queries.project_id).in_(project_ids)))
        query_ids = [str(qid) for qid in query_ids_result.all()]

        if query_ids:
            # Delete query results
            result = session.execute(delete(QueryResult).where(col(QueryResult.query_id).in_(query_ids)))
            deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
            print(f"  ✅ Deleted {deleted_count} query results")
        else:
            print("  ℹ️ No query results to delete")

        return True
    except Exception as e:
        print(f"  ❌ Error deleting query results: {e}")
        return False


def delete_query_stats(session: Session, project_ids: List[str]) -> bool:
    """Delete query stats for queries belonging to the projects"""
    try:
        # First get all query IDs for the projects
        query_ids_result = session.exec(select(Queries.id).where(col(Queries.project_id).in_(project_ids)))
        query_ids = [str(qid) for qid in query_ids_result.all()]

        if query_ids:
            # Delete query stats
            result = session.execute(delete(QueryStats).where(col(QueryStats.query_id).in_(query_ids)))
            deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
            print(f"  ✅ Deleted {deleted_count} query stats")
        else:
            print("  ℹ️ No query stats to delete")

        return True
    except Exception as e:
        print(f"  ❌ Error deleting query stats: {e}")
        return False


def delete_project_user_access(session: Session, project_ids: List[str]) -> bool:
    """Delete project user access for the projects"""
    try:
        result = session.execute(delete(ProjectUserAccess).where(col(ProjectUserAccess.project_id).in_(project_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} project user access records")
        return True
    except Exception as e:
        print(f"  ❌ Error deleting project user access: {e}")
        return False


def delete_project_trust_intersect(session: Session, project_ids: List[str]) -> bool:
    """Delete project trust intersect for the projects"""
    try:
        result = session.execute(
            delete(ProjectTrustIntersect).where(col(ProjectTrustIntersect.project_id).in_(project_ids))
        )
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} project trust intersect records")
        return True
    except Exception as e:
        print(f"  ❌ Error deleting project trust intersect: {e}")
        return False


def delete_queries(session: Session, project_ids: List[str]) -> bool:
    """Delete queries for the projects"""
    try:
        result = session.execute(delete(Queries).where(col(Queries.project_id).in_(project_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} queries")
        return True
    except Exception as e:
        print(f"  ❌ Error deleting queries: {e}")
        return False


def delete_projects(session: Session, project_ids: List[str]) -> bool:
    """Delete the projects themselves"""
    try:
        result = session.execute(delete(Projects).where(col(Projects.id).in_(project_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} projects")
        return True
    except Exception as e:
        print(f"  ❌ Error deleting projects: {e}")
        return False


def delete_projects_audit(session: Session, project_ids: List[str]) -> bool:
    """Delete project audit records for the projects"""
    try:
        result = session.execute(delete(ProjectsAudit).where(col(ProjectsAudit.project_id).in_(project_ids)))
        deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
        print(f"  ✅ Deleted {deleted_count} project audit records")
        return True
    except Exception as e:
        print(f"  ❌ Error deleting project audit records: {e}")
        return False


def delete_project_data_in_order(session: Session, project_ids: List[str]) -> bool:
    """Delete all project-related data in the correct order to avoid dependency issues"""
    print(f"🗑️ Deleting data for {len(project_ids)} projects in dependency order...")

    success = True

    # Delete in reverse dependency order (most dependent first)
    deletion_steps = [
        ("Query Results", delete_query_results),
        ("Query Stats", delete_query_stats),
        ("Queries", delete_queries),
        ("Projects Audit", delete_projects_audit),
        ("Project User Access", delete_project_user_access),
        ("Project Trust Intersect", delete_project_trust_intersect),
        ("XNAT Project Status", delete_xnat_project_status),
        ("Model", delete_projects_models),
        ("Projects", delete_projects),
    ]

    for step_name, delete_function in deletion_steps:
        print(f"  🔍 Deleting {step_name}...")
        try:
            if not delete_function(session, project_ids):
                success = False
                print(f"    ⚠️ Failed to delete {step_name}")
        except Exception as e:
            print(f"    ❌ Error in {step_name} deletion: {e}")
            success = False

    return success


def cleanup_debug_projects() -> bool:
    """Main function to cleanup all debug projects from the database"""
    print("🧹 Starting database cleanup of debug projects...")

    # Load project IDs from JSON file
    projects = load_project_ids()

    if not projects:
        print("❌ No projects found to cleanup.")
        return False

    # Convert project IDs to list and filter out empty ones
    project_ids = [pid for pid in projects.values() if pid]

    if not project_ids:
        print("❌ No valid project IDs found.")
        return False

    print(f"📋 Found {len(project_ids)} projects to cleanup:")
    project_names = {
        "unstaged_project_id": "Unstaged Project",
        "unstaged_project_with_query_id": "Unstaged Project with Query",
        "staged_project_id": "Staged Project",
        "approved_project_id": "Approved Project",
    }

    for key, project_id in projects.items():
        if project_id:
            project_name = project_names.get(key, key)
            print(f"  - {project_name}: {project_id}")

    # Perform database cleanup
    try:
        with Session(engine) as session:
            success = delete_project_data_in_order(session, project_ids)

            if success:
                session.commit()
                print("✅ Database transaction committed successfully")
            else:
                session.rollback()
                print("❌ Database transaction rolled back due to errors")

            return success

    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return False


def cleanup_json_file():
    """Remove the JSON file after cleanup"""
    json_file_path = "tests/debug_prelaunch_task_projects.json"

    try:
        if os.path.exists(json_file_path):
            os.remove(json_file_path)
            print(f"✅ Removed JSON file: {json_file_path}")
        else:
            print(f"⚠️ JSON file not found: {json_file_path}")
    except Exception as e:
        print(f"❌ Error removing JSON file: {e}")


if __name__ == "__main__":
    print("🚀 Starting database cleanup of debug projects...")
    print("=" * 60)

    # Load projects before cleanup for verification
    projects = load_project_ids()
    project_ids = [pid for pid in projects.values() if pid]

    # Perform cleanup
    cleanup_success = cleanup_debug_projects()

    if cleanup_success:
        print("\n" + "=" * 60)
        print("✅ All project data cleaned up successfully!")

        # Remove JSON file
        cleanup_json_file()
    else:
        print("\n" + "=" * 60)
        print("❌ Database cleanup failed. Please check the logs above.")
        print("You may need to manually clean up remaining data.")

    print("\n" + "=" * 60)
    print("🏁 Database cleanup script finished.")
