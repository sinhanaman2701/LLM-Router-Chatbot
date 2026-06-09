from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5
from uuid import uuid4

from fastapi import APIRouter, Form, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from chatbot.middleware.auth_middleware import sign_session_token
from chatbot.state.schemas import UserInfo

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthLoginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    session_id: str
    recovery_message: str | None = None


def _internal_uuid(namespace: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{namespace}:{value}"))


@router.post("/login", response_model=AuthLoginResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
) -> AuthLoginResponse:
    try:
        mock_user = await request.app.state.mock_server_auth.authenticate_user(email, password)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication failed") from exc

    user = UserInfo(
        user_id=_internal_uuid("user", str(mock_user.get("user_id") or mock_user["user_email"])),
        community_id=_internal_uuid("community", str(mock_user["community_id"])),
        email=str(mock_user["user_email"]),
        unit_id=str(mock_user["unit_id"]),
        role=str(mock_user["role"]),
    )
    recovery = await request.app.state.state_manager.get_expired_confirmation_recovery(user.user_id)
    recovery_message: str | None = None
    if recovery is not None:
        recovery_message = (
            "Your last pending confirmation expired when the previous session ended. "
            "Please review the request and confirm again if you still want to proceed."
        )
        await request.app.state.audit_logger.log(
            tool_run_id=str(uuid4()),
            tool_name=str(recovery.get("tool_name", "pending_confirmation")),
            params_json=recovery.get("params", {}),
            result_json={"recovery_message": recovery_message},
            context={
                "session_id": recovery.get("session_id", ""),
                "task_id": recovery.get("task_id", ""),
                "user_id": user.user_id,
                "community_id": user.community_id,
                "prompt_version": recovery.get("prompt_version", "facility_planner_v1.0"),
            },
            status="EXPIRED_PENDING",
            policy_rule_id=None,
            latency_ms=None,
            prompt_version=recovery.get("prompt_version", "facility_planner_v1.0"),
            pre_confirmed=False,
        )
    session = await request.app.state.state_manager.create_session(user)
    token = sign_session_token(session.session_id)
    return AuthLoginResponse(token=token, session_id=session.session_id, recovery_message=recovery_message)
