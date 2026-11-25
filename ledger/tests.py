import json
import re
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse, parse_qs

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from django.templatetags.static import static
from .domain import DataError, ReviewRequired, assemble, calculate, decimal, first_work, packaging, summarize, review_issues, source_fingerprint, invoice_message, fingerprint
from .jungpan import parse_child, parse_parent, parse_products, tracking_url
from .models import Batch
from .sheets import Sheets, build_rows, cell, formula_rows, Formula, BILL, APPROVAL

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
    def test_review_only_targets_billing_decisions_not_general_or_manager_memos(self):
        for memo in ("3개 부족", "3316599518044076553 입고 되면 출고 하기", "도착한 제품이 파손되어 도착하였습니다", "4박스 사용", "10개 박스 사용", "OPP 1봉씩 포장해주세요"):
            with self.subTest(memo=memo):
                s=source()
                s["children"][0].update(ware_err=memo, manager_memo="OPP 500개 사용 / 추가 비용 받아야함")
                o=assemble(s)[0]
                self.assertFalse(o["review_required"])
                self.assertEqual(review_issues(o,RULES),[])
                self.assertEqual(summarize([o],RULES)["pending"],0)
                self.assertNotIn("manager_memo",o)

    def test_cost_reasons_explain_the_decision(self):
        for memo,kind in (("300개 OPP봉투사용 //6개 박스 사용","opp"),("OPP 봉투 로고 인쇄 비용 받아야함","cost"),("박스 많이 사용","quantity")):
            with self.subTest(memo=memo):
                s=source();s["children"][0]["ware_err"]=memo
                issues=review_issues(assemble(s)[0],RULES)
                self.assertIn(kind,[i["kind"] for i in issues])
                self.assertTrue(all(i["text"] and i["action"] for i in issues))

    def test_manager_memo_changes_do_not_invalidate_approved_source(self):
        s=source();before=source_fingerprint(s)
        s["children"][0]["manager_memo"]="처리된 메모 변경"
        self.assertEqual(source_fingerprint(s),before)
        s["children"][0]["ware_err"]="박스 20개 사용"
        self.assertNotEqual(source_fingerprint(s),before)

    def test_unknown_sheet_structure_stops_before_writing(self):
        client = object.__new__(Sheets)
        meta = {"sheets": [{"properties": {"title": n, "sheetId": i, "gridProperties": {"rowCount":15000}}}
                           for i,n in enumerate((BILL,))]}
        with patch.object(client,"metadata",return_value=meta), patch.object(client,"values",return_value=[[],[]]), patch.object(client,"call") as call:
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

    def test_sheets_export_only_real_charges_and_keep_raw_products_in_db(self):
        s = source()
        orders=assemble(s);orders[0].update(reviewed=True,extra_amount="1000",reason="OPP 비용")
        b = SimpleNamespace(id="batch-test", bl=s["bl"], source=s, orders=orders, rules=RULES)
        rows = build_rows(b)
        self.assertEqual(set(rows),{BILL,APPROVAL})
        self.assertEqual(len(rows[BILL]),1)
        self.assertEqual(len(rows[BILL][0]),30)
        self.assertEqual(rows[BILL][0][7],2)
        self.assertEqual(rows[BILL][0][3],"3316572771225002998")
        self.assertEqual(rows[BILL][0][25],1000)
        self.assertEqual(rows[BILL][0][28],invoice_message(orders[0],RULES))
        self.assertEqual(len(b.source["products"]),2)
        orders[0]["extra_amount"]="0"
        self.assertEqual(build_rows(b)[BILL],[])

    def test_derived_sheet_cells_are_formulas_and_ids_are_literals(self):
        s = source()
        orders=assemble(s);orders[0].update(reviewed=True,boxes=20)
        b = SimpleNamespace(id="batch-test", bl=s["bl"], source=s, orders=orders, rules=RULES)
        plan = {"targets": {name: {"start": 10} for name in (BILL, APPROVAL)}}
        rows = formula_rows(b, plan)
        self.assertIsInstance(rows[BILL][0][25], Formula)
        self.assertEqual(cell(rows[BILL][0][3])["userEnteredValue"], {"stringValue": "3316572771225002998"})
        for values in rows.values():
            for row in values:
                for v in row:
                    if isinstance(v, Formula):
                        self.assertEqual(v.count("("), v.count(")"), v)
                        self.assertIn("formulaValue", cell(v)["userEnteredValue"])

    def test_box_charge_is_automatic_and_message_matches_sheet_format(self):
        s=source();s["children"][0]["ware_err"]="20개 박스"
        o=assemble(s)[0]
        self.assertEqual(review_issues(o,RULES),[])
        self.assertEqual(summarize([o],RULES)["invoice_total"],"17920")
        text=invoice_message(o,RULES)
        self.assertTrue(text.startswith("안녕하세요.\n\n주문번호 "))
        self.assertIn("박스 초과 사용 청구: 17,926원",text)
        self.assertIn("최종 청구금액(VAT 포함): 17,920원",text)

    def test_undecided_additional_fee_is_not_a_copyable_invoice(self):
        o=assemble(source())[0];o["boxes"]=20
        summary=summarize([o],RULES)
        self.assertEqual(summary["pending"],1)
        self.assertEqual(summary["invoice_count"],0)


