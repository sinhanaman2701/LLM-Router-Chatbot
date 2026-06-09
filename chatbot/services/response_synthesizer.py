from __future__ import annotations

from chatbot.agents.planners.base_planner import PlannerOutput


class ResponseSynthesizer:
    def synthesize(self, planner_output: PlannerOutput) -> str:
        return planner_output.content
