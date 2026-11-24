import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

WORK_TYPES = ("바코드", "스티커", "재봉", "벌크작업", "인쇄스티커", "도장", "작업시간", "작업필요없음")


class DataError(ValueError):
    pass


class ReviewRequired(DataError):
    pass


def decimal(value, *, minimum=Decimal("0"), maximum=Decimal("1000000000")):
    try:
        n = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError):
        raise DataError("유효한 숫자를 입력해 주세요.") from None
    if not n.is_finite() or n < minimum or n > maximum:
        raise DataError("숫자가 허용 범위를 벗어났습니다.")
    return n


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def source_fingerprint(source):
    return fingerprint({**source, "children": [{k: v for k, v in c.items() if k != "manager_memo"}
                                               for c in source.get("children", [])]})


def review_issues(order, rules):
    memo = order.get("memo", "")
    issues = []
    def add(kind, text, action):
        issues.append({"kind": kind, "text": text, "action": action})
    if order.get("opp"):
        add("opp", f"opp 봉투 {order['opp']}개 사용", "추가 청구할 시간 또는 금액을 정하세요. 추가하지 않으면 두 항목을 0으로 두세요.")
    if re.search(r"비용|청구|받아야|유료|추가\s*(?:금액|요금|작업비)", memo):
        add("cost", "입고오류메시지에 별도 비용 안내가 있습니다", "원문의 비용을 추가 청구시간 또는 별도 추가금액에 반영하세요. 이미 기본 청구에 포함됐다면 0으로 두세요.")
    instructions = re.search(r"해주세요|해\s*주세요|사용\s*예정|요청사항", memo)
    if not instructions and re.search(r"사용|소모", memo) and re.search(r"opp|봉투|박스|뽁뽁이|뾱뾱이", memo, re.I):
        missing = (re.search(r"opp|봉투", memo, re.I) and order.get("opp") is None) or ("박스" in memo and order.get("boxes") is None)
        if missing:
            add("quantity", "메모의 포장재 사용량을 확정하지 못했습니다", "입고오류메시지의 실제 사용량을 확인해 수량을 입력한 뒤 추가 청구 여부를 정하세요.")
    for warning in order.get("warnings", []):
        if warning.startswith("박스 선택값"):
            add("quantity", warning, "입고오류메시지와 실제 사용량을 대조해 박스 실사용량을 확정하세요.")
        elif warning.startswith("같은 1688"):
            add("free_hours", "같은 오더넘버가 다른 하위 주문에도 있습니다", "무료시간이 중복 적용되는지 확인하고, 필요하면 무료시간 직접 보정에 입력하세요.")
    if not order.get("customer"):
        add("customer", "고객명이 비어 있습니다", "청구할 고객명을 입력하세요.")
    if order.get("mode") not in ("직접수령", "로켓그로스"):
        add("mode", "입고방식을 확인해야 합니다", "직접수령 또는 로켓그로스를 선택해야 무료시간을 계산할 수 있습니다.")
    return issues


def first_work(text):
    result = {}
    for match in re.finditer(r"([^\n:]+?)\s*:\s*(\d+(?:\.\d+)?)", text):
        name, value = match.groups()
        name = name.strip()
        if name not in WORK_TYPES:
            raise DataError(f"알 수 없는 작업유형입니다: {name}")
        result.setdefault(name, str(decimal(value)))
    if text.strip() and not result:
        raise DataError("작업내역의 형식을 확인해 주세요.")
    return result


def packaging(memo):
    # shortcut: 사용량이 분명한 한국어 메모만 자동 추출, 다른 표현은 원문을 보고 검토한다.
    out = {"opp": None, "boxes": None}
    if re.search(r"요청사항|해주세요|해 주세요|사용\s*예정|취소|수정|추가", memo):
        return out
    kinds = {"opp": r"OPP\s*(?:봉투)?", "boxes": r"박스"}
    for key, word in kinds.items():
        patterns = [rf"(\d+)\s*(?:개\s*)?{word}\s*(?:사용)?", rf"{word}\s*(?:사용)?\s*(\d+)\s*(?:개)?"]
        values = [int(m.group(1)) for pat in patterns for m in re.finditer(pat, memo, re.I)]
        if values and len(set(values)) == 1:
            out[key] = values[0]
    return out


