# Interview Prep, Demo Video Script & Git/Deployment Guide

## 1. 8–12 Minute Demo Video Script (Covers All 8 Minimum Live Scenario Steps)
1. **0:00–1:30 — School Registration & Scoped Onboarding**:
   - Open `/register`, register `Sunrise Academy`.
   - Create two classes (`Grade 8-A`, `Grade 8-B`).
   - Issue scoped invites (`INV-...`) for 1 Teacher, 2 Students, and 1 Guardian. Accept invites in incognito/API or login page.
2. **1:30–3:00 — Roster Upload & Conflict Resolution**:
   - Upload a CSV roster containing a duplicate student and an unknown class.
   - Show `roster_conflicts` in `/documents/{id}` and resolve (`Merge` / `Skip`).
3. **3:00–4:45 — Assignment Brief Parsing, Clarification & Approval**:
   - Upload an incomplete assignment brief (missing `subject`, `due_date`, `target_ref`).
   - Show `needs_clarification` state and ambiguity notes -> submit clarification -> `pending_review` -> click **Approve & Commit** -> assignment created for all enrolled students.
4. **4:45–6:15 — Multi-Channel Progress & Blocked Updates + Live SSE**:
   - Keep Teacher Dashboard open (`/dashboard`) showing `Live SSE Connected`.
   - Open `/simulator` in another tab: link Student 1 (`/link INV-...`) and send `"Making progress on Q1-Q3"`. Link Student 2 and send `"I am blocked on Q4, need help"`.
   - Show Teacher Dashboard automatically updating live!
5. **6:15–7:30 — Policy-Aware Reminder Engine**:
   - Trigger reminders during quiet hours (`override_hhmm=22:30`) -> show `deferred_quiet_hours`.
   - Trigger reminders during active hours (`override_hhmm=14:00`) -> show `nudge_silent`, `help_blocked`, and `guardian_escalation` (visible on Guardian Dashboard).
6. **7:30–9:00 — Submission, Revision & Completion**:
   - Student 1 submits work via Simulator/Dashboard -> Teacher requests revision -> Student 1 resubmits (v2) -> Teacher marks `completed`.
7. **9:00–10:00 — Wrong-Context Access Denial**:
   - Attempt a student calling teacher feedback (or cross-school invite) -> show `403 Forbidden`.
8. **10:00–11:00 — Audit & Trace Timeline**:
   - Open `/audit` -> show every action, `correlation_id`, `access_denied` event, and `channel -> intent -> action -> notification` trace spans.

## 2. Pair Extension Prep (20 min in Interview)
- **Add a new document type**: Add literal to `documents.doc_type` CHECK in `database.py`, add parser function in `document_parser.py`, add approval handler in `api_documents.py`.
- **Add a new intent**: Add intent name to `SUPPORTED_INTENTS` and regex/LLM rule in `intent_layer.py`, then add role-checked handler in `channel_adapter.py`.
- **Add a new access rule**: Add helper in `auth_service.py` that logs `access_denied` and raises `PermissionDeniedError`.

## 3. Pushing to GitHub & Deploying Live (Run on Your Local Machine)
```bash
# 1. Unzip school-ops-agent-complete.zip and cd into school-ops-agent/
git remote add origin https://github.com/<YOUR_USERNAME>/school-ops-agent.git
git branch -M main
git push -u origin main

# 2. Deploy Live (Example: Render.com / Railway.app Web Service)
# Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
# Env Vars: SECRET_KEY=<random-hex>, LLM_PROVIDER=deterministic
```
