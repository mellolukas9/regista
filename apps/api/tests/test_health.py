from regista_api.core.config import Settings
from regista_api.main import create_app

from .conftest import DbUrls, api_client


def _settings(db_urls: DbUrls, app_url: str | None = None) -> Settings:
    return Settings(
        environment="test",
        database_url=app_url or db_urls.app,
        database_owner_url=db_urls.owner,
    )


async def test_health_ok_when_database_is_reachable(db_urls: DbUrls) -> None:
    async with api_client(create_app(_settings(db_urls))) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_503_without_leaking_details_when_database_is_down(db_urls: DbUrls) -> None:
    unreachable = "postgresql+asyncpg://regista_app:secret-pw@127.0.0.1:1/regista"
    async with api_client(create_app(_settings(db_urls, unreachable))) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "secret-pw" not in response.text