class ParserTests(SimpleTestCase):
    def test_tracking_link_uses_the_real_search_field_and_escapes_query_values(self):
        for tracking in ("753560928","AB-1234","123&shOrdNo=other"):
            with self.subTest(tracking=tracking):
                url=urlparse(tracking_url(tracking));query=parse_qs(url.query,keep_blank_values=True)
                self.assertEqual(url.netloc,"www.jungpan.com")
                self.assertEqual(url.path,"/Admin/1688/Order_L.asp")
                self.assertEqual(query["shChTrkNo"],[tracking])
                self.assertEqual(query["shBeginDay"],["2000-01-01"])
                self.assertNotIn("shOrdNo",query)

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
        for name in ("WareErrMemo", "AdmMemo"):
            html += f'<input id="{name}18053" value="">'
        html += '<select id="BoxCnt18053"><option selected value="0">0</option></select>'
        c = parse_child(html, {"seq": "18053", "number": "202611170061"})
        self.assertEqual(c["amount"], "9.3")
        self.assertEqual(c["recipient"], "테스트 고객")
        self.assertNotIn("manager_memo",c)


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

    def test_public_app_opens_and_creates_requests_without_employee_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/batches/").status_code, 200)
        r=self.client.post("/api/batches/",json.dumps({"bl":"202611190043"}),content_type="application/json")
        self.assertEqual(r.status_code,201)
        self.assertIsNone(Batch.objects.get(bl="202611190043").created_by)
        self.assertRedirects(self.client.get("/login/"),"/")

    def test_source_links_preserve_every_tracking_on_the_order(self):
        self.batch.orders[0]["trackings"]=["753560928","79049245784592"]
        self.batch.save()
        data=self.client.get(f"/api/batches/{self.batch.id}/").json()
        links=data["decisions"][0]["source_links"]
        self.assertEqual([l["tracking"] for l in links],["753560928","79049245784592"])
        for link in links:
            self.assertEqual(parse_qs(urlparse(link["url"]).query)["shChTrkNo"],[link["tracking"]])

    def test_portal_mount_keeps_page_assets_and_api_under_its_path(self):
        r=self.client.get("/bl-cost/")
        self.assertContains(r,'data-app-root="/bl-cost"')
        self.assertContains(r,f'href="/bl-cost{static("app.css")}"')
        self.assertContains(r,f'src="/bl-cost{static("app.js")}"')
        self.assertContains(r,"물류 작업 도구")
        data=self.client.get(f"/bl-cost/api/batches/{self.batch.id}/").json()
        self.assertEqual(data["bl"],self.batch.bl)
        self.assertContains(self.client.get("/"),'data-app-root=""')

    def test_portal_preview_accepts_its_csrf_origin_and_rejects_untrusted_sites(self):
        client=Client(enforce_csrf_checks=True,HTTP_X_FORWARDED_PROTO="https")
        client.get("/bl-cost/")
        token=client.cookies["csrftoken"].value
        data=json.dumps({"revision":0,"order_id":"17974","changes":{"extra_hours":"0.5","reason":"OPP"}})
        url=f"/bl-cost/api/batches/{self.batch.id}/preview/"
        r=client.post(url,data,content_type="application/json",HTTP_X_CSRFTOKEN=token,HTTP_ORIGIN="https://jp-pack.netlify.app")
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["calculation"]["total"],"8250")
        self.assertEqual(client.post(url,data,content_type="application/json",HTTP_X_CSRFTOKEN=token,HTTP_ORIGIN="https://unknown.netlify.app").status_code,403)

    def test_existing_draft_reclassifies_without_overwriting_source_or_decisions(self):
        o=self.batch.orders[0]
        o.update(memo="3개 부족",opp=None,boxes=None,review_required=True,manager_memo="이미 처리된 추가 비용",extra_amount="1000",reason="기존 결정 보존")
        self.batch.save()
        result=self.client.get(f"/api/batches/{self.batch.id}/").json()
        self.assertEqual(result["summary"]["pending"],0)
        self.assertEqual(result["decisions"],[])
        self.assertNotIn("orders",result)
        self.assertNotIn("manager_memo",result["invoices"][0])
        self.assertEqual(result["invoices"][0]["extra_amount"],"1000")
        self.batch.refresh_from_db()
        self.assertTrue(self.batch.orders[0]["review_required"])
        self.assertEqual(self.batch.source,source())

    def test_declining_fee_removes_free_order_from_customer_view(self):
        r=self.post("edit",order_id="17974",changes={"reviewed":True,"extra_hours":"0","extra_amount":"0"})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["decisions"],[])
        self.assertEqual(r.json()["invoices"],[])
        self.assertEqual(len(r.json()["completed_decisions"]),1)
        self.assertEqual(r.json()["completed_decisions"][0]["extra_amount"],"0")
        self.batch.refresh_from_db()
        self.assertTrue(self.batch.source["products"])

    def test_extra_fee_updates_invoice_total_and_customer_message(self):
        r=self.post("edit",order_id="17974",changes={"reviewed":True,"extra_hours":".5","extra_amount":"1000","reason":"OPP 부자재"})
        self.assertEqual(r.status_code,200)
        data=r.json()
        self.assertEqual(data["summary"]["invoice_total"],"9250")
        self.assertEqual(len(data["invoices"]),1)
        self.assertIn("최종 청구금액(VAT 포함): 9,250원",data["invoices"][0]["message"])
        self.assertIn("별도 추가금액: 1,000원",data["invoices"][0]["message"])

    def test_duplicate_bl_returns_same_batch(self):
        r = self.client.post("/api/batches/", json.dumps({"bl": self.batch.bl}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Batch.objects.count(), 1)

    def test_public_user_can_request_save_and_retry_without_staff_role(self):
        self.client.logout()
        self.batch.orders[0].update(reviewed=True,extra_amount="1000",reason="OPP 비용")
        self.batch.save()
        with patch("ledger.views.Sheets") as sheets:
            sheets.return_value.rules.return_value=RULES
            sheets.return_value.prepare.return_value={"targets":{},"write_key":"test"}
            self.assertEqual(self.post("approve").status_code,200)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.state,"approved")
        self.assertIsNone(self.batch.approved_by)
        self.assertIsNotNone(self.batch.approved_at)
        self.batch.state="error";self.batch.save()
        r=self.post("retry",revision=self.batch.revision)
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["state"],"approved")

    def test_completed_and_saved_decisions_can_be_corrected_without_sheet_write(self):
        self.client.logout()
        self.batch.orders[0].update(reviewed=True,extra_amount="1000",reason="OPP 비용")
        self.batch.state="saved";self.batch.sheet_result={"verified":True};self.batch.save()
        with patch("ledger.views.Sheets") as sheets:
            self.assertEqual(self.post("preview",order_id="17974",changes={"extra_amount":"0"}).status_code,200)
            r=self.post("edit",order_id="17974",changes={"extra_amount":"0","reviewed":True})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["state"],"review")
        self.assertEqual(r.json()["invoices"],[])
        self.assertEqual(r.json()["completed_decisions"][0]["extra_amount"],"0")
        self.assertTrue(r.json()["sheet_result"]["verified"])
        sheets.assert_not_called()

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


