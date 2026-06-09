from __future__ import annotations

from pathlib import Path

from chatbot.agents.planners.base_planner import BasePlanner
from chatbot.config import settings

PROMPT_PATH = Path(__file__).parent / "prompts" / "facility_planner_v1.0.txt"


class FacilityPlanner(BasePlanner):
    def __init__(self, llm_client, harness, state_manager) -> None:
        super().__init__(
            llm_client=llm_client,
            harness=harness,
            state_manager=state_manager,
            max_iterations=settings.FACILITY_PLANNER_MAX_ITERATIONS,
            prompt_path=PROMPT_PATH,
        )