def assemble(source):
    by_child = {str(c["seq"]): c for c in source["children"]}
    if len(by_child) != len(source["children"]) or not by_child:
        raise DataError("하위 주문 목록이 비어 있거나 중복되었습니다.")
    grouped = {}
    work_groups = {}
    seen_units = set()
    shop_owners = {}
    for p in source["products"]:
        if p["unit"] in seen_units or not p.get("order") or not p.get("tracking"):
            raise DataError("상품 식별번호가 중복되었거나 오더넘버·트래킹이 누락되었습니다.")
        seen_units.add(p["unit"])
        child = by_child.get(str(p["child_seq"]))
        if not child:
            raise DataError("상품과 하위 주문의 연결이 맞지 않습니다.")
        key = str(child["seq"])
        if key not in grouped:
            pack = packaging(child.get("ware_err", ""))
            box_field = decimal(child.get("box_field") or 0)
            if box_field != box_field.to_integral_value():
                raise DataError("중판 박스 선택값이 정수가 아닙니다.")
            box_conflict = bool(box_field and pack["boxes"] is not None and box_field != pack["boxes"])
            if box_field and pack["boxes"] is None:
                pack["boxes"] = int(box_field)
            memo = child.get("ware_err", "").strip()
            ship = child.get("shipping", "")
            mode = "로켓그로스" if "쿠팡로켓그로스" in ship else "직접수령" if "지정배송지" in ship else ""
            grouped[key] = {"id": key, "jungpan_order": child["number"], "shop_orders": [], "trackings": [],
                "customer": child.get("recipient", ""), "member": child.get("member", ""), "mode": mode,
                "amount": str(decimal(child["amount"])), "work": {}, "products": [], "memo": memo,
                "admin_memo": child.get("admin_memo", ""),
                "opp": pack["opp"], "boxes": pack["boxes"], "cbm": "0", "warnings": [],
                "review_required": False, "reviewed": False,
                "extra_hours": "0", "extra_amount": "0", "reason": "", "waive_work": False,
                "waive_total": False, "manual_total": None, "free_hours_override": None}
            if box_conflict:
                grouped[key]["warnings"].append(f"박스 선택값 {box_field}개와 메모 {pack['boxes']}개가 다릅니다. 실사용량을 확인해 주세요.")
                grouped[key]["review_required"] = True
        o = grouped[key]
        o["products"].append(p["unit"])
        for field, value in (("shop_orders", p["order"]), ("trackings", p["tracking"])):
            if value not in o[field]:
                o[field].append(value)
        shop_owners.setdefault(p["order"], set()).add(key)
        for name, value in first_work(p.get("work", "")).items():
            wk = (key, p["tracking"], name)
            if wk in work_groups and work_groups[wk] != value:
                raise DataError("같은 트래킹의 작업수량이 서로 다릅니다. 원본 확인이 필요합니다.")
            work_groups[wk] = value
    for (child, _, name), value in work_groups.items():
        w = grouped[child]["work"]
        w[name] = str(decimal(w.get(name, "0")) + decimal(value))
    for o in grouped.values():
        if any(len(shop_owners[n]) > 1 for n in o["shop_orders"]):
            o["warnings"].append("같은 1688 오더넘버가 다른 하위 주문에도 있습니다. 무료시간 적용 단위를 확인해 주세요.")
            o["review_required"] = True
        if o["opp"] or o["boxes"]:
            o["warnings"].append("포장재 규격이 없어 원가표의 기준 단가를 적용합니다.")
        o["review_required"] = bool(review_issues(o, {}))
    return list(grouped.values())


