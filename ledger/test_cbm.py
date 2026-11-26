import json
from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase, TestCase

from .domain import BOX_DIMENSIONS, DataError, assemble, calculate, invoice_message, packaging, review_issues, summarize
from .models import Batch
from .sheets import BILL, build_rows
from .tests import RULES, source
from .views import edited_order, serialized


def sized_order(memo):
    s = source()
    s["children"][0]["ware_err"] = memo
    return assemble(s)[0]


class BoxVolumeTests(SimpleTestCase):
    def test_every_supplied_box_size_is_calculated_in_cubic_metres(self):
        expected = {1: ".012", 2: ".013468", 3: ".024381", 4: ".036",
                    5: ".056869", 6: ".08", 7: ".12", 8: ".04624"}
        for size, value in expected.items():
            with self.subTest(size=size):
                o = sized_order(f"{size}호 박스 1개 사용")
                self.assertEqual(Decimal(o["cbm"]), Decimal(value))
                self.assertEqual(o["boxes"], 1)
                self.assertEqual(o["box_volume"]["items"][0]["dimensions"], list(BOX_DIMENSIONS[size]))

    def test_mixed_sizes_and_total_are_not_counted_twice_or_per_product(self):
        for memo in ("중국현장 16개 7호 박스 +1개 6호박스: 17박스 사용",
                     "중국현장 7호 박스 16개 + 6호 박스 1개 / 총 17개 박스 사용",
                     "7호박스:16개 + 6호박스:1개", "7호 16개\n6호 1개"):
            with self.subTest(memo=memo):
                o = sized_order(memo)
                self.assertEqual(o["boxes"], 17)
                self.assertEqual(Decimal(o["cbm"]), Decimal("2"))
                self.assertEqual(len(o["products"]), 2)
                self.assertEqual(review_issues(o, RULES), [])
                self.assertEqual(calculate(o, RULES)["cbm_charge"], "0")

    def test_three_cbm_is_free_and_only_the_excess_is_billed(self):
        free = sized_order("7호박스 25개 사용")
        self.assertEqual(Decimal(free["cbm"]), 3)
        self.assertEqual(calculate(free, RULES)["cbm_charge"], "0")
        o = sized_order("26개 7호 박스 + 1개 6호 박스: 27박스 사용")
        c = calculate(o, RULES)
        self.assertEqual(Decimal(o["cbm"]), Decimal("3.2"))
        self.assertEqual(Decimal(c["cbm_excess"]), Decimal(".2"))
        self.assertEqual(c["cbm_charge"], "2200")
        self.assertEqual(c["box_charge"], "30474")
        self.assertEqual(c["total"], "32670")
        self.assertIn("CBM 3.2 / 초과 0.2CBM 청구: 2,200원 (VAT 포함)", invoice_message(o, RULES))

    def test_ambiguous_sizes_never_become_a_zero_cbm_invoice(self):
        for memo in ("7호 박스 사용", "9호박스 26개 사용", "7호박스 16개 + 7호박스 10개 사용",
                     "16개 7호박스 + 1개 6호박스: 18박스 사용", "7호박스 26개 + 2개 박스 사용",
                     "7호박스 -26개 사용", "16.5개 7호박스 사용", "7호박스 26개 사용 예정",
                     "7호박스 26개 취소, 20개로 수정", "7호박스 1000001개 사용",
                     "7호박스 26개 + 6.5호 1개 사용", "16개 7호박스 20개 사용",
                     "7호박스 26개 + 26박스 사용", "7호박스 26개 + 작은 박스 26개 / 총 26박스 사용"):
            with self.subTest(memo=memo):
                o = sized_order(memo)
                self.assertTrue(o["box_volume"]["error"])
                self.assertTrue(any(i["kind"] == "cbm" for i in review_issues(o, RULES)))
                self.assertFalse(calculate(o, RULES)["ready"])
                self.assertEqual(summarize([o], RULES)["invoice_count"], 0)
                b = SimpleNamespace(id="local-test", bl="202611240025", orders=[o], rules=RULES)
                self.assertEqual(build_rows(b)[BILL], [])

    def test_existing_unsized_usage_and_manual_cbm_remain_supported(self):
        o = sized_order("20개 박스 사용")
        self.assertIsNone(o["box_volume"])
        self.assertEqual(o["cbm"], "0")
        self.assertEqual(calculate(o, RULES)["total"], "17920")
        o["cbm"] = "3.2"
        self.assertEqual(calculate(o, RULES)["cbm_charge"], "2200")
        self.assertEqual(packaging("OPP 1봉씩 포장해주세요")["boxes"], None)

    def test_volume_warning_does_not_repeat_box_quantity_help_or_hide_missing_opp(self):
        self.assertEqual([i["kind"] for i in review_issues(sized_order("7호 박스 사용"), RULES)], ["cbm"])
        issues = review_issues(sized_order("OPP 봉투 사용 / 7호 박스 사용"), RULES)
        self.assertEqual({i["kind"] for i in issues}, {"cbm", "quantity"})

    def test_manual_correction_is_explicit_and_box_changes_invalidate_the_old_volume(self):
        o = sized_order("7호박스 26개 사용")
        changed = edited_order(o, {"boxes": 25}, require_reason=False)
        self.assertFalse(calculate(changed, RULES)["ready"])
        corrected = edited_order(o, {"boxes": 25, "cbm": "3", "reason": "실사용 수량 확인"})
        self.assertTrue(corrected["cbm_override"])
        self.assertEqual(calculate(corrected, RULES)["cbm_charge"], "0")
        self.assertEqual(Decimal(o["cbm"]), Decimal("3.12"))
        ambiguous = sized_order("7호 박스 사용")
        corrected = edited_order(ambiguous, {"boxes": 26, "cbm": "3.12", "reason": "현장 확인"})
        self.assertTrue(calculate(corrected, RULES)["ready"])
        self.assertFalse(edited_order(o, {"cbm": o["cbm"]}).get("cbm_override"))

    def test_blank_or_zero_volume_cannot_confirm_boxes_as_volume_free(self):
        o = sized_order("7호 박스 사용")
        for value in (None, ""):
            with self.assertRaises(DataError):
                edited_order(o, {"boxes": 26, "cbm": value}, require_reason=False)
        changed = edited_order(o, {"boxes": 26, "cbm": "0", "reason": "현장 확인"})
        self.assertFalse(calculate(changed, RULES)["ready"])

    def test_existing_sheet_columns_receive_the_same_cbm_charge_as_the_customer_message(self):
        o = sized_order("7호박스 26개 + 6호박스 1개 사용")
        b = SimpleNamespace(id="local-test", bl="202611240025", orders=[o], rules=RULES)
        row = build_rows(b)[BILL][0]
        self.assertEqual(row[19:22], [Decimal("3.2"), Decimal(".2"), Decimal("2200")])
        self.assertIn("2,200원", row[28])
        self.assertEqual(row[24], Decimal("32670"))


