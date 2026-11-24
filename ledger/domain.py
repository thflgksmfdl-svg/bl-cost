import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

WORK_TYPES = ("바코드", "스티커", "재봉", "벌크작업", "인쇄스티커", "도장", "작업시간", "작업필요없음")
PACK_WORDS = re.compile(r"opp|봉투|박스|뽁뽁이|뾱뾱이|袋|箱|비용|청구|받아야", re.I)


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
            required = bool(memo.strip(". \n~"))
            ship = child.get("shipping", "")
            mode = "로켓그로스" if "쿠팡로켓그로스" in ship else "직접수령" if "지정배송지" in ship else ""
            grouped[key] = {"id": key, "jungpan_order": child["number"], "shop_orders": [], "trackings": [],
                "customer": child.get("recipient", ""), "member": child.get("member", ""), "mode": mode,
                "amount": str(decimal(child["amount"])), "work": {}, "products": [], "memo": memo,
                "manager_memo": child.get("manager_memo", ""), "admin_memo": child.get("admin_memo", ""),
                "opp": pack["opp"], "boxes": pack["boxes"], "cbm": "0", "warnings": [],
                "review_required": required or bool(pack["opp"] or pack["boxes"]), "reviewed": False,
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
    return {"orders": len(orders), "products": sum(len(o["products"]) for o in orders), "work": totals,
            "pending": sum(1 for o in orders if o["review_required"] and not o["reviewed"]),
            "total": str(sum((decimal(c["total"]) for c in calcs if c["ready"]), Decimal("0"))),
            "calculation_ready": all(c["ready"] for c in calcs)}
