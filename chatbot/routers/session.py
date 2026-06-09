from fastapi import APIRouter

router = APIRouter(prefix="/chat", tags=["session"])


@router.post("/session")
async def create_session_placeholder() -> dict[str, str]:
    return {"status": "not_implemented"}
