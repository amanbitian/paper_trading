from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    user_name: str = Field(
        min_length=3,
        max_length=30,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Public handle shown on the platform. Use letters, numbers, and underscores only.",
    )
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    starting_cash: Decimal = Field(default=Decimal("1000000"), ge=0)
    risk_profile: str = Field(default="moderate", max_length=50)

    @field_validator("user_name", mode="before")
    @classmethod
    def normalize_user_name(cls, value: str) -> str:
        return str(value).strip().lower()

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(str(value).strip().split())

    @field_validator("password")
    @classmethod
    def validate_password_bcrypt_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer.")
        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def validate_reset_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be 72 bytes or fewer.")
        if not any(char.isupper() for char in value):
            raise ValueError("Password must include at least one uppercase letter.")
        if not any(char.isdigit() for char in value):
            raise ValueError("Password must include at least one digit.")
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    user_name: str
    email: EmailStr
    starting_cash: Decimal
    current_cash: Decimal
    risk_profile: str
    email_alerts_enabled: bool = False
    created_at: datetime
    updated_at: datetime