class BoxVolumeApiTests(TestCase):
    def setUp(self):
        self.order = sized_order("7호 박스 사용")
        self.batch = Batch.objects.create(bl="202611240025", source=source(), orders=[self.order], rules=RULES, state="review")

    def test_unresolved_volume_cannot_be_marked_as_a_billable_review(self):
        r = self.client.post(f"/api/batches/{self.batch.id}/edit/", json.dumps({"revision": 0,
            "order_id": self.order["id"], "changes": {"reviewed": True}}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.revision, 0)

    def test_volume_basis_is_exposed_and_preview_does_not_change_the_saved_order(self):
        detail = serialized(self.batch, True)
        self.assertIn("box_volume", detail["decisions"][0])
        r = self.client.post(f"/api/batches/{self.batch.id}/preview/", json.dumps({"revision": 0,
            "order_id": self.order["id"], "changes": {"boxes": 26, "cbm": "3.12"}}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["calculation"]["cbm_charge"], "1320")
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.orders[0]["cbm"])

    def test_zero_volume_save_returns_the_actual_correction_needed(self):
        r = self.client.post(f"/api/batches/{self.batch.id}/edit/", json.dumps({"revision": 0,
            "order_id": self.order["id"], "changes": {"boxes": 26, "cbm": "0", "reviewed": True, "reason": "확인"}}), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("0보다 큰 CBM", r.json()["error"])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.revision, 0)
