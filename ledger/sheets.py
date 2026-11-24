import json
import os
import re
from decimal import Decimal

from django.conf import settings
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials
from google.auth.exceptions import GoogleAuthError
from requests.exceptions import RequestException

from .domain import DataError, ReviewRequired, calculate, decimal, fingerprint, invoice_message, review_issues

BILL = "청구 입력(내가 입력)"
APPROVAL = "시스템 승인기준"
BILL_HEADER_HASH = "1ee2c905bb48f947301ea240b93ccd478807bdb7377513a172d677f2f2aa86df"
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


def build_rows(batch):
    bills, approvals = [], []
    rules = batch.rules
    for o in batch.orders:
        if review_issues(o, rules) and not o["reviewed"]:
            raise DataError("추가 비용 결정을 먼저 완료해 주세요.")
        c = calculate(o, rules)
        if not c["ready"]:
            raise DataError("확정할 수 없는 계산이 남아 있습니다.")
        if not decimal(c["total"]):
            continue
        fx, vat = decimal(rules["exchange"]), decimal(rules["vat"])
        hours = decimal(c["actual_hours"])
        charged = (Decimal(0) if o["waive_work"] else decimal(c["charge_hours"])) + decimal(o["extra_hours"])
        gross = decimal(c["work_charge"]) + decimal(c["extra_time_charge"])
        supply = gross / (1 + vat)
        boxes, cbm = decimal(o.get("boxes") or 0), decimal(o.get("cbm") or 0)
        total = decimal(c["total"])
        note = f"중판 주문 {o['jungpan_order']} / 시스템 승인 {batch.id}\n사유: {o['reason']}\n입고오류메시지: {o['memo']}"
        bills.append([f"{batch.bl[:4]}-{batch.bl[4:6]}", batch.bl, o["customer"], ", ".join(o["shop_orders"]), ", ".join(o["trackings"]),
            o["mode"], decimal(o["amount"]), hours, decimal(c["free_hours"]), charged, supply, gross-supply, gross,
            boxes, decimal(rules["free_boxes"]), decimal(c["box_excess"]), decimal(c["box_excess"])*decimal(rules["box_cny"]),
            fx, decimal(c["box_charge"]), cbm, max(Decimal(0), cbm-3), decimal(c["cbm_charge"]), decimal(c["base"]),
            "직접입력", total, total, "", "", invoice_message(o, rules), note])
        costs = rules["costs"]
        approvals.append([f"{batch.id}:{o['id']}", batch.bl, o["jungpan_order"], fx, decimal(rules["hourly"]), vat,
            decimal(rules["free_boxes"]), decimal(rules["box_cny"]), decimal(costs["hourly_cny"]), decimal(costs["opp_cny"]),
            decimal(costs["box_cny"]), decimal(costs["barcode_krw"]), decimal(costs["sticker_cny"]), decimal(costs["sewing_cny"]),
            decimal(costs["cbm_cny"]), decimal(c["free_hours"]), decimal(o["extra_hours"]), decimal(o["extra_amount"]),
            int(o["waive_work"]), int(o["waive_total"]), decimal(o["manual_total"]) if o["manual_total"] is not None else None,
            3, 10000, o["reason"], rules["version"], "https://www.jungpan.com/Admin/1688/Order_L.asp?gMnu1=123&gMnu2=12302",
            f"shOrdNo={o['jungpan_order']}; BL={batch.bl}; 최신 작업기록을 하위주문·트래킹별 중복 제거",
            str(getattr(batch, "created_at", "")), str(getattr(batch, "approved_by", ""))])
    return {BILL: bills, APPROVAL: approvals}


