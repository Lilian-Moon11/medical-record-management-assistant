import sqlite3
import sys
sys.path.insert(0, '.')

from database.schema import _ensure_schema
from database.records_requests import (
    create_request, list_requests, mark_complete,
    check_upload_for_matches,
)
from utils.roi_parser import parse_due_date_from_text
from datetime import datetime

# ── ROI parser tests ──────────────────────────────────────────────────────────
due, src = parse_due_date_from_text(
    'Records will be provided within 30 days of receipt.', datetime(2026,1,1))
assert src == 'parsed', f'Expected parsed, got {src}'
assert due == '2026-01-31', f'Got {due}'

due2, src2 = parse_due_date_from_text('Please allow up to 2 weeks.', datetime(2026,1,1))
assert src2 == 'parsed', f'Expected parsed, got {src2}'
assert due2 == '2026-01-15', f'Got {due2}'

due3, src3 = parse_due_date_from_text('No time limit mentioned.', datetime(2026,1,1))
assert src3 == 'default', f'Expected default, got {src3}'
assert due3 == '2026-01-31', f'Got {due3}'
print('roi_parser: OK')

# ── DB tests ──────────────────────────────────────────────────────────────────
conn = sqlite3.connect(':memory:')
_ensure_schema(conn)

rid = create_request(conn, 1, 'Stanford Medicine', 'Oncology', '2026-01-01', '2026-01-31', 'default')
rows = list_requests(conn, 1)
assert len(rows) == 1
assert rows[0][1] == 'Stanford Medicine'
print('create_request / list_requests: OK')

# Case 1: file name alone contains provider + department term
# "Stanford Medicine" normalised = "stanford medicine"
# parsed_text contains "Stanford Medicine Oncology Department" -> match
matched = check_upload_for_matches(
    conn, 1, doc_id=99,
    file_name='lab_results.pdf',
    parsed_text='Stanford Medicine Oncology Department - Patient Records',
)
assert matched == [rid], f'Expected [{rid}], got {matched}'
print('check_upload_for_matches (parsed_text match): OK')

# Case 2: two requests, same provider, different departments
# Stanford_Oncology doc should match Oncology request but NOT Cardiology
rid2 = create_request(conn, 1, 'Stanford Medicine', 'Cardiology', '2026-01-01', '2026-01-31', 'default')
# Reset first request to pending
conn.execute("UPDATE records_requests SET status='pending', candidate_doc_id=NULL WHERE id=?", (rid,))
conn.commit()

matched2 = check_upload_for_matches(
    conn, 1, doc_id=100,
    file_name='records.pdf',
    parsed_text='Stanford Medicine Oncology - Medical Records Release',
)
assert rid in matched2, f'Oncology request not matched: {matched2}'
assert rid2 not in matched2, f'Cardiology wrongly matched: {matched2}'
print('check_upload_for_matches (department filter): OK')

# Case 3: mark complete
mark_complete(conn, rid)
rows2 = list_requests(conn, 1)
done = next(r for r in rows2 if r[0] == rid)
assert done[6] == 'complete', f'Expected complete, got {done[6]}'
print('mark_complete: OK')

print('\nAll checks passed.')
