from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, Client
from django.utils import timezone

from .domain import DataError, assemble
from .models import ReportSnapshot
from .reports import build_report, legacy_source, queue_reports, sync_reports
from .sheets import Sheets
from .tests import RULES, source


BL = "202611240025"


def history():
    return legacy_source([[BL, 2, 3, 52, 0, 0, 0, 0, 0]],
                         [[BL, 2, 3, 0, 0, 0, 0, 0]], [[BL]], [[100]], [], [])


def batch():
    original = source()
    return SimpleNamespace(bl=BL, id="test-batch", state="review", source=original, orders=assemble(original),
                           collected_at=timezone.now(), rules=deepcopy(RULES))


class ReportCalculationTests(SimpleTestCase):
    def test_same_bl_is_replaced_not_added_and_differences_remain_visible(self):
        r = build_report(history(), [batch()])
        self.assertEqual(r["totals"]["bl_count"], 1)
        self.assertEqual(r["totals"]["products"], 2)
        self.assertEqual(r["totals"]["work"]["작업시간"], "2")
        self.assertEqual(r["totals"]["four_total"], "52")
        self.assertEqual(r["rows"][0]["verification"], "원본·DB 일치")
        self.assertIn("작업시간: 기존 시트 3 / 중판 2", r["rows"][0]["differences"])
        self.assertEqual(r["totals"]["needs_check"], 1)

    def test_free_orders_and_zero_price_work_still_count(self):
        b = batch()
        b.orders[0].update(opp=0, memo="", extra_hours="0")
        r = build_report({"legacy": {}, "billing": {}}, [b])
        self.assertEqual(r["totals"]["invoice_count"], 0)
        self.assertEqual(r["totals"]["work"]["작업시간"], "2")
        self.assertEqual(r["totals"]["four_total"], "52")
        self.assertGreater(float(r["totals"]["cost"]), 0)

    def test_extra_hours_do_not_change_actual_hours_and_pending_is_excluded(self):
        b = batch()
        b.orders[0].update(extra_hours="10", extra_amount="1000")
        pending = build_report({"legacy": {}, "billing": {}}, [b])
        self.assertEqual(pending["totals"]["pending"], 1)
        self.assertEqual(pending["totals"]["invoice_total"], "0")
        b.orders[0]["reviewed"] = True
        confirmed = build_report({"legacy": {}, "billing": {}}, [b])
        self.assertEqual(confirmed["totals"]["work"]["작업시간"], "2")
        self.assertEqual(confirmed["totals"]["invoice_total"], "166000")
        self.assertEqual(confirmed["rows"][0]["sheet_check"], "확인 필요")

    def test_payment_blank_is_unknown_not_unpaid_or_paid(self):
        s = legacy_source([], [], [], [], [[BL]]*5,
                          [[100, "결제완료"], [200, "미결제"], [300, ""], [400, "보류"], [50, "알수없음"]])
        r = build_report(s, [])
        self.assertEqual(r["totals"]["paid"], "100")
        self.assertEqual(r["totals"]["unpaid"], "200")
        self.assertEqual(r["totals"]["unknown"], "350")
        self.assertEqual(r["totals"]["held"], "400")
        self.assertEqual(r["totals"]["cost_missing"], 1)
        self.assertTrue(r["rows"][0]["differences"])

    def test_incomplete_recollection_keeps_previous_complete_snapshot(self):
        b = batch()
        previous = build_report(history(), [b])
        b.orders, b.collected_at, b.state = [], None, "fetching"
        r = build_report(history(), [b], previous)
        r2 = build_report(history(), [b], r)
        self.assertEqual(r2["totals"]["work"]["작업시간"], "2")
        self.assertEqual(r2["rows"][0]["source_at"], previous["rows"][0]["source_at"])
        self.assertEqual(sum(d.startswith("최신 중판 조회") for d in r2["rows"][0]["differences"]), 1)

    def test_source_disagreement_does_not_claim_verification(self):
        b = batch()
        b.orders[0]["work"]["작업시간"] = "99"
        r = build_report(history(), [b])
        self.assertEqual(r["rows"][0]["verification"], "확인 필요")

    def test_legacy_cost_mismatch_and_missing_cost_are_not_hidden(self):
        s = history()
        s["legacy"][BL].update(cost_check="확인 필요", cost=None)
        r = build_report(s, [])
        self.assertEqual(r["totals"]["needs_check"], 1)
        self.assertEqual(r["totals"]["cost_missing"], 1)
        self.assertEqual(r["totals"]["pending_unknown"], 1)

    def test_duplicate_or_invalid_history_stops_before_publication(self):
        for rows in ([[BL], [BL]], [["invalid"]], [[BL, 2, "#REF!"]]):
            with self.subTest(rows=rows), self.assertRaises(DataError):
                legacy_source(rows, [], [], [], [], [])

    def test_report_writer_never_changes_raw_cost_billing_or_payment_cells(self):
        client = object.__new__(Sheets)
        names = ["BL별 작업 검산", "대시보드", "주문 데이터", "원가 정산", "청구 입력(내가 입력)"]
        props = {"sheets": [{"properties": {"title": name, "sheetId": i+10,
            "gridProperties": {"rowCount": 1000, "columnCount": 32}}} for i, name in enumerate(names)]}
        requests = []
        with patch.object(client, "metadata", return_value=props), patch.object(client, "values", return_value=[[], [], []]), patch.object(client, "call", side_effect=lambda *a, **kw: requests.extend(kw["json"]["requests"])):
            with self.assertRaises(DataError):
                client.save_reports(build_report(history(), []))
        write_ids = [r["updateCells"].get("start", r["updateCells"].get("range"))["sheetId"] for r in requests if "updateCells" in r]
        self.assertTrue(write_ids)
        self.assertTrue(set(write_ids) <= {10, 11, 1349874000})
        self.assertTrue(all(r["updateCells"].get("start", r["updateCells"].get("range")).get("columnIndex", 0) < 15 for r in requests if "updateCells" in r and r["updateCells"].get("start", r["updateCells"].get("range"))["sheetId"] == 10))


class ReportStateTests(TestCase):
    def test_public_reports_routes_work_through_both_origins(self):
        for root in ("", "/bl-cost"):
            self.assertEqual(self.client.get(root+"/work-check/").status_code, 200)
            self.assertEqual(self.client.get(root+"/dashboard/").status_code, 200)
            self.assertEqual(self.client.get(root+"/api/reports/").json()["state"], "queued")

    def test_refresh_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/api/reports/").status_code, 403)

    def test_failed_sheet_write_preserves_last_report(self):
        snapshot = ReportSnapshot.objects.create(data={"previous": True})
        client = SimpleNamespace(report_source=lambda: history(), report_backup=lambda: {"original": True},
                                 save_reports=lambda data: (_ for _ in ()).throw(DataError("write failed")))
        with self.assertRaises(DataError):
            sync_reports(client)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.data, {"previous": True})
        self.assertEqual(snapshot.backup, {"original": True})
        self.assertIsNone(snapshot.synced_at)

    def test_request_during_refresh_is_not_lost(self):
        ReportSnapshot.objects.create()
        def save(data):
            queue_reports()
            return {"verified": True}
        client = SimpleNamespace(report_source=history, report_backup=lambda: {"backup": True}, save_reports=save)
        sync_reports(client)
        self.assertEqual(ReportSnapshot.objects.get(pk=1).state, "queued")
