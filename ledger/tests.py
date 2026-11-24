import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from .domain import DataError, assemble, calculate, decimal, first_work, packaging, summarize
from .jungpan import parse_child, parse_parent, parse_products
from .models import Batch
from .sheets import Sheets, build_rows, cell, formula_rows, Formula, RAW, COST, BILL, APPROVAL

RULES = {"month": "2026-11", "version": "test", "exchange": "203.7", "hourly": "15000", "vat": "0.1",
         "free_boxes": "10", "box_cny": "8", "free_hours": [["0", ".5", ".5"], ["100", "1", "1.5"],
          ["300", "1.5", "2"], ["500", "2", "3"], ["1500", "3", "4"]],
         "costs": {"hourly_cny": "50", "opp_cny": ".2", "box_cny": "7.78", "cbm_cny": "393",
                   "sticker_cny": ".42", "sewing_cny": ".79", "barcode_krw": "140"}}


def source():
    return {"bl": "202611240025", "parent_seq": "18597", "children": [{"seq": "17974", "number": "202611160081",
       "amount": "1958", "shipping": "쿠팡로켓그로스", "recipient": "테스트 고객", "member": "테스트 회원",
       "ware_err": "300개 OPP봉투사용 //6개 박스 사용", "manager_memo": "", "admin_memo": "", "box_field": "0"}],
       "products": [{"unit": unit, "child_seq": "17974", "order": "3316572771225002998", "tracking": "710111954115",
        "name": "Test item", "work": "인쇄스티커 : 52\n작업시간 : 2\n인쇄스티커 : 52\n작업시간 : 1\n작업시간 : 2",
        "quantity": "5", "item_amount": "¥ 120", "status": "입고완료"} for unit in ("20431", "20430")]}


