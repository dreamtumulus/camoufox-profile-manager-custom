"""Explicit recovery actions on the trash: restore a profile or remove it for good."""

from fastapi import APIRouter, HTTPException, Query

from camoufox_pm.api.access import (
    is_admin,
    require_profile_access,
    visible_profile_ids,
)
from camoufox_pm.api.dependencies import get_profile_manager
from camoufox_pm.api.models.profiles import ProfileResponse
from camoufox_pm.api.models.recovery import TrashListData
from camoufox_pm.api.models.system import ApiResponse

router = APIRouter()


@router.get("/trash/profiles", response_model=ApiResponse[TrashListData], operation_id="list_trash")
async def list_trash():
    profiles = await get_profile_manager().list_trashed_profiles()
    allowed = await visible_profile_ids()
    if allowed is not None:
        profiles = [profile for profile in profiles if profile.id in allowed]
    return ApiResponse(
        success=True,
        message="Trash",
        data={"profiles": [ProfileResponse.from_profile(profile) for profile in profiles]},
    )


@router.post(
    "/trash/profiles/{profile_id}/restore",
    response_model=ApiResponse[ProfileResponse],
    operation_id="restore_trashed_profile",
)
async def restore_trash(profile_id: str):
    await require_profile_access(profile_id, "edit")
    profile = await get_profile_manager().restore_trashed_profile(profile_id)
    if profile is None:
        raise HTTPException(404, "Trashed profile not found")
    return ApiResponse(
        success=True,
        message="Profile restored; schedules remain paused",
        data=ProfileResponse.from_profile(profile),
    )


@router.delete(
    "/trash/profiles/{profile_id}",
    response_model=ApiResponse[None],
    operation_id="permanently_delete_profile",
)
async def permanently_delete(profile_id: str, confirm: bool = Query(False)):
    await require_profile_access(profile_id, "edit")
    if not confirm:
        raise HTTPException(400, "Permanent deletion requires confirm=true")
    try:
        deleted = await get_profile_manager().permanently_delete_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc) if is_admin() else "Recovery operation failed") from exc
    if not deleted:
        raise HTTPException(404, "Trashed profile not found")
    return ApiResponse(success=True, message="Profile permanently deleted", data=None)