class SheetSyncTests(SimpleTestCase):
    def setUp(self):
        self.client=object.__new__(Sheets)
        self.order=assemble(source())[0]
        self.order.update(reviewed=True,extra_amount="2000",reason="OPP 비용")
        self.batch=SimpleNamespace(id="batch-test",bl=source()["bl"],orders=[self.order],rules=RULES,
                                   approved_at="2026-11-24T13:00:00Z",sheet_plan={})
        old=build_rows(self.batch)[BILL][0]
        self.rows={4:old}
        self.meta={"sheets":[{"properties":{"title":BILL,"sheetId":1,"gridProperties":{"rowCount":100}}}]}

    def values(self,ranges,**kwargs):
        if ranges[-1].endswith("A3:AD3"):
            return [[self.rows.get(i,[])[1:5] for i in range(4,max(self.rows,default=3)+1)], [["header"]]]
        result=[]
        for r in ranges:
            rownum=int(re.search(r"!A(\d+)",r)[1])
            result.append([list(self.rows[rownum])])
        return result

    def prepare(self):
        with patch.object(self.client,"metadata",return_value=self.meta),patch.object(self.client,"values",side_effect=self.values),patch("ledger.sheets.BILL_HEADER_HASH",fingerprint(["header"])):
            return self.client.prepare(self.batch)

    def test_existing_bl_updates_matching_row_and_only_appends_new_charge(self):
        other=list(self.rows[4]);other[1]="202611010001";self.rows[5]=other
        new={**self.order,"id":"new-order","trackings":["new-tracking"],"extra_amount":"5000"}
        self.batch.orders.append(new)
        plan=self.prepare();self.batch.sheet_plan=plan
        self.assertEqual(plan["targets"][BILL]["rows"],[4,6])
        self.assertEqual(plan["targets"][BILL]["added_rows"],[6])
        self.assertTrue(plan["before"])
        formulas=formula_rows(self.batch,plan)[BILL]
        self.assertIn("Y4",formulas[0][25]);self.assertIn("Y6",formulas[1][25])
        with patch.object(self.client,"marker",return_value=None),patch.object(self.client,"prepare",return_value=plan),patch.object(self.client,"call") as call,patch.object(self.client,"verify",return_value={"verified":True}):
            self.assertTrue(self.client.save(self.batch)["verified"])
        updates=[r["updateCells"] for r in call.call_args.kwargs["json"]["requests"] if "updateCells" in r and r["updateCells"]["start"]["sheetId"]==1]
        self.assertEqual([u["start"]["rowIndex"] for u in updates],[3,5])
        self.assertTrue(all(len(u["rows"])==1 for u in updates))

    def test_legacy_single_tracking_matches_order_with_multiple_trackings(self):
        self.order["trackings"].append("another-tracking")
        self.assertEqual(self.prepare()["targets"][BILL]["rows"],[4])

    def test_correcting_charge_to_zero_updates_old_row_instead_of_leaving_old_invoice(self):
        self.order["extra_amount"]="0"
        plan=self.prepare()
        self.assertEqual(plan["targets"][BILL]["rows"],[4])
        self.assertEqual(plan["targets"][BILL]["added_rows"],[])
        self.assertEqual(build_rows(self.batch,plan["order_ids"])[BILL][0][25],0)
        self.assertEqual(build_rows(self.batch)[BILL],[])

    def test_duplicate_or_unmatched_positive_legacy_rows_stop_before_writing(self):
        self.rows[5]=list(self.rows[4])
        with self.assertRaisesMessage(DataError,"여러 행"):
            self.prepare()
        del self.rows[5];self.rows[4][4]="unknown-tracking"
        with self.assertRaisesMessage(DataError,"연결하지 못했습니다"):
            self.prepare()

    def test_sheet_edit_after_confirmation_requires_reconfirmation(self):
        self.batch.sheet_plan=self.prepare()
        self.rows[4][29]="사용자가 시트에서 직접 수정"
        changed=self.prepare()
        with patch.object(self.client,"marker",return_value=None),patch.object(self.client,"prepare",return_value=changed),patch.object(self.client,"call") as call:
            with self.assertRaises(ReviewRequired):
                self.client.save(self.batch)
        call.assert_not_called()

    def test_write_marker_is_specific_to_each_approved_save(self):
        plan=self.prepare();self.batch.sheet_plan=plan
        meta={"developerMetadata":[{"metadataKey":"blcost_batch","metadataValue":plan["write_key"]}]}
        self.assertIsNotNone(self.client.marker(self.batch,meta))
        self.batch.approved_at="2026-11-24T13:05:00Z"
        self.batch.sheet_plan=self.prepare()
        self.assertIsNone(self.client.marker(self.batch,meta))

    def test_verification_checks_updated_rows_and_preserves_long_ids(self):
        plan=self.prepare();self.batch.sheet_plan=plan
        data=build_rows(self.batch,plan["order_ids"])
        actual=[[[v for v in row]] for rows in data.values() for row in rows]
        with patch.object(self.client,"marker",return_value={"metadataId":1}),patch.object(self.client,"values",return_value=actual):
            self.assertTrue(self.client.verify(self.batch)["verified"])
            actual[0][0][25]=999
            with self.assertRaisesMessage(DataError,"검산"):
                self.client.verify(self.batch)
