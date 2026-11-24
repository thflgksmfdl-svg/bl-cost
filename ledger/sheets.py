import json
import os
import re
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from google.auth.exceptions import GoogleAuthError
from requests.exceptions import RequestException

from .domain import DataError, ReviewRequired, calculate, decimal, fingerprint

RAW = "주문 데이터"
COST = "원가 정산"
BILL = "청구 입력(내가 입력)"
APPROVAL = "시스템 승인기준"
HEADER_HASHES = {
    RAW: "272d6e4ee9bb2dcdff05a220a799aa97be5967cb0ca8b8faf27558d0076f5bb3",
    COST: "e506543b0191d60f616d83babe420bd2340cbe06703e5b23741622e928ddadda",
    BILL: "1ee2c905bb48f947301ea240b93ccd478807bdb7377513a172d677f2f2aa86df",
}
APPROVAL_HEADERS = ["시스템 고유키", "BL", "중판 주문", "승인 환율", "시간당 청구액(원)", "부가세율", "무료 박스",
    "박스 청구단가(CNY)", "시간당 원가(CNY)", "opp 원가(CNY)", "박스 원가(CNY)", "바코드 원가(원)",
    "스티커 원가(CNY)", "재봉 원가(CNY)", "CBM 원가(CNY)", "승인 무료시간", "추가 청구시간", "별도 추가금액(원)",
    "기본 작업비 면제", "전체 면제", "최종금액 직접 지정", "무료 CBM", "초과 CBM 청구단가(원)", "검토 사유", "규칙 버전",
    "원본 URL", "조회 요청", "조회 요청시각", "승인자"]


class Formula(str):
    pass


def number(value):
    return str(decimal(re.sub(r"[^\d.\-]", "", str(value))))


