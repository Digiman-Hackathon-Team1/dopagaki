from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from api.agent.router import router as agent_router
from api.pricing.router import router as pricing_router

app = FastAPI(title="API")
app.include_router(agent_router)
app.include_router(pricing_router)


class Health(BaseModel):
    status: Literal["ok"]


@app.get("/api/health", operation_id="getHealth")
def get_health() -> Health:
    return Health(status="ok")
