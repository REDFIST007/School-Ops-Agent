import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.database import init_db
from app.main import app


class TestMinimumLiveScenario(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        settings.database_path = Path(self.tmp.name) / "test_e2e.db"
        settings.upload_dir = Path(self.tmp.name) / "uploads"
        init_db()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_8_step_minimum_live_scenario(self) -> None:
        # 1. Register school, create 2 classes, invite 1 teacher, 2 students, 1 guardian
        admin = self.client.post(
            "/api/auth/register",
            json={
                "school_name": "Sunrise Academy",
                "admin_name": "Principal Rao",
                "admin_email": "rao@sunrise.edu",
                "password": "password123",
            },
        ).json()
        admin_hdr = {"Authorization": f"Bearer {admin['session_token']}"}

        c1 = self.client.post("/api/schools/classes", json={"name": "Grade 8-A", "grade": "8"}, headers=admin_hdr).json()["id"]
        c2 = self.client.post("/api/schools/classes", json={"name": "Grade 8-B", "grade": "8"}, headers=admin_hdr).json()["id"]
        self.assertNotEqual(c1, c2)

        t_inv = self.client.post(
            "/api/auth/invites",
            json={"role": "teacher", "invitee_name": "Ms. Priya", "class_id": c1},
            headers=admin_hdr,
        ).json()["code"]
        teacher = self.client.post(
            "/api/auth/accept-invite",
            json={"code": t_inv, "email": "priya@sunrise.edu", "password": "password123"},
        ).json()
        teacher_hdr = {"Authorization": f"Bearer {teacher['session_token']}"}

        s1_inv = self.client.post(
            "/api/auth/invites",
            json={"role": "student", "invitee_name": "Aarav", "class_id": c1},
            headers=admin_hdr,
        ).json()["code"]
        s1 = self.client.post(
            "/api/auth/accept-invite",
            json={"code": s1_inv, "email": "aarav@sunrise.edu", "password": "password123"},
        ).json()
        s1_hdr = {"Authorization": f"Bearer {s1['session_token']}"}

        s2_inv = self.client.post(
            "/api/auth/invites",
            json={"role": "student", "invitee_name": "Diya", "class_id": c1},
            headers=admin_hdr,
        ).json()["code"]
        s2 = self.client.post(
            "/api/auth/accept-invite",
            json={"code": s2_inv, "email": "diya@sunrise.edu", "password": "password123"},
        ).json()

        g_inv = self.client.post(
            "/api/auth/invites",
            json={"role": "guardian", "invitee_name": "Parent Diya", "linked_student_id": s2["user_id"]},
            headers=admin_hdr,
        ).json()["code"]
        guardian = self.client.post(
            "/api/auth/accept-invite",
            json={"code": g_inv, "email": "parent_diya@sunrise.edu", "password": "password123"},
        ).json()
        self.assertEqual(guardian["role"], "guardian")

        # 2. Upload roster document with duplicate/ambiguous row and resolve it
        roster_csv = "student_name,class_name,guardian_contact\nAarav,Grade 8-A,+919000000001\nRohan,Unknown Class,+919000000002\n"
        r_doc = self.client.post(
            "/api/documents/upload",
            data={"doc_type": "class_roster", "pasted_text": roster_csv},
            headers=admin_hdr,
        ).json()
        self.assertGreaterEqual(len(r_doc["conflicts"]), 2)
        conflict_id = r_doc["conflicts"][0]["id"]
        res_cf = self.client.post(
            f"/api/documents/conflicts/{conflict_id}/resolve",
            json={"resolution": "merged"},
            headers=admin_hdr,
        )
        self.assertEqual(res_cf.status_code, 200)

        # Idempotent re-upload of exact same roster does not duplicate
        r_doc_dup = self.client.post(
            "/api/documents/upload",
            data={"doc_type": "class_roster", "pasted_text": roster_csv},
            headers=admin_hdr,
        ).json()
        self.assertTrue(r_doc_dup["deduplicated"])

        # 3. Upload incomplete assignment brief -> clarify -> approve -> creates assignment
        brief = "Title: Linear Equations\nInstructions: Solve 10 problems."
        b_doc = self.client.post(
            "/api/documents/upload",
            data={"doc_type": "assignment_brief", "pasted_text": brief},
            headers=teacher_hdr,
        ).json()
        self.assertEqual(b_doc["document"]["approval_state"], "needs_clarification")
        doc_id = b_doc["document"]["id"]

        clarified = self.client.post(
            f"/api/documents/{doc_id}/clarify",
            json={"answers": {"subject": "Math", "due_date": "2026-11-30", "target_ref": "Grade 8-A"}},
            headers=teacher_hdr,
        ).json()
        self.assertEqual(clarified["approval_state"], "pending_review")

        approved = self.client.post(
            f"/api/documents/{doc_id}/approve?create_assignment_flag=true&target_class_id={c1}",
            headers=teacher_hdr,
        ).json()
        self.assertIsNotNone(approved["created_assignment"])
        self.assertEqual(approved["created_assignment"]["student_count"], 2)

        # 4. Student 1 reports progress via Telegram webhook; Student 2 reports blocked via WhatsApp webhook
        # Link Student 1 & 2 chat identities
        self.client.post(
            "/api/auth/link-chat",
            json={"channel": "telegram", "external_user_id": "tg_s1"},
            headers=s1_hdr,
        )
        # Student 2 links via simulator/webhook using a fresh chat invite or direct DB-free API:
        s2_hdr = {"Authorization": f"Bearer {s2['session_token']}"}
        self.client.post(
            "/api/auth/link-chat",
            json={"channel": "whatsapp", "external_user_id": "919888877777"},
            headers=s2_hdr,
        )

        tg_resp = self.client.post(
            "/api/webhooks/telegram",
            json={"update_id": 1001, "message": {"message_id": 1, "chat": {"id": "tg_s1"}, "text": "Making progress on Q1-Q5"}},
        ).json()
        self.assertEqual(tg_resp["intent"], "progress_update")

        # Duplicate webhook retry is idempotent
        tg_dup = self.client.post(
            "/api/webhooks/telegram",
            json={"update_id": 1001, "message": {"message_id": 1, "chat": {"id": "tg_s1"}, "text": "Making progress on Q1-Q5"}},
        ).json()
        self.assertTrue(tg_dup["deduplicated"])

        wa_resp = self.client.post(
            "/api/webhooks/whatsapp",
            json={"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.1", "from": "919888877777", "text": {"body": "I am blocked on Q7, need help"}}]}}]}]},
        ).json()
        self.assertEqual(wa_resp["intent"], "blocked_help_request")

        # 5. Reminder job respects quiet hours (e.g. 22:30) and differentiates states when active (14:00)
        rem_quiet = self.client.post("/api/reminders/run?override_hhmm=22:30", headers=teacher_hdr).json()
        self.assertTrue(rem_quiet["quiet_hours_active"])
        self.assertGreater(rem_quiet["counts"]["deferred"], 0)

        rem_active = self.client.post("/api/reminders/run?override_hhmm=14:00", headers=teacher_hdr).json()
        self.assertFalse(rem_active["quiet_hours_active"])
        kinds = {r["kind"] for r in rem_active["reminders"]}
        self.assertIn("nudge_silent", kinds)
        self.assertIn("help_blocked", kinds)
        self.assertIn("guardian_escalation", kinds)

        # 6. Student 1 submits work via Telegram; Teacher requests revision then completes after resubmission
        sub_resp = self.client.post(
            "/api/webhooks/telegram",
            json={"update_id": 1002, "message": {"message_id": 2, "chat": {"id": "tg_s1"}, "text": "Submitted my linear equations answers"}},
        ).json()
        self.assertEqual(sub_resp["intent"], "submission")

        # Fetch student_assignment_id for Student 1 from SQLite via teacher dashboard or API
        from app.database import get_conn
        with get_conn() as conn:
            sa1_id = conn.execute("SELECT id FROM student_assignments WHERE student_id = ?", (s1["user_id"],)).fetchone()["id"]

        fb1 = self.client.post(
            f"/api/assignments/{sa1_id}/feedback",
            json={"decision": "revision_requested", "comment": "Please show steps for Q4."},
            headers=teacher_hdr,
        )
        self.assertEqual(fb1.status_code, 201)

        resub = self.client.post(
            f"/api/assignments/{sa1_id}/submit",
            json={"submission_type": "text", "content_text": "Updated Q4 with full steps."},
            headers=s1_hdr,
        ).json()
        self.assertEqual(resub["version"], 2)

        fb2 = self.client.post(
            f"/api/assignments/{sa1_id}/feedback",
            json={"decision": "completed", "comment": "Great work!"},
            headers=teacher_hdr,
        )
        self.assertEqual(fb2.status_code, 201)

        # 7. Wrong-context access attempt (Student 1 tries to give teacher feedback) -> rejected 403
        bad_fb = self.client.post(
            f"/api/assignments/{sa1_id}/feedback",
            json={"decision": "completed", "comment": "Self-approve"},
            headers=s1_hdr,
        )
        self.assertEqual(bad_fb.status_code, 403)

        # 8. Audit & Trace timeline explains everything
        audit = self.client.get("/api/audit", headers=admin_hdr).json()
        event_types = {e["event_type"] for e in audit}
        self.assertIn("school_registered", event_types)
        self.assertIn("document_parsed", event_types)
        self.assertIn("document_approved", event_types)
        self.assertIn("assignment_created", event_types)
        self.assertIn("student_blocked", event_types)
        self.assertIn("reminders_executed", event_types)
        self.assertIn("work_submitted", event_types)
        self.assertIn("work_resubmitted", event_types)
        self.assertIn("access_denied", event_types)


if __name__ == "__main__":
    unittest.main()
