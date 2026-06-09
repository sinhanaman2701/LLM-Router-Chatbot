from __future__ import annotations

from dataclasses import dataclass
from typing import Type

from pydantic import BaseModel, ConfigDict

from chatbot.state.schemas import RiskLevel


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetFacilityAvailabilityParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facility_id: str
    date: str


class CreateBookingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facility_id: str
    date: str
    start_time: str
    end_time: str


class CancelBookingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    booking_id: str


GetFacilityListParams = EmptyParams
GetMyBookingsParams = EmptyParams


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    param_schema: Type[BaseModel]
    domain: list[str]
    risk_level: RiskLevel
    requires_confirmation: bool
    idempotent: bool
    sensitive_fields: list[str]
    rate_limit: int | None = None
    timeout_seconds: int = 10
    max_retries: int = 3


TOOL_REGISTRY: dict[str, ToolDef] = {
    "get_facility_list": ToolDef(
        name="get_facility_list",
        description="Fetch the facilities available in the resident community.",
        param_schema=GetFacilityListParams,
        domain=["facility_booking"],
        risk_level="LOW",
        requires_confirmation=False,
        idempotent=True,
        sensitive_fields=[],
        max_retries=3,
    ),
    "get_facility_availability": ToolDef(
        name="get_facility_availability",
        description="Fetch bookings for a facility and compute availability for a date.",
        param_schema=GetFacilityAvailabilityParams,
        domain=["facility_booking"],
        risk_level="LOW",
        requires_confirmation=False,
        idempotent=True,
        sensitive_fields=[],
        max_retries=3,
    ),
    "create_booking": ToolDef(
        name="create_booking",
        description="Create a new facility booking for the resident.",
        param_schema=CreateBookingParams,
        domain=["facility_booking"],
        risk_level="HIGH",
        requires_confirmation=True,
        idempotent=False,
        sensitive_fields=["user_email"],
        max_retries=1,
    ),
    "cancel_booking": ToolDef(
        name="cancel_booking",
        description="Cancel an existing facility booking.",
        param_schema=CancelBookingParams,
        domain=["facility_booking"],
        risk_level="HIGH",
        requires_confirmation=True,
        idempotent=False,
        sensitive_fields=[],
        max_retries=1,
    ),
    "get_my_bookings": ToolDef(
        name="get_my_bookings",
        description="Fetch the resident's bookings from the backend.",
        param_schema=GetMyBookingsParams,
        domain=["facility_booking"],
        risk_level="LOW",
        requires_confirmation=False,
        idempotent=True,
        sensitive_fields=[],
        max_retries=3,
    ),
}