def formula_rows(batch, plan):
    data = build_rows(batch)
    for i, row in enumerate(data[BILL]):
        b = plan["targets"][BILL]["start"] + i
        a = plan["targets"][APPROVAL]["start"] + i
        ref = lambda col: f"'{APPROVAL}'!{col}{a}"
        formulas = {8: ref("P"), 9: f'IF({ref("S")}=1,0,MAX(0,H{b}-I{b}))+{ref("Q")}', 10: f'J{b}*{ref("E")}',
            11: f'K{b}*{ref("F")}', 12: f'SUM(K{b}:L{b})', 14: ref("G"), 15: f'MAX(0,N{b}-O{b})', 16: f'P{b}*{ref("H")}',
            17: ref("D"), 18: f'ROUND(Q{b}*R{b}*(1+{ref("F")}),0)', 20: f'MAX(0,T{b}-{ref("V")})',
            21: f'ROUND(U{b}*{ref("W")}*(1+{ref("F")}),0)',
            22: f'M{b}-{ref("Q")}*{ref("E")}*(1+{ref("F")})+S{b}+V{b}',
            24: f'ROUNDDOWN(IF({ref("T")}=1,0,IF(ISNUMBER({ref("U")}),{ref("U")},W{b}+{ref("Q")}*{ref("E")}*(1+{ref("F")})+{ref("R")}))/10,0)*10', 25: f'Y{b}'}
        for col, value in formulas.items():
            row[col] = Formula("=" + value)
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
        if BILL not in props:
            raise DataError(f"필요한 시트가 없습니다: {BILL}")
        columns, headers = self.values([f"'{BILL}'!B4:B{min(14955,props[BILL]['gridProperties']['rowCount'])}", f"'{BILL}'!A3:AD3"])
        if not headers or fingerprint(headers[0]) != BILL_HEADER_HASH:
            raise DataError("청구 입력의 열 구조가 변경되었습니다. 저장을 중단했습니다.")
        if any(r and str(r[0]) == batch.bl for r in columns):
            raise DataError("이 BL은 기존 시트에 있습니다. 중복 저장을 차단했습니다. 화면의 계산 결과는 확인할 수 있습니다.")
        starts = {BILL: max((i+5 for i,r in enumerate(columns) if r and r[0] != ""),default=4)}
        create_approval = APPROVAL not in props
        if create_approval:
            used = {p["sheetId"] for p in props.values()}
            approval_id = next(i for i in range(1619030188,1619030288) if i not in used)
            props[APPROVAL] = {"sheetId": approval_id, "gridProperties": {"rowCount": 1000}}
            starts[APPROVAL] = 2
        else:
            keys, headers = self.values([f"'{APPROVAL}'!A:A", f"'{APPROVAL}'!A1:AC1"])
            if not headers or headers[0] != APPROVAL_HEADERS:
                raise DataError("시스템 승인기준 탭의 열 구조를 확인해 주세요.")
            starts[APPROVAL] = max(2,len(keys)+1)
        data = build_rows(batch)
        if not data[BILL]:
            raise DataError("청구할 금액이 없어 시트에 추가할 행이 없습니다.")
        targets = {name: {"start": starts[name], "count": len(rows), "sheet_id": props[name]["sheetId"],
                         "grid_rows": props[name]["gridProperties"]["rowCount"]} for name,rows in data.items()}
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
            if name == BILL and target["start"] > 4:
                requests.append({"copyPaste": {"source": {"sheetId": target["sheet_id"], "startRowIndex": target["start"]-2,
                    "endRowIndex": target["start"]-1, "startColumnIndex": 0, "endColumnIndex": len(rows[0])},
                    "destination": {"sheetId": target["sheet_id"], "startRowIndex": target["start"]-1,
                    "endRowIndex": end, "startColumnIndex": 0, "endColumnIndex": len(rows[0])}, "pasteType": "PASTE_FORMAT"}})
            requests.append({"updateCells": {"start": {"sheetId": target["sheet_id"], "rowIndex": target["start"] - 1, "columnIndex": 0},
                             "rows": [{"values": [cell(v) for v in row]} for row in rows], "fields": "userEnteredValue"}})
        requests += [
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
                last = "AD" if name == BILL else "AC"
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
