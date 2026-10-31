"""Общая обвязка тестов: изолированный HYDRA_HOME до импорта приложения."""
from __future__ import annotations

import os
import shutil
import tempfile

TMP = tempfile.mkdtemp(prefix="hydra-test-")
os.environ["HYDRA_HOME"] = TMP
os.environ["HYDRA_ADMIN_PASSWORD"] = "test-password-123"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from hydra.web.app import create_app  # noqa: E402

ADMIN = {"username": "admin", "password": "test-password-123"}


@pytest.fixture(scope="session")
def app():
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin(client):
    r = client.post("/login", data=ADMIN, follow_redirects=False)
    assert r.status_code == 303, r.text
    return client


@pytest.fixture(scope="session")
def node(admin):
    r = admin.post(
        "/nodes/new",
        data={"name": "de1", "address": "de1.example.com", "engine": "xray", "api_port": "8443"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return int(r.headers["location"].split("/")[2].split("?")[0])


@pytest.fixture(scope="session", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(TMP, ignore_errors=True)