def cell(value):
    if value is None or value == "":
        return {}
    if isinstance(value, Formula):
        return {"userEnteredValue": {"formulaValue": str(value)}}
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return {"userEnteredValue": {"numberValue": float(value)}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def note(order, batch):
    return (f"중판 주문 {order['jungpan_order']} / 시스템 승인 {batch.id}\n"
            f"추가 청구시간 {order['extra_hours']}시간 / 별도 추가금액 {order['extra_amount']}원(VAT 포함)\n"
            f"사유: {order['reason']}\n{order['memo']}")


def build_rows(batch):
    raw, cost, bills = [], [], []
    orders = {o["id"]: o for o in batch.orders}
    seen = set()
    month = f"{batch.bl[:4]}-{batch.bl[4:6]}"
    rules = batch.rules
    fx = decimal(rules["exchange"])
    for index, p in enumerate(batch.source["products"]):
        o = orders[p["child_seq"]]
        c = calculate(o, rules)
        if not c["ready"]:
            raise DataError("확정할 수 없는 계산이 남아 있습니다.")
        first = o["id"] not in seen
        seen.add(o["id"])
        w = o["work"] if first else {}
        hours = decimal(w.get("작업시간", 0))
        opp = decimal(o.get("opp") or 0) if first else Decimal(0)
        boxes = decimal(o.get("boxes") or 0) if first else Decimal(0)
        cbm = decimal(o.get("cbm", "0")) if first else Decimal(0)
        costs = rules["costs"]
        work_cost = hours * decimal(costs["hourly_cny"]) * fx
        opp_cost = opp * decimal(costs["opp_cny"]) * fx
        box_cost = boxes * decimal(costs["box_cny"]) * fx
        barcode = decimal(w.get("바코드", 0))
        sticker = decimal(w.get("스티커", 0))
        sewing = decimal(w.get("재봉", 0))
        barcode_cost = barcode * decimal(costs["barcode_krw"])
        sticker_cost = sticker * decimal(costs["sticker_cny"]) * fx
        sewing_cost = sewing * decimal(costs["sewing_cny"]) * fx
        cbm_cost = cbm * decimal(costs["cbm_cny"]) * fx
        actual_cost = work_cost + opp_cost + box_cost + barcode_cost + sticker_cost + sewing_cost + cbm_cost
        total = decimal(c["total"]) if first else Decimal(0)
        memo = note(o, batch)
        message = (f"주문번호 {', '.join(o['shop_orders'])} 추가 비용 안내\n"
                   f"실제 작업시간 {c['actual_hours']}시간 / 무료 {c['free_hours']}시간\n"
                   f"박스 {o.get('boxes') or 0}개 / 초과 {c['box_excess']}개\n"
                   f"추가 청구시간 {o['extra_hours']}시간 / 별도 추가금액 {o['extra_amount']}원\n"
                   f"최종 청구금액(VAT 포함): {total:,.0f}원") if first and total else ""
        row = [None] * 58
        row[:17] = [month, batch.bl, o["customer"], p["order"], p["tracking"], o["mode"],
                    decimal(o["amount"]), cbm, hours, opp, boxes, 0, 0, 0, 0, 0, memo]
        row[17:28] = [fx, work_cost, opp_cost, box_cost, 0, 0, actual_cost,
                       decimal(c["free_hours"]) if first else 0,
                       decimal(c["charge_hours"]) + decimal(o["extra_hours"]) if first else 0,
                       decimal(c["work_charge"]) + decimal(c["extra_time_charge"]) if first else 0,
                       decimal(c["cbm_charge"]) if first else 0]
        row[28:41] = [0, "자동", 0, 0, "자동", 0,
                        decimal(c["box_charge"]) if first else 0, 0, 0,
                        decimal(c["base"]) if first else 0, "직접입력", total, total]
        row[41:48] = [message, total - actual_cost,
                       (total - actual_cost) / total if total else 0, actual_cost / total if total else 0,
                       decimal(w.get("인쇄스티커", 0)), f"OPP {opp}개 / 박스 {boxes}개" if first else "", 0]
        row[48:] = [p["name"], barcode, sticker, sewing, decimal(w.get("벌크작업", 0)),
                    decimal(w.get("도장", 0)), p["work"], p["status"], f"중판 상품 {p['unit']}", f"{batch.id}:{p['unit']}"]
        raw.append(row)
        cost.append([month, batch.bl, p["order"], p["tracking"], o["customer"], decimal(o["amount"]), fx,
                     cbm, decimal(costs["cbm_cny"]), cbm_cost, hours, work_cost, barcode, barcode_cost,
                     sticker, sticker_cost, sewing, sewing_cost, decimal(w.get("벌크작업", 0)), 0,
                     decimal(w.get("도장", 0)), 0, opp, opp_cost, boxes, box_cost, 0, 0, 0,
                     actual_cost, p["work"], memo, "계산 완료"])
        if first and (hours or boxes or opp or total or decimal(o["extra_hours"]) or decimal(o["extra_amount"])):
            work_gross = decimal(c["work_charge"]) + decimal(c["extra_time_charge"])
            supply = work_gross / (1 + decimal(rules["vat"]))
            bills.append([month, batch.bl, o["customer"], ", ".join(o["shop_orders"]), ", ".join(o["trackings"]),
                o["mode"], decimal(o["amount"]), hours, decimal(c["free_hours"]),
                decimal(c["charge_hours"]) + decimal(o["extra_hours"]), supply, work_gross - supply, work_gross,
                boxes, decimal(rules["free_boxes"]), decimal(c["box_excess"]),
                decimal(c["box_excess"]) * decimal(rules["box_cny"]), fx, decimal(c["box_charge"]), cbm,
                max(Decimal(0), cbm - 3), decimal(c["cbm_charge"]), decimal(c["base"]),
                "직접입력", total, total, "", "", message, memo])
    approvals = []
    for o in batch.orders:
        c = calculate(o, rules)
        approvals.append([f"{batch.id}:{o['id']}", batch.bl, o["jungpan_order"], fx, decimal(rules["hourly"]),
            decimal(rules["vat"]), decimal(rules["free_boxes"]), decimal(rules["box_cny"]), decimal(costs["hourly_cny"]),
            decimal(costs["opp_cny"]), decimal(costs["box_cny"]), decimal(costs["barcode_krw"]), decimal(costs["sticker_cny"]),
            decimal(costs["sewing_cny"]), decimal(costs["cbm_cny"]), decimal(c["free_hours"]), decimal(o["extra_hours"]),
            decimal(o["extra_amount"]), int(o["waive_work"]), int(o["waive_total"]),
            decimal(o["manual_total"]) if o["manual_total"] is not None else None, 3, 10000, o["reason"], rules["version"],
            "https://www.jungpan.com/Admin/1688/Order_L.asp?gMnu1=123&gMnu2=12302",
            f"shOrdNo={o['jungpan_order']}; BL={batch.bl}; 상품별 최신 작업기록을 하위주문·트래킹별 중복 제거",
            str(getattr(batch, "created_at", "")), str(getattr(batch, "approved_by", ""))])
    return {RAW: raw, COST: cost, BILL: bills, APPROVAL: approvals}


def formula_rows(batch, plan):
    data = build_rows(batch)
    targets = plan["targets"]
    approval_rows = {o["id"]: targets[APPROVAL]["start"] + i for i, o in enumerate(batch.orders)}
    seen, bill_index = set(), 0
    for i, product in enumerate(batch.source["products"]):
        row = targets[RAW]["start"] + i
        cost_row = targets[COST]["start"] + i
        key = product["child_seq"]
        order = next(o for o in batch.orders if o["id"] == key)
        first = key not in seen
        seen.add(key)
        p = f"'{APPROVAL}'!"
        a = lambda col: f"{p}{col}{approval_rows[key]}"
        r = lambda col: f"{col}{row}"
        ref = lambda col: f"'{RAW}'!{col}{row}"
        raw = data[RAW][i]
        has_bill = first and (raw[8] or raw[9] or raw[10] or raw[40] or decimal(order["extra_hours"]) or decimal(order["extra_amount"]))
        formulas = {17: a("D"), 18: f'{r("I")}*{a("I")}*{r("R")}', 19: f'{r("J")}*{a("J")}*{r("R")}',
            20: f'{r("K")}*{a("K")}*{r("R")}',
            23: f'SUM({r("S")}:{r("W")})+{r("AX")}*{a("L")}+({r("AY")}*{a("M")}+{r("AZ")}*{a("N")}+{r("H")}*{a("O")})*{r("R")}',
            42: f'{r("AO")}-{r("X")}', 43: f'IF({r("AO")}=0,0,{r("AQ")}/{r("AO")})',
            44: f'IF({r("AO")}=0,0,{r("X")}/{r("AO")})'}
        if first:
            formulas.update({24: a("P"), 25: f'MAX(0,{r("I")}-{r("Y")})+{a("Q")}',
                26: f'(IF({a("S")}=1,0,MAX(0,{r("I")}-{r("Y")}))+{a("Q")})*{a("E")}*(1+{a("F")})',
                27: f'ROUND(MAX(0,{r("H")}-{a("V")})*{a("W")}*(1+{a("F")}),0)',
                34: f'ROUND(MAX(0,{r("K")}-{a("G")})*{a("H")}*{r("R")}*(1+{a("F")}),0)',
                37: f'{r("AA")}-{a("Q")}*{a("E")}*(1+{a("F")})+{r("AB")}+{r("AI")}',
                39: f'ROUNDDOWN(IF({a("T")}=1,0,IF(ISNUMBER({a("U")}),{a("U")},{r("AL")}+{a("Q")}*{a("E")}*(1+{a("F")})+{a("R")}))/10,0)*10',
                40: r("AN")})
        for col, formula in formulas.items():
            raw[col] = Formula("=" + formula)
        cost = data[COST][i]
        for col, source in {6:"R",7:"H",10:"I",11:"S",12:"AX",14:"AY",16:"AZ",18:"BA",20:"BB",22:"J",23:"T",24:"K",25:"U",29:"X"}.items():
            cost[col] = Formula("=" + ref(source))
        cost[8] = Formula("=" + a("O"))
        for col, formula in {9:f'H{cost_row}*I{cost_row}*G{cost_row}', 13:f'M{cost_row}*{a("L")}',
            15:f'O{cost_row}*{a("M")}*G{cost_row}',17:f'Q{cost_row}*{a("N")}*G{cost_row}'}.items():
            cost[col] = Formula("=" + formula)
        if has_bill:
            bill = data[BILL][bill_index]
            b = targets[BILL]["start"] + bill_index
            for col, source in {7:"I",8:"Y",9:"Z",12:"AA",13:"K",17:"R",18:"AI",19:"H",21:"AB",22:"AL",24:"AO",25:"AO"}.items():
                bill[col] = Formula("=" + ref(source))
            for col, formula in {10:f'M{b}/(1+{a("F")})',11:f'M{b}-K{b}',14:a("G"),15:f'MAX(0,N{b}-O{b})',
                16:f'P{b}*{a("H")}',20:f'MAX(0,T{b}-{a("V")})'}.items():
                bill[col] = Formula("=" + formula)
            bill_index += 1
    return data


class Sheets:
    def __init__(self):
        try:
            raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            if raw:
                credentials = Credentials.from_service_account_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets"])
            else:
                path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
                if not path:
                    raise DataError("구글시트 연동 계정을 설정해 주세요. 중판 조회 결과는 초안으로 보관됩니다.")
                credentials = Credentials.from_service_account_file(path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            self.session = AuthorizedSession(credentials)
        except (ValueError, OSError, KeyError):
            raise DataError("구글 서비스 계정 설정 형식을 확인해 주세요.") from None
        self.base = f"https://sheets.googleapis.com/v4/spreadsheets/{settings.SHEET_ID}"

    def call(self, method, suffix="", **kwargs):
        try:
            response = self.session.request(method, self.base + suffix, timeout=45, **kwargs)
            if not response.ok:
                raise DataError(f"구글시트 연결 실패(HTTP {response.status_code}). 공유 권한과 API 설정을 확인해 주세요.")
            return response.json()
        except (TimeoutError, OSError, RequestException, GoogleAuthError):
            raise DataError("구글시트 응답을 확인하지 못했습니다. 저장 표식을 재조회한 후 재시도합니다.") from None

    def values(self, ranges, formatted=False):
        result = self.call("GET", "/values:batchGet", params=[("ranges", r) for r in ranges] +
                           [("valueRenderOption", "FORMATTED_VALUE" if formatted else "UNFORMATTED_VALUE")])
        return [v.get("values", []) for v in result["valueRanges"]]

    def rules(self, month):
        rule, costs, rates = self.values(["'청구규칙'!A4:C23", "'원가표'!L4:L21", "'월별 환율'!A4:B100"], True)
        get = lambda rows, row, col=0: number(rows[row][col])
        exchange = next((number(r[1]) for r in rates if len(r) > 1 and r[0] == month and r[1] != ""), None)
        if exchange is None:
            raise DataError(f"{month}의 적용 환율이 없습니다. 월별 환율 탭에 등록해 주세요.")
        result = {"month": month, "exchange": exchange, "hourly": get(rule, 0, 2), "vat": get(rule, 14, 2),
            "free_boxes": get(rule, 9, 2), "box_cny": get(rule, 10, 2),
            "free_hours": [[number(v) for v in r[:3]] for r in rule[15:20]],
            "costs": {"hourly_cny": get(costs, 0), "opp_cny": get(costs, 2), "box_cny": get(costs, 4),
                "cbm_cny": get(costs, 17 if month < "2026-11" else 7), "sticker_cny": get(costs, 10),
                "sewing_cny": get(costs, 11), "barcode_krw": get(costs, 15)}}
        result["version"] = fingerprint(result)
        if decimal(result["free_boxes"]) != 10:
            raise DataError("시트의 무료 박스 기준이 승인된 10개 규칙과 다릅니다.")
        return result

    def metadata(self):
        return self.call("GET", params={"fields": "spreadsheetId,sheets(properties),developerMetadata"})

    def marker(self, batch_id, metadata=None):
        data = metadata if metadata is not None else self.metadata()
        return next((m for m in data.get("developerMetadata", [])
                     if m.get("metadataKey") == "blcost_batch" and m.get("metadataValue") == str(batch_id)), None)

    def prepare(self, batch):
        meta = self.metadata()
        if self.marker(batch.id, meta):
            return {"already_saved": True}
        props = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
        for name in (RAW, COST, BILL):
            if name not in props:
                raise DataError(f"필요한 시트가 없습니다: {name}")
        create_approval = APPROVAL not in props
        if create_approval:
            used = {p["sheetId"] for p in props.values()}
            approval_id = next(i for i in range(1619030188, 1619030288) if i not in used)
            props[APPROVAL] = {"sheetId": approval_id, "gridProperties": {"rowCount": 1000}}
        columns = self.values([f"'{name}'!B{first}:B{min(15000, props[name]['gridProperties']['rowCount'])}"
                              for name, first in ((RAW,6),(COST,4),(BILL,4))] +
                              [f"'{RAW}'!A5:BF5", f"'{COST}'!A3:AG3", f"'{BILL}'!A3:AD3"])
        for name, header, width in zip((RAW,COST,BILL), columns[3:], (57,33,30)):
            if not header or fingerprint(header[0][:width]) != HEADER_HASHES[name]:
                raise DataError(f"{name}의 열 구조가 변경되었습니다. 잘못된 열에 쓰지 않도록 저장을 중단했습니다.")
        headers = columns[3][0]
        if len(headers) < 57 or headers[1] != "묶음 주문번호" or headers[49] != "바코드 수량":
            raise DataError("주문 데이터의 열 구조가 변경되었습니다. 저장하지 않았습니다.")
        if len(headers) > 57 and headers[57] not in ("", "시스템 고유키"):
            raise DataError("시스템 고유키를 기록할 BF열을 확인해 주세요.")
        starts = {}
        for name, rows, first in zip((RAW, COST, BILL), columns[:3], (6, 4, 4)):
            if any(r and str(r[0]) == batch.bl for r in rows):
                raise DataError("이 BL이 기존 공유시트에 있습니다. 중복 저장을 차단했습니다. 기존 자료 보정은 원본 대조 후 별도로 진행해 주세요.")
            starts[name] = max((i + first + 1 for i, r in enumerate(rows) if r and r[0] != ""), default=first)
        approval_keys = []
        if not create_approval:
            approval_keys, approval_headers = self.values([f"'{APPROVAL}'!A:A", f"'{APPROVAL}'!A1:AC1"])
            if not approval_headers or approval_headers[0] != APPROVAL_HEADERS:
                raise DataError("시스템 승인기준 탭의 열 구조를 확인해 주세요.")
        starts[APPROVAL] = max(2, len(approval_keys) + 1)
        data = build_rows(batch)
        targets = {name: {"start": starts[name], "count": len(rows), "sheet_id": props[name]["sheetId"],
                         "grid_rows": props[name]["gridProperties"]["rowCount"]} for name, rows in data.items()}
        return {"targets": targets, "data_hash": fingerprint(data), "sheet_id": settings.SHEET_ID, "create_approval": create_approval}

    def save(self, batch):
        if self.marker(batch.id):
            return self.verify(batch)
        plan = self.prepare(batch)
        if plan != batch.sheet_plan:
            raise ReviewRequired("승인 이후 시트의 행 위치가 바뀌었습니다. 금액을 다시 확인하고 승인해 주세요.")
        data = formula_rows(batch, plan)
        requests = []
        if plan["create_approval"]:
            requests.append({"addSheet": {"properties": {"sheetId": plan["targets"][APPROVAL]["sheet_id"], "title": APPROVAL,
                "gridProperties": {"rowCount": 1000, "columnCount": len(APPROVAL_HEADERS), "frozenRowCount": 1}}}})
            requests.append({"updateCells": {"start": {"sheetId": plan["targets"][APPROVAL]["sheet_id"], "rowIndex": 0},
                "rows": [{"values": [cell(v) for v in APPROVAL_HEADERS]}], "fields": "userEnteredValue"}})
        for name, rows in data.items():
            if not rows:
                continue
            target = plan["targets"][name]
            end = target["start"] - 1 + len(rows)
            if end > 14955:
                raise DataError("기존 자동 집계 수식의 행 범위를 확장한 후 다시 저장해 주세요.")
            if end > target["grid_rows"]:
                requests.append({"appendDimension": {"sheetId": target["sheet_id"], "dimension": "ROWS", "length": end - target["grid_rows"]}})
            if name != APPROVAL and target["start"] > (6 if name == RAW else 4):
                requests.append({"copyPaste": {"source": {"sheetId": target["sheet_id"], "startRowIndex": target["start"]-2,
                    "endRowIndex": target["start"]-1, "startColumnIndex": 0, "endColumnIndex": len(rows[0])},
                    "destination": {"sheetId": target["sheet_id"], "startRowIndex": target["start"]-1,
                    "endRowIndex": end, "startColumnIndex": 0, "endColumnIndex": len(rows[0])}, "pasteType": "PASTE_FORMAT"}})
            requests.append({"updateCells": {"start": {"sheetId": target["sheet_id"], "rowIndex": target["start"] - 1, "columnIndex": 0},
                             "rows": [{"values": [cell(v) for v in row]} for row in rows], "fields": "userEnteredValue"}})
        requests += [
            {"updateCells": {"start": {"sheetId": plan["targets"][RAW]["sheet_id"], "rowIndex": 4, "columnIndex": 57},
                             "rows": [{"values": [cell("시스템 고유키")]}], "fields": "userEnteredValue"}},
            {"createDeveloperMetadata": {"developerMetadata": {"metadataKey": "blcost_batch", "metadataValue": str(batch.id),
                "location": {"spreadsheet": True}, "visibility": "DOCUMENT"}}},
        ]
        # shortcut: 앱 밖에서 원장 끝에 행을 추가하는 작업은 직전 재검사로 감지, 공동 자동 입력이 생기면 전용 입력 원장으로 분리한다.
        self.call("POST", ":batchUpdate", json={"requests": requests})
        return self.verify(batch)

    def verify(self, batch):
        if not self.marker(batch.id):
            raise DataError("저장 완료 표식이 없습니다. 중복 입력하지 않고 확인을 중단했습니다.")
        data = build_rows(batch)
        ranges = []
        names = []
        for name, rows in data.items():
            if rows:
                start = batch.sheet_plan["targets"][name]["start"]
                last = "BF" if name == RAW else "AG" if name == COST else "AD" if name == BILL else "AC"
                ranges.append(f"'{name}'!A{start}:{last}{start + len(rows) - 1}")
                names.append(name)
        actual = self.values(ranges)
        for name, read_rows in zip(names, actual):
            expected = data[name]
            if len(read_rows) != len(expected):
                raise DataError("저장된 행 수가 다릅니다. 완료로 처리하지 않았습니다.")
            for a, e in zip(read_rows, expected):
                for i, value in enumerate(e):
                    got = a[i] if len(a) > i else None
                    if value in (None, "") and got in (None, ""):
                        continue
                    if isinstance(value, (Decimal, int, float)):
                        if abs(decimal(got, minimum=Decimal("-1000000000000")) - Decimal(str(value))) > Decimal("0.01"):
                            raise DataError("시트 저장 금액·수량 검산이 일치하지 않습니다.")
                    elif got != str(value):
                        raise DataError("시트 저장 문자·식별번호 검산이 일치하지 않습니다.")
        return {"verified": True, "targets": batch.sheet_plan["targets"], "spreadsheet_id": settings.SHEET_ID}
