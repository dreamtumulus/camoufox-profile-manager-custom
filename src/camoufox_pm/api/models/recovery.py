"""Public recovery response shapes for OpenAPI clients."""

from pydantic import BaseModel

from .profiles import ProfileResponse


class TrashListData(BaseModel):
    profiles: list[ProfileResponse]
