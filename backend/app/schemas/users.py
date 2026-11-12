import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

RoleKey = Literal["super_admin", "developer", "client"]


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    roles: list[str]
    permissions: list[str]
    client_id: uuid.UUID | None
    csrf_token: str


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    client_id: uuid.UUID | None
    roles: list[str]
    last_login_at: datetime | None
    created_at: datetime
    permissions: list[str] = []
    custom_permissions: bool = False


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=200)
    password: str = Field(max_length=256)
    role: RoleKey
    client_id: uuid.UUID | None = None


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    password: str | None = Field(default=None, max_length=256)


class RolesUpdate(BaseModel):
    role: RoleKey
    client_id: uuid.UUID | None = None


class PermissionsUpdate(BaseModel):
    permissions: list[str] = Field(max_length=100, description="The complete set the user should have")


class UserWebsitesUpdate(BaseModel):
    website_ids: list[uuid.UUID] = Field(max_length=2000)
