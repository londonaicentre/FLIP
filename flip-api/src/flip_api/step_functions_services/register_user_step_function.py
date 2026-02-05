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

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from flip_api.auth.dependencies import verify_token
from flip_api.db.database import get_session
from flip_api.domain.interfaces.user import IRegisterUser, IRegisterUserDto, IRoles
from flip_api.user_services.delete_user import delete_user
from flip_api.user_services.register_user import register_user
from flip_api.user_services.set_user_roles import set_user_roles
from flip_api.utils.logger import logger

router = APIRouter(prefix="/step", tags=["step_functions_services"])


@router.post("/users", response_model=IRegisterUserDto, status_code=status.HTTP_201_CREATED)
def register_user_step_function_endpoint(
    request: Request,
    user_data: IRegisterUser,
    db: Session = Depends(get_session),
    token_id: UUID = Depends(verify_token),
):
    """
    Register a new user and assign roles

    This mimics the AWS Step Functions workflow defined in registerUser.yml

    Args:
        request (Request): The FastAPI request object.
        user_data (IRegisterUser): The user data to register, including email and roles.
        db (Session): The database session.
        token_id (UUID): The ID of the current user making the request.

    Returns:
        dict: A dictionary containing the result of the registration and role assignment.

    Raises:
        HTTPException: If an error occurs during registration or role assignment.
    """
    try:
        # Step 1: Register User
        logger.info(f"Registering user: {user_data.email}")

        register_response = register_user(
            user_data=user_data,
            request=request,
            db=db,
            token_id=token_id,
        )

        # If the above does not raise an exception, we assume registration was successful

        # Extract user ID and roles
        user_id = register_response.user_id
        roles = IRoles(roles=register_response.roles)

        # if not user_id:
        #     logger.error("User ID not found in registration response")
        #     raise HTTPException(status_code=500, detail="User ID not found in registration response")

        logger.info(f"Setting roles for user {user_id}: {roles}")

        try:
            # Set User Roles
            set_roles_response = set_user_roles(user_id=user_id, roles_data=roles, db=db, token_id=token_id)
            logger.info(f"Roles set successfully for user {user_id}: {set_roles_response}")

        except Exception as e:
            logger.error(f"Failed to set user roles: {roles}: {str(e)}")

            # If setting roles failed, delete the user
            logger.warning(f"Deleting user {user_id} due to failure in setting roles")

            delete_user(user_id=user_id, request=request, db=db, token_id=token_id)

            # Re-raise the exception to propagate the error
            raise HTTPException(
                status_code=500,
                detail=f"Failed to set user roles for user {user_id}. User has been deleted.",
            )

        # Step 7: Return the formatted output
        logger.info(f"User {user_id} registered successfully with roles {roles}")

        return IRegisterUserDto(
            user_id=user_id,
            email=register_response.email,
            roles=register_response.roles,
        )

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in register_user_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to register user: {str(e)}")
