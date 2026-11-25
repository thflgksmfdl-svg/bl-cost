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


def build_rows(batch, include_zero=()):
    bills, approvals = [], []
    rules = batch.rules
    for o in batch.orders:
        if review_issues(o, rules) and not o["reviewed"]:
            continue
        c = calculate(o, rules)
        if not c["ready"]:
            raise DataError("확정할 수 없는 계산이 남아 있습니다.")
        if not decimal(c["total"]) and o["id"] not in include_zero:
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
            str(getattr(batch, "created_at", "")), str(getattr(batch, "approved_by", None) or "공용 사용자")])
    return {BILL: bills, APPROVAL: approvals}


def formula_rows(batch, plan):
    data = build_rows(batch, plan.get("order_ids", ()))
    for i, row in enumerate(data[BILL]):
        b = positions(plan["targets"][BILL])[i]
        a = positions(plan["targets"][APPROVAL])[i]
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


def positions(target):
    return target.get("rows", list(range(target["start"], target["start"] + target.get("count", 1))))


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

    def values(self, ranges, formatted=False, formulas=False):
        result = self.call("GET", "/values:batchGet", params=[("ranges", r) for r in ranges] +
                           [("valueRenderOption", "FORMULA" if formulas else "FORMATTED_VALUE" if formatted else "UNFORMATTED_VALUE")])
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

    def report_source(self):
        from .reports import legacy_source
        props = {s["properties"]["title"]: s["properties"] for s in self.metadata()["sheets"]}
        required = ("BL별 작업 검산", "대시보드", "원가 정산", BILL)
        if any(name not in props for name in required):
            raise DataError("보고서에 필요한 기존 공유시트 탭이 없습니다.")
        end = props["BL별 작업 검산"]["gridProperties"]["rowCount"]
        cost_end, bill_end = (props[n]["gridProperties"]["rowCount"] for n in ("원가 정산", BILL))
        if end < 510 or cost_end > 100000 or bill_end > 100000:
            raise DataError("기존 보고서의 집계 범위가 바뀌었습니다. 범위를 확인해 주세요.")
        values = self.values(["'BL별 작업 검산'!P8:X500", "'BL별 작업 검산'!Y8:AF507",
            f"'원가 정산'!B4:B{cost_end}", f"'원가 정산'!AD4:AD{cost_end}",
            f"'{BILL}'!B4:B{bill_end}", f"'{BILL}'!Z4:AB{bill_end}"])
        if len(values[0]) == 493 or len(values[1]) == 500:
            raise DataError("과거 BL 집계 범위가 가득 찼습니다. 원본 집계 범위를 확장해 주세요.")
        return legacy_source(*values)

    def report_backup(self):
        ranges = ["'BL별 작업 검산'!A1:M510", "'대시보드'!A1:L300"]
        return {"ranges": ranges, "formulas": self.values(ranges, formulas=True)}

    def save_reports(self, report):
        from .reports import SUMMARY_HEADERS, SUMMARY_SHEET, WORK
        props = {s["properties"]["title"]: s["properties"] for s in self.metadata()["sheets"]}
        requests = []
        if SUMMARY_SHEET not in props:
            used = {p["sheetId"] for p in props.values()}
            sid = next(i for i in range(1349874000, 1349874100) if i not in used)
            props[SUMMARY_SHEET] = {"sheetId": sid, "gridProperties": {"rowCount": max(1000, len(report["rows"])+1), "columnCount": 28}}
            requests.append({"addSheet": {"properties": {**props[SUMMARY_SHEET], "title": SUMMARY_SHEET}}})
        else:
            headers = self.values([f"'{SUMMARY_SHEET}'!A1:AB1"])
            if not headers or not headers[0] or headers[0][0] != SUMMARY_HEADERS:
                raise DataError("시스템 집계의 열 구조가 바뀌었습니다. 기존 자료를 보호하기 위해 저장을 중단했습니다.")
        sid = props[SUMMARY_SHEET]["sheetId"]
        check_id, dashboard_id = (props[n]["sheetId"] for n in ("BL별 작업 검산", "대시보드"))
        data, check = [], []
        for i, r in enumerate(report["rows"], 2):
            w = r["work"]
            nums = lambda *keys: [decimal(w[k]) for k in keys]
            data.append([r["bl"], r["month"], r["source"], r["orders"], r["products"], *nums(*WORK[:5]),
                Formula(f"=SUM(G{i}:J{i})"), *nums(*WORK[5:]), Formula(f"=SUM(L{i}:M{i})"), r["verification"],
                " · ".join(r["differences"]), r["invoice_count"], decimal(r["invoice_total"]), r["pending"],
                *[decimal(r[k]) for k in ("paid", "unpaid", "unknown", "held")], decimal(r["cost"]) if r["cost"] is not None else None,
                r["cost_basis"], r["source_at"], r["sheet_check"], Decimal(r["sheet_difference"])])
            if r["source"] == "청구 시트만 존재":
                data[-1][4:14] = ["미확인"]*10
            ref = lambda c: Formula(f"='{SUMMARY_SHEET}'!{c}{i}")
            check.append([ref(c) for c in ("A", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O")] +
                         [f"{r['source']} / " + (" · ".join(r["differences"]) or "추가 확인사항 없음")])

        def write(sheet_id, row, col, values, clear_to=None):
            if clear_to is not None:
                requests.append({"updateCells": {"range": {"sheetId": sheet_id, "startRowIndex": row, "endRowIndex": clear_to,
                    "startColumnIndex": col, "endColumnIndex": col+len(values[0])}, "rows": [], "fields": "userEnteredValue"}})
            requests.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": col},
                "rows": [{"values": [cell(v) for v in line]} for line in values], "fields": "userEnteredValue"}})

        if len(data)+1 > props[SUMMARY_SHEET]["gridProperties"]["rowCount"]:
            requests.append({"appendDimension": {"sheetId": sid, "dimension": "ROWS", "length": len(data)+1-props[SUMMARY_SHEET]["gridProperties"]["rowCount"]}})
        write(sid, 0, 0, [SUMMARY_HEADERS]+data, props[SUMMARY_SHEET]["gridProperties"]["rowCount"])
        check_end = max(props["BL별 작업 검산"]["gridProperties"]["rowCount"], len(check)+7)
        if check_end > props["BL별 작업 검산"]["gridProperties"]["rowCount"]:
            requests.append({"appendDimension": {"sheetId": check_id, "dimension": "ROWS", "length": check_end-props["BL별 작업 검산"]["gridProperties"]["rowCount"]}})
        write(check_id, 6, 0, [["BL 번호", "상품 행", "실제 작업시간(h)", "인쇄스티커", "스티커", "바코드", "벌크작업", "4종 합계", "재봉", "도장", "재봉·도장 합계", "검산 상태", "차이 항목 / 확인사항"]]+check, check_end)
        write(check_id, 1, 0, [["전체 작업 포함 · 중판 조회값 우선 · 기존 자료와의 차이는 확인사항에 표시 · 원본 집계 P:AF 보존"]])
        ref = lambda c: f"'{SUMMARY_SHEET}'!{c}2:{c}"
        for col, value in [(0, Formula(f"=COUNTA({ref('A')})")), (2, Formula(f'=TEXT(SUM({ref("F")}),"#,##0.##")&"시간"')),
            (4, Formula(f'=TEXT(SUM({ref("K")}),"#,##0")&"개"')), (8, Formula(f'=TEXT(SUM({ref("N")}),"#,##0")&"개"')),
            (11, Formula(f'=COUNTIF({ref("P")},"?*")&" BL"'))]:
            write(check_id, 3, col, [[value]])
        write(check_id, 2, 11, [["차이 확인 필요"]])
        write(check_id, 5, 11, [["수집 원본·DB 대조"]])
        write(dashboard_id, 0, 0, [["작업·확정 청구 대시보드"]])
        write(dashboard_id, 2, 0, [["핵심 지표", "집계값"], ["참고 원가", Formula(f'=IF(COUNTA({ref("A")})=0,0,IF(COUNT({ref("X")})=0,"미확인",SUM({ref("X")})))')],
            ["확정 고객 청구", Formula(f"=SUM({ref('R')})")], ["원가·마진 기준", "기존 원가와 추정 원가 포함 · 실제 마진은 미산출"],
            ["입금 기록액", Formula(f"=SUM({ref('T')})")], ["미결제 기록액", Formula(f"=SUM({ref('U')})")],
            ["결제상태 미입력액", Formula(f"=SUM({ref('V')})")]])
        months = []
        for i, m in enumerate(report["months"], 4):
            criterion = f"D{i}"
            months.append([m["month"], Formula(f"=COUNTIF({ref('B')},{criterion})")] +
                [Formula(f'=IF(COUNTIFS({ref("B")},{criterion},{ref(c)},"<>")=0,"미확인",SUMIF({ref("B")},{criterion},{ref(c)}))') if c in ("S", "F", "X") else
                 Formula(f"=SUMIF({ref('B')},{criterion},{ref(c)})") for c in ("F", "Q", "R", "T", "V", "S", "X")])
        write(dashboard_id, 2, 3, [["작업월", "BL 수", "실제 시간(h)", "확정 청구 건", "확정 청구액", "입금 기록액", "상태 미입력액", "추가 결정 대기", "참고 원가"]]+months, max(15, len(months)+3))
        write(dashboard_id, 13, 1, [["10개까지 무료 / 11개째부터 초과분 청구"]])
        write(dashboard_id, 16, 0, [["입금은 청구 입력의 결제완료 기록 기준입니다. 빈 결제상태는 미결제로 간주하지 않습니다."]])
        write(dashboard_id, 17, 0, [["작업월 기준 집계입니다. 과거 시트의 추가 비용 결정 여부는 확인되지 않았습니다."]])
        for target, header_row, columns in [(sid, 0, 28), (dashboard_id, 2, 12)]:
            requests.append({"repeatCell": {"range": {"sheetId": target, "startRowIndex": header_row, "endRowIndex": header_row+1,
                "startColumnIndex": 0 if target == sid else 3, "endColumnIndex": columns}, "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": .85, "green": .94, "blue": .89}, "textFormat": {"bold": True}, "wrapStrategy": "WRAP"}},
                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.wrapStrategy"}})
        requests.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}})
        for first, last, pattern in [(4, 7, "#,##0.##"), (7, 10, '#,##0"원"'), (10, 11, "#,##0"), (11, 12, '#,##0"원"')]:
            requests.append({"repeatCell": {"range": {"sheetId": dashboard_id, "startRowIndex": 3, "endRowIndex": max(15, len(months)+3),
                "startColumnIndex": first, "endColumnIndex": last}, "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}}, "fields": "userEnteredFormat.numberFormat"}})
        requests.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": len(data)+1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}}, "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}})
        for first, last, width in [(0, 1, 130), (1, 2, 110), (2, 3, 140), (3, 14, 95), (14, 16, 250), (16, 24, 130), (24, 28, 260)]:
            requests.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": first, "endIndex": last}, "properties": {"pixelSize": width}, "fields": "pixelSize"}})
        requests.append({"updateDimensionProperties": {"range": {"sheetId": dashboard_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 12}, "properties": {"pixelSize": 135}, "fields": "pixelSize"}})
        self.call("POST", ":batchUpdate", json={"requests": requests})
        actual = self.values([f"'{SUMMARY_SHEET}'!A2:AB{max(2,len(data)+1)}", "'대시보드'!B4:B5", f"'BL별 작업 검산'!A8:M{max(8,len(check)+7)}", f"'대시보드'!D4:L{max(4,len(months)+3)}"])
        cost_total = "미확인" if data and report["totals"]["cost_missing"] == len(data) else decimal(report["totals"]["cost"])
        expected = [[list(row) for row in data], [[cost_total], [decimal(report["totals"]["invoice_total"])]], [], []]
        if len(actual) != len(expected):
            raise DataError("보고서 저장 결과의 범위 수가 다릅니다.")
        for row, r in zip(expected[0], report["rows"]):
            row[10], row[13] = decimal(r["four_total"]), decimal(r["two_total"])
            if r["source"] == "청구 시트만 존재":
                row[4:14] = ["미확인"]*10
            expected[2].append([r["bl"], r["products"], *[decimal(r["work"][k]) for k in WORK[:5]], decimal(r["four_total"]),
                *[decimal(r["work"][k]) for k in WORK[5:]], decimal(r["two_total"]), r["verification"],
                f"{r['source']} / " + (" · ".join(r["differences"]) or "추가 확인사항 없음")])
            if r["source"] == "청구 시트만 존재":
                expected[2][-1][1:11] = ["미확인"]*10
        for m in report["months"]:
            expected[3].append([m["month"], m["bl_count"], "미확인" if m["work_missing"] == m["bl_count"] else decimal(m["work"]["작업시간"]),
                m["invoice_count"], decimal(m["invoice_total"]), decimal(m["paid"]), decimal(m["unknown"]),
                "미확인" if m["pending_unknown"] == m["bl_count"] else m["pending"], "미확인" if m["cost_missing"] == m["bl_count"] else decimal(m["cost"])])
        for got_rows, want_rows in zip(actual, expected):
            if len(got_rows) != len(want_rows):
                raise DataError("보고서 저장 행 수가 일치하지 않습니다.")
            for got, want in zip(got_rows, want_rows):
                for i, value in enumerate(want):
                    read = got[i] if len(got) > i else ""
                    if value in (None, "") and read in (None, ""):
                        continue
                    if isinstance(value, (Decimal, int, float)):
                        if abs(Decimal(str(read)) - Decimal(str(value))) > Decimal(".01"):
                            raise DataError("사이트·시트 보고서 수량 또는 금액이 다릅니다.")
                    elif str(read) != str(value):
                        raise DataError("사이트·시트 보고서 문자 또는 식별번호가 다릅니다.")
        return {"verified": True, "spreadsheet_id": settings.SHEET_ID, "summary_sheet_id": sid,
            "check_sheet_id": check_id, "dashboard_sheet_id": dashboard_id, "bl_count": len(data), "data_hash": fingerprint(report)}

    def marker(self, batch, metadata=None):
        data = metadata if metadata is not None else self.metadata()
        key = batch.sheet_plan.get("write_key", str(batch.id))
        return next((m for m in data.get("developerMetadata", [])
                     if m.get("metadataKey") == "blcost_batch" and m.get("metadataValue") == key), None)

    def prepare(self, batch):
        meta = self.metadata()
        props = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
        if BILL not in props:
            raise DataError(f"필요한 시트가 없습니다: {BILL}")
        columns, headers = self.values([f"'{BILL}'!B4:E{min(14955,props[BILL]['gridProperties']['rowCount'])}", f"'{BILL}'!A3:AD3"])
        if not headers or fingerprint(headers[0]) != BILL_HEADER_HASH:
            raise DataError("청구 입력의 열 구조가 변경되었습니다. 저장을 중단했습니다.")
        existing = [i+4 for i,r in enumerate(columns) if r and str(r[0]) == batch.bl]
        snapshots = self.values([f"'{BILL}'!A{r}:AD{r}" for r in existing]) if existing else []
        matches, clear_ids, held = {}, [], []
        for rownum, values in zip(existing, snapshots):
            row = values[0] if values else []
            tracks = set(re.split(r"[,\s/]+", str(row[4]).strip())) if len(row)>4 and row[4] else set()
            candidates = [o for o in batch.orders if tracks and tracks <= set(o["trackings"])]
            previous = decimal(row[25] or 0) if len(row)>25 else Decimal(0)
            if len(candidates) != 1:
                if previous:
                    raise DataError(f"청구 입력 {rownum}행의 트래킹을 원본 주문 한 건과 연결하지 못했습니다. 기존 행을 확인해 주세요.")
                continue
            oid = candidates[0]["id"]
            if oid in matches:
                raise DataError(f"청구 입력에 같은 주문이 여러 행에 있습니다({matches[oid]}, {rownum}행). 중복 행을 확인해 주세요.")
            matches[oid] = rownum
            if review_issues(candidates[0],batch.rules) and not candidates[0]["reviewed"]:
                note=row[29] if len(row)>29 else ""
                if not str(note).startswith("시스템: 추가 비용 검토 대기"):
                    note="시스템: 추가 비용 검토 대기 — 고객 청구 보류\n"+str(note)
                held.append({"row":rownum,"identity":row[1:5],"note":note})
            if previous or (len(row)>29 and str(row[29]).startswith("시스템: 추가 비용 검토 대기")):
                clear_ids.append(oid)
        starts = {BILL: max((i+5 for i,r in enumerate(columns) if any(v not in (None, "") for v in r)),default=4)}
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
        data = build_rows(batch, clear_ids)
        order_ids = [r[0].split(":")[-1] for r in data[APPROVAL]]
        bill_rows, next_row, added = [], starts[BILL], []
        for oid in order_ids:
            if oid in matches:
                bill_rows.append(matches[oid])
            else:
                bill_rows.append(next_row)
                added.append(next_row)
                next_row += 1
        targets = {name: {"start": starts[name], "count": len(rows), "sheet_id": props[name]["sheetId"],
                         "grid_rows": props[name]["gridProperties"]["rowCount"]} for name,rows in data.items()}
        targets[BILL]["rows"], targets[BILL]["added_rows"] = bill_rows, added
        before = self.values([f"'{BILL}'!A{r}:AD{r}" for r in existing], formulas=True) if existing else []
        return {"targets": targets, "order_ids": order_ids, "before": before, "existing_rows": existing,"held":held,
                "empty":not data[BILL] and not held,
                "write_key": f"{batch.id}:{getattr(batch,'approved_at',None) or getattr(batch,'revision',0)}",
                "data_hash": fingerprint({"rows":data,"held":held}), "sheet_id": settings.SHEET_ID, "create_approval": create_approval and bool(data[APPROVAL])}

    def save(self, batch):
        if self.marker(batch):
            return self.verify(batch)
        plan = self.prepare(batch)
        if plan != batch.sheet_plan:
            raise ReviewRequired("반영 요청 이후 시트 내용이나 행 위치가 바뀌었습니다. 최신 금액을 확인하고 다시 반영해 주세요.")
        if plan["empty"]:
            return self.verify(batch)
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
            row_numbers = positions(target)
            end = max(row_numbers)
            if name == BILL and end > 14955:
                raise DataError("기존 자동 집계 수식의 행 범위를 확장한 후 다시 저장해 주세요.")
            if end > target["grid_rows"]:
                requests.append({"appendDimension": {"sheetId": target["sheet_id"], "dimension": "ROWS", "length": end - target["grid_rows"]}})
            for rownum, row in zip(row_numbers, rows):
                if name == BILL and rownum in target.get("added_rows", []) and rownum > 4:
                    requests.append({"copyPaste": {"source": {"sheetId": target["sheet_id"], "startRowIndex": rownum-2,
                        "endRowIndex": rownum-1, "startColumnIndex": 0, "endColumnIndex": len(row)},
                        "destination": {"sheetId": target["sheet_id"], "startRowIndex": rownum-1,
                        "endRowIndex": rownum, "startColumnIndex": 0, "endColumnIndex": len(row)}, "pasteType": "PASTE_FORMAT"}})
                segments=[(0,row[:26]),(28,row[28:])] if name==BILL else [(0,row)]
                for col, values in segments:
                    requests.append({"updateCells": {"start": {"sheetId": target["sheet_id"], "rowIndex": rownum - 1, "columnIndex": col},
                                     "rows": [{"values": [cell(v) for v in values]}], "fields": "userEnteredValue"}})
        for hold in plan["held"]:
            for col,values in [(23,["직접입력",0,0]),(28,["",hold["note"]])]:
                requests.append({"updateCells":{"start":{"sheetId":plan["targets"][BILL]["sheet_id"],"rowIndex":hold["row"]-1,"columnIndex":col},
                    "rows":[{"values":[cell(v) for v in values]}],"fields":"userEnteredValue"}})
        requests += [
            {"createDeveloperMetadata": {"developerMetadata": {"metadataKey": "blcost_batch", "metadataValue": plan["write_key"],
                "location": {"spreadsheet": True}, "visibility": "DOCUMENT"}}},
        ]
        # shortcut: 앱 밖에서 원장 끝에 행을 추가하는 작업은 직전 재검사로 감지, 공동 자동 입력이 생기면 전용 입력 원장으로 분리한다.
        self.call("POST", ":batchUpdate", json={"requests": requests})
        return self.verify(batch)

    def verify(self, batch):
        if batch.sheet_plan.get("empty"):
            return {"verified":True,"targets":batch.sheet_plan["targets"],"no_changes":True,"spreadsheet_id":settings.SHEET_ID}
        if not self.marker(batch):
            raise DataError("저장 완료 표식이 없습니다. 중복 입력하지 않고 확인을 중단했습니다.")
        data = build_rows(batch, batch.sheet_plan.get("order_ids", ()))
        ranges, expected = [], []
        for name, rows in data.items():
            if rows:
                last = "AD" if name == BILL else "AC"
                for rownum, row in zip(positions(batch.sheet_plan["targets"][name]), rows):
                    ranges.append(f"'{name}'!A{rownum}:{last}{rownum}")
                    expected.append((row,name==BILL))
        for hold in batch.sheet_plan.get("held",[]):
            for first,last,row in [("B","E",hold["identity"]),("X","Z",["직접입력",0,0]),("AC","AD",["",hold["note"]])]:
                ranges.append(f"'{BILL}'!{first}{hold['row']}:{last}{hold['row']}")
                expected.append((row,False))
        actual = self.values(ranges)
        if len(actual) != len(expected):
            raise DataError("저장된 행 수가 다릅니다. 완료로 처리하지 않았습니다.")
        for read_rows, (row,preserve_payment) in zip(actual, expected):
            if len(read_rows) != 1:
                raise DataError("저장된 행 수가 다릅니다. 완료로 처리하지 않았습니다.")
            for i, value in enumerate(row):
                if preserve_payment and i in (26,27):
                    continue
                got = read_rows[0][i] if len(read_rows[0]) > i else None
                if value in (None, "") and got in (None, ""):
                    continue
                if isinstance(value, (Decimal, int, float)):
                    if abs(decimal(got, minimum=Decimal("-1000000000000"),maximum=Decimal("1e24")) - Decimal(str(value))) > Decimal("0.01"):
                        raise DataError("시트 저장 금액·수량 검산이 일치하지 않습니다.")
                elif got != str(value):
                    raise DataError("시트 저장 문자·식별번호 검산이 일치하지 않습니다.")
        return {"verified": True, "targets": batch.sheet_plan["targets"], "spreadsheet_id": settings.SHEET_ID}
