"""작업 원본과 청구 결정을 구분해 사이트·시트에 같은 보고서를 제공합니다."""
from decimal import Decimal
import re

from django.db import transaction
from django.utils import timezone

from .domain import DataError, assemble, calculate, decimal, summarize
from .models import Batch, ReportSnapshot

WORK = ("작업시간", "인쇄스티커", "스티커", "바코드", "벌크작업", "재봉", "도장")
WORK_LABELS = {"작업시간": "작업시간", "인쇄스티커": "인쇄스티커", "스티커": "스티커", "바코드": "바코드", "벌크작업": "벌크", "재봉": "재봉", "도장": "도장"}
SUMMARY_SHEET = "시스템 집계"
SUMMARY_HEADERS = ["BL", "작업월", "자료 출처", "하위 주문 수", "상품 행 수", "실제 작업시간", "인쇄스티커", "스티커", "바코드", "벌크작업", "4종 합계", "재봉", "도장", "재봉·도장 합계", "검산 상태", "확인사항", "확정 청구 건수", "확정 청구액", "추가 비용 결정 대기", "입금 기록액", "미결제 기록액", "결제상태 미입력액", "결제 보류액", "참고 원가", "원가 기준", "원본 조회 시각", "청구 시트 대조", "청구액 차이"]


def valid_bl(value):
    value = str(value).strip()
    if not re.fullmatch(r"20\d{10}", value):
        raise DataError("보고서 원본의 BL 번호 형식을 확인해 주세요.")
    return value


def at(row, index, default=""):
    return row[index] if len(row) > index and row[index] not in (None, "") else default


def work_values(values):
    return {k: str(decimal(v or 0)) for k, v in zip(WORK, values)}


def legacy_source(raw, costs, cost_bls, cost_amounts, bill_bls, bills):
    """기존 검산의 원본/원가 QUERY는 보존하고 그 집계만 읽습니다."""
    result = {"legacy": {}, "billing": {}}
    for row in raw:
        if not at(row, 0):
            continue
        bl = valid_bl(row[0])
        if bl in result["legacy"]:
            raise DataError("기존 BL 작업 집계에 중복 BL이 있습니다.")
        result["legacy"][bl] = {"products": int(decimal(at(row, 1, 0))),
            "work": work_values([at(row, i, 0) for i in range(2, 9)]), "cost": None, "cost_check": "미대조"}
    for row in costs:
        if not at(row, 0):
            continue
        bl = valid_bl(row[0])
        if bl not in result["legacy"]:
            raise DataError("원가 정산에만 있는 BL이 있습니다. 기존 검산 원본을 확인해 주세요.")
        old = result["legacy"][bl]
        expected = [old["products"]] + [old["work"][k] for k in ("작업시간", "바코드", "스티커", "재봉", "벌크작업", "도장")]
        old["cost_check"] = "일치" if all(decimal(at(row, i+1, 0)) == decimal(v) for i, v in enumerate(expected)) else "확인 필요"
    for i, row in enumerate(cost_bls):
        if not at(row, 0):
            continue
        bl = valid_bl(row[0])
        if bl not in result["legacy"]:
            raise DataError("원가 집계와 BL 목록이 다릅니다.")
        old = result["legacy"][bl]
        amount = at(cost_amounts[i], 0, 0) if i < len(cost_amounts) else 0
        old["cost"] = str(decimal(old["cost"] or 0) + decimal(amount))
    for i, row in enumerate(bill_bls):
        if not at(row, 0):
            continue
        bl = valid_bl(row[0])
        values = bills[i] if i < len(bills) else []
        amount = decimal(at(values, 0, 0))
        if not amount:
            continue
        bill = result["billing"].setdefault(bl, {"count": 0, "total": "0", "paid": "0", "unpaid": "0", "unknown": "0", "held": "0"})
        bill["count"] += 1
        bill["total"] = str(decimal(bill["total"]) + amount)
        status = str(at(values, 1)).strip()
        field = {"결제완료": "paid", "미결제": "unpaid", "보류": "held"}.get(status, "unknown")
        bill[field] = str(decimal(bill[field]) + amount)
    return result


def totals(rows):
    summed = {"bl_count": len(rows), "products": sum(r["products"] for r in rows),
              "work": {k: str(sum((decimal(r["work"].get(k, 0)) for r in rows), Decimal(0))) for k in WORK},
              "invoice_count": sum(r["invoice_count"] for r in rows),
              "pending": sum(r["pending"] or 0 for r in rows),
              "pending_unknown": sum(r["pending"] is None for r in rows),
              "needs_check": sum(bool(r["differences"]) or r["verification"] == "확인 필요" for r in rows),
              "legacy_count": sum(r["source"] == "기존 시트" for r in rows),
              "work_missing": sum(r["source"] == "청구 시트만 존재" for r in rows),
              "cost_missing": sum(r["cost"] is None for r in rows)}
    for key in ("invoice_total", "paid", "unpaid", "unknown", "held", "cost"):
        summed[key] = str(sum((decimal(r.get(key) or 0) for r in rows), Decimal(0)))
    summed["four_total"] = str(sum((decimal(summed["work"][k]) for k in WORK[1:5]), Decimal(0)))
    summed["two_total"] = str(sum((decimal(summed["work"][k]) for k in WORK[5:]), Decimal(0)))
    return summed