class DomainTests(SimpleTestCase):
    def test_unknown_sheet_structure_stops_before_writing(self):
        client = object.__new__(Sheets)
        meta = {"sheets": [{"properties": {"title": n, "sheetId": i, "gridProperties": {"rowCount":15000}}}
                           for i,n in enumerate((RAW,COST,BILL))]}
        with patch.object(client,"metadata",return_value=meta), patch.object(client,"values",return_value=[[],[],[],[],[],[]]), patch.object(client,"call") as call:
            with self.assertRaisesMessage(DataError,"열 구조"):
                client.prepare(SimpleNamespace(id="test",bl="202601010001"))
        call.assert_not_called()

    def test_top_entry_per_type_and_tracking_deduplication(self):
        orders = assemble(source())
        self.assertEqual(orders[0]["work"], {"인쇄스티커": "52", "작업시간": "2"})
        self.assertEqual(orders[0]["boxes"], 6)
        self.assertEqual(orders[0]["opp"], 300)
        self.assertEqual(summarize(orders, RULES)["work"]["작업시간"], "2")

    def test_conflicting_product_repetitions_stop_collection(self):
        s = source()
        s["products"][1]["work"] = "작업시간 : 4"
        with self.assertRaises(DataError):
            assemble(s)

    def test_box_select_is_used_and_conflicts_need_review(self):
        s = source()
        s["children"][0].update(ware_err="", box_field="12")
        self.assertEqual(assemble(s)[0]["boxes"], 12)
        s["children"][0]["ware_err"] = "8박스 사용"
        o = assemble(s)[0]
        self.assertTrue(o["review_required"])
        self.assertIn("실사용량", o["warnings"][0])

    def test_actual_usage_not_instructions_or_corrections(self):
        self.assertEqual(packaging("4박스 사용")["boxes"], 4)
        self.assertEqual(packaging("박스 20개 사용")["boxes"], 20)
        for memo in ("OPP 1봉씩 포장해주세요", "박스 10개 사용 예정", "박스 10개 취소, 8개로 수정"):
            self.assertIsNone(packaging(memo)["boxes"])
        o = assemble(source())[0]
        o["memo"] = "OPP 봉투 로고 인쇄 비용 받아야함"
        self.assertTrue(o["review_required"])

    def test_long_order_numbers_stay_text_in_google_cells(self):
        n = "3317196504266012180"
        self.assertEqual(cell(n), {"userEnteredValue": {"stringValue": n}})
        self.assertEqual(cell("=IMPORTXML('bad')")["userEnteredValue"]["stringValue"], "=IMPORTXML('bad')")

    def test_box_allowance_and_vat_rounding(self):
        o = assemble(source())[0]
        for boxes, excess, charge in ((10, "0", "0"), (11, "1", "1793"), (20, "10", "17926")):
            o["boxes"] = boxes
            c = calculate(o, RULES)
            self.assertEqual(c["box_excess"], excess)
            self.assertEqual(c["box_charge"], charge)
        self.assertEqual(c["total"], "17920")

    def test_extra_time_does_not_change_actual_cost_hours(self):
        o = assemble(source())[0]
        o.update(extra_hours=".5", extra_amount="1000", reason="별도 작업과 부자재")
        c = calculate(o, RULES)
        self.assertEqual(c["actual_hours"], "2")
        self.assertEqual(c["extra_time_charge"], "8250.00")
        self.assertEqual(c["total"], "9250")

    def test_work_waiver_keeps_box_charge(self):
        o = assemble(source())[0]
        o.update(boxes=20, waive_work=True)
        self.assertEqual(calculate(o, RULES)["total"], "17920")
        o["waive_total"] = True
        self.assertEqual(calculate(o, RULES)["total"], "0")

    def test_missing_configuration_never_becomes_zero_charge(self):
        self.assertFalse(calculate(assemble(source())[0], {})["ready"])
        for value in ("NaN", "Infinity", "-1", "99999999999999999999"):
            with self.assertRaises(DataError):
                decimal(value)

    def test_sheets_rows_allocate_order_costs_once(self):
        s = source()
        b = SimpleNamespace(id="batch-test", bl=s["bl"], source=s, orders=assemble(s), rules=RULES)
        rows = build_rows(b)
        self.assertEqual(len(rows["주문 데이터"]), 2)
        self.assertTrue(all(len(r) == 58 for r in rows["주문 데이터"]))
        self.assertTrue(all(len(r) == 33 for r in rows["원가 정산"]))
        self.assertTrue(all(len(r) == 30 for r in rows["청구 입력(내가 입력)"]))
        self.assertEqual(sum(r[8] for r in rows["주문 데이터"]), 2)
        self.assertEqual(sum(r[45] for r in rows["주문 데이터"]), 52)
        self.assertEqual(rows["주문 데이터"][0][3], "3316572771225002998")

    def test_derived_sheet_cells_are_formulas_and_ids_are_literals(self):
        s = source()
        b = SimpleNamespace(id="batch-test", bl=s["bl"], source=s, orders=assemble(s), rules=RULES)
        plan = {"targets": {name: {"start": 10} for name in (RAW, COST, BILL, APPROVAL)}}
        rows = formula_rows(b, plan)
        self.assertIsInstance(rows[RAW][0][40], Formula)
        self.assertIsInstance(rows[COST][0][29], Formula)
        self.assertEqual(cell(rows[RAW][0][3])["userEnteredValue"], {"stringValue": "3316572771225002998"})
        for values in rows.values():
            for row in values:
                for v in row:
                    if isinstance(v, Formula):
                        self.assertEqual(v.count("("), v.count(")"), v)
                        self.assertIn("formulaValue", cell(v)["userEnteredValue"])


class ParserTests(SimpleTestCase):
    def test_product_id_and_long_shop_order(self):
        html = '''<table><tr><th>번호</th></tr><tr><td>20530</td><td></td><td>Test item</td>
        <td><input name="frgValue" value="79048918464133"><div><input name="sOrdValue" value="3317196504266012180"><input name="ORD_SEQ_P" value="18053"></div></td>
        <td>원산지작업</td><td><span>바코드 : 8</span></td><td>Black</td><td>¥ .58</td><td>16</td><td>¥ 9.3</td><td>입고완료</td></tr></table>'''
        p = parse_products(html)[0]
        self.assertEqual(p["order"], "3317196504266012180")
        self.assertEqual(p["child_seq"], "18053")
        self.assertEqual(p["work"], "바코드 : 8")

    def test_child_uses_last_amount_not_quantity_or_first_amount(self):
        cells = ['<input name="chkORD_SEQ" value="18053">', '202611170061', '', '회원', '지정배송지(선불)<br>테스트 고객', '', '', '16 / 16<br>￥ 10.5<br>￥ 9.3', '', '', '', '', '']
        html = '<table id="orderTable"><tr>' + ''.join(f'<td>{v}</td>' for v in cells) + '</tr></table>'
        for name in ("WareErrMemo", "MgrMemo", "AdmMemo"):
            html += f'<input id="{name}18053" value="">'
        html += '<select id="BoxCnt18053"><option selected value="0">0</option></select>'
        c = parse_child(html, {"seq": "18053", "number": "202611170061"})
        self.assertEqual(c["amount"], "9.3")
        self.assertEqual(c["recipient"], "테스트 고객")


class WorkflowTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_X_FORWARDED_PROTO"] = "https"
        users = get_user_model()
        self.user = users.objects.create_user("operator", password="test-password-for-development")
        self.admin = users.objects.create_superuser("owner", password="test-password-for-development")
        self.client.force_login(self.user)
        s = source()
        self.batch = Batch.objects.create(bl=s["bl"], state="review", source=s, orders=assemble(s), rules=RULES,
                                          created_by=self.user, summary=summarize(assemble(s), RULES))

    def post(self, action, **values):
        return self.client.post(f"/api/batches/{self.batch.id}/{action}/", json.dumps({"revision": 0, **values}), content_type="application/json")

    def test_anonymous_has_no_access_to_financial_data(self):
        self.client.logout()
        self.assertEqual(self.client.get("/api/batches/").status_code, 302)

    def test_duplicate_bl_returns_same_batch(self):
        r = self.client.post("/api/batches/", json.dumps({"bl": self.batch.bl}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Batch.objects.count(), 1)

    def test_operator_cannot_approve(self):
        self.assertEqual(self.post("approve").status_code, 403)

    def test_stale_revision_and_negative_values_rejected(self):
        r = self.post("edit", revision=99, order_id="17974", changes={"boxes": 0})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.post("edit", order_id="17974", changes={"boxes": -1}).status_code, 400)

    def test_real_source_cannot_be_changed_in_review(self):
        self.assertEqual(self.post("edit", order_id="17974", changes={"work": {"작업시간": "100"}}).status_code, 400)

    def test_review_changes_do_not_call_google_sheets(self):
        with patch("ledger.views.Sheets") as sheets:
            r = self.post("edit", order_id="17974", changes={"reviewed": True, "extra_amount": "1000", "reason": "포장재"})
        self.assertEqual(r.status_code, 200)
        sheets.assert_not_called()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.source, source())
        self.assertEqual(self.batch.audit_set.count(), 1)

    def test_pending_review_blocks_approval(self):
        self.client.force_login(self.admin)
        with patch("ledger.views.Sheets") as sheets:
            self.assertEqual(self.post("approve").status_code, 400)
        sheets.assert_not_called()

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True, HTTP_X_FORWARDED_PROTO="https")
        client.force_login(self.user)
        self.assertEqual(client.post("/api/batches/", {"bl": "202611240025"}).status_code, 403)

    def test_new_deploy_waits_for_previous_worker_instead_of_exiting(self):
        from ledger.management.commands.runworker import Command
        self.batch.state = "queued"
        self.batch.save()
        db = MagicMock(vendor="postgresql")
        db.cursor.return_value.__enter__.return_value.fetchone.side_effect = [(False,), (True,)]
        with patch("ledger.management.commands.runworker.connection", db), patch("ledger.management.commands.runworker.time.sleep") as sleep, patch("ledger.management.commands.runworker.process", side_effect=KeyboardInterrupt) as process:
            with self.assertRaises(KeyboardInterrupt):
                Command().handle()
        sleep.assert_called_once_with(2)
        process.assert_called_once()

    def test_ambiguous_adjustment_requires_reason(self):
        self.assertEqual(self.post("edit", order_id="17974", changes={"extra_hours": "1"}).status_code, 400)


class SheetRecoveryTests(SimpleTestCase):
    def test_saved_marker_prevents_second_write(self):
        client = object.__new__(Sheets)
        with patch.object(client, "marker", return_value={"metadataId": 7}), patch.object(client, "verify", return_value={"verified": True}) as verify, patch.object(client, "call") as call:
            batch = SimpleNamespace(id="test")
            self.assertEqual(client.save(batch), {"verified": True})
            call.assert_not_called()
            verify.assert_called_once_with(batch)
