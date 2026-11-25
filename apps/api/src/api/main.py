from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="API")


class Health(BaseModel):
    status: Literal["ok"]


@app.get("/api/health", operation_id="getHealth")
def get_health() -> Health:
    return Health(status="ok")