def build_report(source, batches, previous=None):
    rows = {}
    for bl, old in source["legacy"].items():
        rows[bl] = {"bl": bl, "batch_id": None, "source": "기존 시트", "source_at": None, "orders": None,
            "products": old["products"], "work": old["work"], "pending": None, "cost": old["cost"],
            "cost_basis": "기존 원가 정산 집계", "verification": "기존 원가 대조 " + old["cost_check"],
            "differences": [] if old["cost_check"] == "일치" else ["기존 주문 데이터·원가 정산 집계 대조 필요"], "legacy": None}
    # 재조회 중에는 이전에 완성된 집계를 유지하고, 그 시각과 진행 상태를 표시합니다.
    for row in (previous or {}).get("rows", []):
        if row["source"] == "중판 조회":
            rows[row["bl"]] = {**row, "differences": [d for d in row["differences"] if not d.startswith(("확정 청구와", "최신 중판 조회"))]}
    for batch in batches:
        if not batch.orders or not batch.collected_at:
            if batch.bl in rows and batch.state in ("queued", "fetching", "error"):
                rows[batch.bl]["differences"].append("최신 중판 조회 미완료 · 이전 집계 유지")
            continue
        summary = summarize(batch.orders, batch.rules)
        differences = []
        verification = "미대조"
        try:
            original = summarize(assemble(batch.source), {})
            same = summary["orders"] == original["orders"] and summary["products"] == original["products"] and all(decimal(summary["work"].get(k, 0)) == decimal(original["work"].get(k, 0)) for k in WORK)
            complete = original["orders"] == len(batch.source.get("children", []))
            verification = "원본·DB 일치" if same and complete else "확인 필요"
            if not same or not complete:
                differences.append("수집 원본과 DB 작업 집계 차이")
        except (DataError, KeyError, TypeError):
            differences.append("수집 원본 대조 불가")
            verification = "확인 필요"
        old = source["legacy"].get(batch.bl)
        if old:
            if old["products"] != summary["products"]:
                differences.append(f"상품 행: 기존 시트 {old['products']} / 중판 {summary['products']}")
            for k in WORK:
                before, after = decimal(old["work"].get(k, 0)), decimal(summary["work"].get(k, 0))
                if before != after:
                    differences.append(f"{WORK_LABELS[k]}: 기존 시트 {before} / 중판 {after}")
        calcs = [calculate(o, batch.rules) for o in batch.orders]
        cost = str(sum((decimal(c["cost"]) for c in calcs), Decimal(0))) if all(c["ready"] for c in calcs) else None
        rows[batch.bl] = {"bl": batch.bl, "batch_id": str(batch.id), "source": "중판 조회", "source_at": batch.collected_at.isoformat(),
            "orders": summary["orders"], "products": summary["products"], "work": {k: summary["work"].get(k, "0") for k in WORK},
            "pending": summary["pending"], "invoice_count": summary["invoice_count"], "invoice_total": summary["invoice_total"],
            "cost": cost, "cost_basis": "조회 당시 단가 기준 추정 · CBM·포장 규격 미확정",
            "verification": verification, "differences": differences, "legacy": old}
        if not summary["calculation_ready"]:
            differences.append("청구규칙·원가 계산 미완료")
    for bl, bill in source["billing"].items():
        if bl not in rows:
            rows[bl] = {"bl": bl, "batch_id": None, "source": "청구 시트만 존재", "source_at": None, "orders": None,
                "products": 0, "work": {k: "0" for k in WORK}, "pending": None, "cost": None, "cost_basis": "작업 원본 없음",
                "verification": "확인 필요", "differences": ["청구 기록에 연결된 작업 원본 없음"], "legacy": None}
    for bl, row in rows.items():
        bill = source["billing"].get(bl, {"count": 0, "total": "0", "paid": "0", "unpaid": "0", "unknown": "0", "held": "0"})
        if row["source"] != "중판 조회":
            row.update(invoice_count=bill["count"], invoice_total=bill["total"])
        row.update({k: bill[k] for k in ("paid", "unpaid", "unknown", "held")})
        row["sheet_total"] = bill["total"]
        row["sheet_difference"] = str(decimal(row["invoice_total"]) - decimal(bill["total"]))
        row["sheet_check"] = "일치" if not decimal(row["sheet_difference"]) and row["invoice_count"] == bill["count"] else "확인 필요"
        if row["sheet_check"] != "일치":
            row["differences"].append("확정 청구와 시트 청구 건수·금액 차이")
        row["month"] = f"{bl[:4]}-{bl[4:6]}"
        row["four_total"] = str(sum((decimal(row["work"][k]) for k in WORK[1:5]), Decimal(0)))
        row["two_total"] = str(sum((decimal(row["work"][k]) for k in WORK[5:]), Decimal(0)))
    ordered = sorted(rows.values(), key=lambda r: r["bl"], reverse=True)
    months = [{"month": month, **totals([r for r in ordered if r["month"] == month])} for month in sorted({r["month"] for r in ordered}, reverse=True)]
    return {"rows": ordered, "months": months, "totals": totals(ordered)}


def queue_reports():
    snapshot, _ = ReportSnapshot.objects.get_or_create(pk=1)
    ReportSnapshot.objects.filter(pk=1).update(requested_at=timezone.now(), state="queued" if snapshot.state != "refreshing" else "refreshing", error="")


def sync_reports(client):
    snapshot = ReportSnapshot.objects.get(pk=1)
    requested_at = snapshot.requested_at
    ReportSnapshot.objects.filter(pk=1).update(state="refreshing", error="")
    source = client.report_source()
    data = build_report(source, list(Batch.objects.all()), snapshot.data)
    if not snapshot.backup:
        snapshot.backup = client.report_backup()
        snapshot.save(update_fields=["backup"])
    receipt = client.save_reports(data)
    with transaction.atomic():
        current = ReportSnapshot.objects.select_for_update().get(pk=1)
        current.source, current.data, current.receipt = source, data, receipt
        current.synced_at, current.error = timezone.now(), ""
        current.state = "ready" if current.requested_at == requested_at else "queued"
        current.save()
