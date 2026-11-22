import os
import re
import sys
import tempfile

import pytest

# Каждый запуск тестов работает с отдельной временной базой
_tmp = tempfile.mkdtemp(prefix="dekanat-test-")
os.environ["DATA_DIR"] = _tmp
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.security import reset_ip_limits  # noqa: E402
from app.seed import init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    init_db(reset=True)
    reset_ip_limits()
    yield


def make_client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


def login(login_: str, password: str) -> TestClient:
    c = make_client()
    r = c.post("/login", data={"login": login_, "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def csrf(c: TestClient, path: str = "/profile") -> str:
    html = c.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "CSRF token not found"
    return m.group(1)


@pytest.fixture
def kira():
    return login("kira", "Kira-2026")


@pytest.fixture
def timur():
    return login("timur", "Timur-2026")


@pytest.fixture
def staff():
    return login("dekanat", "Dekanat-2026")
