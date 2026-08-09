from fastapi.testclient import TestClient

from app.core.skill_base import SkillRegistry
from app.main import app


def test_full_application_startup_registers_skills():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert len(SkillRegistry.list_all()) == 13
