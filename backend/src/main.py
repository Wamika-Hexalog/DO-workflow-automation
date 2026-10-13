"""do-workflow-automation FastAPI entry-point.

cd backend
uv run uvicorn main:app --app-dir src --reload --port 8002
"""

from fastapi import FastAPI

app = FastAPI(
    title="do-workflow-automation",
    description="Delivery Order automation: browser agent, e-mail pipeline, ShipThis ERP",
    version="0.1.0",
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "do-workflow-automation"}
