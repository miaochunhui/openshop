"""AI mediator API routes."""
from __future__ import annotations

from typing import Any

from packages.commerce import ai_config, ai_service
from packages.commerce.web import RequestContext, Router


def register(router: Router) -> None:
    @router.add("GET", "/api/ai/status", auth="public")
    def ai_status(ctx: RequestContext) -> dict[str, Any]:
        return ai_config.get_status(ctx)

    @router.add("POST", "/api/ai/mediate", auth="user")
    def ai_mediate(ctx: RequestContext) -> dict[str, Any]:
        return ai_service.mediate(ctx, ctx.json_object())

    @router.add("POST", "/api/ai/plans/{plan_id}/adopt", auth="user")
    def ai_adopt(ctx: RequestContext, plan_id: str) -> dict[str, Any]:
        return ai_service.adopt_plan(ctx, plan_id)
