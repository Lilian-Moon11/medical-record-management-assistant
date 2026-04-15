# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

import sqlite3
import sys
import unittest
sys.path.insert(0, '.')

from database.schema import _ensure_schema
from database.records_requests import (
    create_request, list_requests, mark_complete,
    check_upload_for_matches,
)
from utils.roi_parser import parse_due_date_from_text
from datetime import datetime

class TestRecordsRequests(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        _ensure_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_roi_parser_days(self):
        due, src = parse_due_date_from_text(
            'Records will be provided within 30 days of receipt.', datetime(2026,1,1))
        self.assertEqual(src, 'parsed')
        self.assertEqual(due, '2026-01-31')

    def test_roi_parser_weeks(self):
        due, src = parse_due_date_from_text('Please allow up to 2 weeks.', datetime(2026,1,1))
        self.assertEqual(src, 'parsed')
        self.assertEqual(due, '2026-01-15')

    def test_roi_parser_default(self):
        due, src = parse_due_date_from_text('No time limit mentioned.', datetime(2026,1,1))
        self.assertEqual(src, 'default')
        self.assertEqual(due, '2026-01-31')

    def test_create_and_list_requests(self):
        rid = create_request(self.conn, 1, 'Stanford Medicine', 'Oncology', '2026-01-01', '2026-01-31', 'default')
        rows = list_requests(self.conn, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 'Stanford Medicine')

    def test_check_upload_parsed_text_match(self):
        rid = create_request(self.conn, 1, 'Stanford Medicine', 'Oncology', '2026-01-01', '2026-01-31', 'default')
        matched = check_upload_for_matches(
            self.conn, 1, doc_id=99,
            file_name='lab_results.pdf',
            parsed_text='Stanford Medicine Oncology Department - Patient Records',
        )
        self.assertEqual(matched, [rid])

    def test_check_upload_department_filter(self):
        rid1 = create_request(self.conn, 1, 'Stanford Medicine', 'Oncology', '2026-01-01', '2026-01-31', 'default')
        rid2 = create_request(self.conn, 1, 'Stanford Medicine', 'Cardiology', '2026-01-01', '2026-01-31', 'default')
        
        matched = check_upload_for_matches(
            self.conn, 1, doc_id=100,
            file_name='records.pdf',
            parsed_text='Stanford Medicine Oncology - Medical Records Release',
        )
        self.assertIn(rid1, matched)
        self.assertNotIn(rid2, matched)

    def test_mark_complete(self):
        rid = create_request(self.conn, 1, 'Stanford Medicine', 'Oncology', '2026-01-01', '2026-01-31', 'default')
        mark_complete(self.conn, rid)
        rows = list_requests(self.conn, 1)
        done = next(r for r in rows if r[0] == rid)
        self.assertEqual(done[6], 'complete')

if __name__ == '__main__':
    unittest.main()