def calculate(order, rules):
    if not rules:
        return {"ready": False, "error": "구글시트 청구규칙·원가표·월별 환율을 연결해 주세요."}
    try:
        rate = decimal(rules["exchange"])
        if not rate or order["mode"] not in ("직접수령", "로켓그로스"):
            raise DataError("환율 또는 입고방식을 확인해 주세요.")
        amount = decimal(order["amount"])
        bands = rules["free_hours"]
        applicable = [b for b in bands if amount >= decimal(b[0])]
        if not applicable:
            raise DataError("주문금액에 해당하는 무료시간 구간이 없습니다.")
        band = max(applicable, key=lambda b: decimal(b[0]))
        free = decimal(band[1 if order["mode"] == "직접수령" else 2])
        if order.get("free_hours_override") is not None:
            free = decimal(order["free_hours_override"], maximum=Decimal("10000"))
        actual = decimal(order["work"].get("작업시간", "0"))
        charge_hours = max(Decimal("0"), actual - free)
        extra_hours = decimal(order.get("extra_hours", "0"), maximum=Decimal("10000"))
        extra_amount = decimal(order.get("extra_amount", "0"))
        vat = decimal(rules["vat"], maximum=Decimal("1"))
        hourly = decimal(rules["hourly"])
        work = Decimal("0") if order.get("waive_work") else charge_hours * hourly * (1 + vat)
        extra_time = extra_hours * hourly * (1 + vat)
        boxes = decimal(order.get("boxes") or 0)
        excess = max(Decimal("0"), boxes - decimal(rules["free_boxes"]))
        box_charge = (excess * decimal(rules["box_cny"]) * rate * (1 + vat)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        cbm_charge = (max(Decimal("0"), decimal(order.get("cbm", "0")) - Decimal("3")) * Decimal("10000") * (1 + vat)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        base = work + box_charge + cbm_charge
        total = base + extra_time + extra_amount
        if order.get("manual_total") is not None:
            total = decimal(order["manual_total"])
        if order.get("waive_total"):
            total = Decimal("0")
        total = (total / 10).to_integral_value(rounding=ROUND_DOWN) * 10
        unit_costs = rules["costs"]
        cost = actual * decimal(unit_costs["hourly_cny"]) * rate
        cost += decimal(order.get("opp") or 0) * decimal(unit_costs["opp_cny"]) * rate
        cost += boxes * decimal(unit_costs["box_cny"]) * rate
        for name, key, cny in [("바코드", "barcode_krw", False), ("스티커", "sticker_cny", True), ("재봉", "sewing_cny", True)]:
            cost += decimal(order["work"].get(name, "0")) * decimal(unit_costs[key]) * (rate if cny else 1)
        cost += decimal(order.get("cbm", "0")) * decimal(unit_costs["cbm_cny"]) * rate
        return {"ready": True, "actual_hours": str(actual), "free_hours": str(free), "charge_hours": str(charge_hours),
                "work_charge": str(work), "extra_time_charge": str(extra_time), "box_excess": str(excess),
                "box_charge": str(box_charge), "cbm_charge": str(cbm_charge), "base": str(base),
                "extra_amount": str(extra_amount), "total": str(total), "cost": str(cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                "margin": str((total - cost).quantize(Decimal("1"), rounding=ROUND_HALF_UP))}
    except (DataError, KeyError, IndexError):
        return {"ready": False, "error": "계산에 필요한 주문정보·규칙·단가를 확인해 주세요."}


def summarize(orders, rules):
    totals = {}
    for o in orders:
        for k, v in o["work"].items():
            totals[k] = str(decimal(totals.get(k, 0)) + decimal(v))
    calcs = [calculate(o, rules) for o in orders]
    invoices = [c for o,c in zip(orders,calcs) if c["ready"] and decimal(c["total"]) > 0
                and (o["reviewed"] or not review_issues(o,rules))]
    return {"orders": len(orders), "products": sum(len(o["products"]) for o in orders), "work": totals,
            "pending": sum(1 for o in orders if review_issues(o, rules) and not o["reviewed"]),
            "total": str(sum((decimal(c["total"]) for c in calcs if c["ready"]), Decimal("0"))),
            "invoice_count":len(invoices), "invoice_total":str(sum((decimal(c["total"]) for c in invoices),Decimal("0"))),
            "calculation_ready": all(c["ready"] for c in calcs)}


def invoice_message(order, rules):
    c = calculate(order,rules)
    if not c["ready"] or not decimal(c["total"]):
        return ""
    q = lambda v: format(decimal(v).normalize(),"f")
    charge_hours=(Decimal(0) if order.get("waive_work") else decimal(c["charge_hours"]))+decimal(order["extra_hours"])
    lines = ["안녕하세요.", "", f"주문번호 {', '.join(order['shop_orders'])} 추가 비용 안내드립니다.",
        f"- {order['mode']} / 총 작업시간 {q(c['actual_hours'])}시간 / 무료시간 {q(c['free_hours'])}시간 / 청구시간 {q(charge_hours)}시간"]
    if decimal(c["box_charge"]):
        lines.append(f"- 박스 초과 사용 청구: {decimal(c['box_charge']):,.0f}원")
    if decimal(c["cbm_charge"]):
        lines.append(f"- CBM {q(order['cbm'])} / 초과 {q(max(Decimal(0),decimal(order['cbm'])-3))}CBM 청구: {decimal(c['cbm_charge']):,.0f}원")
    if decimal(order["extra_hours"]):
        lines.append(f"- 별도 추가 청구시간 {q(order['extra_hours'])}시간: {decimal(c['extra_time_charge']):,.0f}원")
    if decimal(order["extra_amount"]):
        lines.append(f"- 별도 추가금액: {decimal(order['extra_amount']):,.0f}원")
    if order["reason"] and (decimal(order["extra_hours"]) or decimal(order["extra_amount"]) or order.get("manual_total") is not None or order.get("waive_work")):
        lines.append(f"- 안내: {order['reason']}")
    if order.get("waive_work"):
        lines.append("- 기본 작업비 면제 적용")
    lines += [f"- 최종 청구금액(VAT 포함): {decimal(c['total']):,.0f}원", "", "확인 부탁드리며, 비용은 하단 링크에서 결제 부탁드립니다."]
    return "\n".join(lines)
