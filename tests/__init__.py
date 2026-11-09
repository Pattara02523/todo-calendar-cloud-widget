# -*- coding: utf-8 -*-
"""
Automated E2E Test Suite (Tiers 1-4) for todo-calendar-cloud-widget.
Strict opaque-box contract tests isolating all runtime data in tempfile.TemporaryDirectory()
and mocking external cloud endpoints (Notion & Google Calendar) so live user credentials
and tasks are NEVER modified.
"""
