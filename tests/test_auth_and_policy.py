import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.database import init_db
from app.main import app


class TestAuthAndPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        settings.database_path = Path(self.tmp.name) / "test_auth.db"
        settings.upload_dir = Path(self.tmp.name) / "uploads"
        init_db()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cross_school_and_wrong_context_isolation(self) -> None:
        # Register School A
        r1 = self.client.post(
            "/api/auth/register",
            json={
                "school_name": "Alpha School",
                "admin_name": "Admin A",
                "admin_email": "admin_a@alpha.edu",
                "password": "password123",
            },
        )
        self.assertEqual(r1.status_code, 201)
        tok_a = r1.json()["session_token"]

        cls_a = self.client.post(
            "/api/schools/classes",
            json={"name": "Grade 8-A", "grade": "8"},
            headers={"Authorization": f"Bearer {tok_a}"},
        ).json()["id"]

        # Register School B
        r2 = self.client.post(
            "/api/auth/register",
            json={
                "school_name": "Beta School",
                "admin_name": "Admin B",
                "admin_email": "admin_b@beta.edu",
                "password": "password123",
            },
        )
        self.assertEqual(r2.status_code, 201)
        tok_b = r2.json()["session_token"]

        # Admin B tries to invite a student into School A's class -> must be 403
        bad_inv = self.client.post(
            "/api/auth/invites",
            json={"role": "student", "invitee_name": "Bob", "class_id": cls_a},
            headers={"Authorization": f"Bearer {tok_b}"},
        )
        self.assertEqual(bad_inv.status_code, 403)

        # Unauthenticated access to protected route -> 401
        anon_client = TestClient(app)
        unauth = anon_client.get("/api/schools/classes")
        self.assertEqual(unauth.status_code, 401)

        # Student cannot create a class -> 403
        stu_inv = self.client.post(
            "/api/auth/invites",
            json={"role": "student", "invitee_name": "Stu One", "class_id": cls_a},
            headers={"Authorization": f"Bearer {tok_a}"},
        ).json()["code"]
        stu_tok = self.client.post(
            "/api/auth/accept-invite",
            json={"code": stu_inv, "email": "stu1@alpha.edu", "password": "password123"},
        ).json()["session_token"]

        stu_cls = self.client.post(
            "/api/schools/classes",
            json={"name": "Hack Class", "grade": "9"},
            headers={"Authorization": f"Bearer {stu_tok}"},
        )
        self.assertEqual(stu_cls.status_code, 403)

        # Audit log in School A recorded the denied attempt
        audit = self.client.get("/api/audit", headers={"Authorization": f"Bearer {tok_a}"}).json()
        self.assertTrue(any(e["event_type"] == "access_denied" and e["outcome"] == "denied" for e in audit))


if __name__ == "__main__":
    unittest.main()
