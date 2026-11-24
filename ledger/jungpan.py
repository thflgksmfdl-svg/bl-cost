import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from .domain import DataError

BASE = "https://www.jungpan.com"
LIST = "/Admin/1688/Order_L.asp?gMnu1=123&gMnu2=12302"


def soup(html):
    return BeautifulSoup(html, "html.parser")


def order_row(doc, seq=None, number=None):
    for row in doc.select("#orderTable tr"):
        checkbox = row.select_one('input[name="chkORD_SEQ"]')
        cells = row.find_all("td", recursive=False)
        if checkbox and len(cells) == 13:
            if seq and checkbox.get("value") != str(seq):
                continue
            if number and not re.search(rf"\b{re.escape(number)}\b", cells[1].get_text(" ", strip=True)):
                continue
            return row, cells, checkbox["value"]
    raise DataError("정확히 일치하는 주문을 찾지 못했습니다. BL·검색 범위·로그인 상태를 확인해 주세요.")


def parse_parent(html, bl):
    doc = soup(html)
    row, cells, seq = order_row(doc, number=bl)
    children = []
    for link in cells[5].select("a[onclick]"):
        m = re.search(r"fncOrderView\('([0-9]+)'\)", link["onclick"])
        if m:
            children.append({"seq": m[1], "number": link.get_text(strip=True)})
    if not children:
        raise DataError("묶음 BL의 하위 주문을 찾지 못했습니다. 단독 주문번호와 BL을 구분해 주세요.")
    if len({c["seq"] for c in children}) != len(children):
        raise DataError("하위 주문 목록이 중복되어 수집을 중단했습니다.")
    return seq, children


def parse_products(html):
    doc = soup(html)
    table = doc.find("table")
    if not table:
        raise DataError("상품 상세 목록이 없습니다.")
    result = []
    for row in table.find_all("tr"):
        order_input = row.select_one('input[name="sOrdValue"]')
        if not order_input:
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) != 11:
            raise DataError("중판 상품 표 구조가 변경되었습니다.")
        child = order_input.parent.select_one('input[name="ORD_SEQ_P"]')
        tracking = row.select_one('input[name="frgValue"]')
        if not child or not tracking:
            raise DataError("상품과 원본 주문을 연결할 수 없습니다.")
        result.append({"unit": cells[0].get_text(strip=True), "child_seq": child["value"],
            "order": order_input.get("value", ""), "tracking": tracking.get("value", ""),
            "name": cells[2].get_text("\n", strip=True).split("\n")[0],
            "work": cells[5].get_text("\n", strip=True), "quantity": cells[8].get_text(strip=True),
            "item_amount": cells[9].get_text(strip=True),
            "status": cells[10].get_text("\n", strip=True).split("\n")[0]})
    if not result:
        raise DataError("상품을 읽지 못했습니다. 0건으로 처리하지 않습니다.")
    return result


def parse_child(html, expected):
    doc = soup(html)
    row, cells, seq = order_row(doc, seq=expected["seq"], number=expected["number"])
    prices = re.findall(r"[¥￥]\s*([\d,.]+)", cells[7].get_text(" ", strip=True))
    if len(prices) != 2:
        raise DataError("총액·총신고금액 칸의 형식이 바뀌었습니다.")
    shipping = cells[4].get_text("\n", strip=True).split("\n")
    def memo(name):
        element = doc.find(id=f"{name}{seq}")
        if element is None:
            raise DataError("원본 메모 필드가 누락되었습니다.")
        return element.get("value", "")
    box = doc.find(id=f"BoxCnt{seq}")
    if box is None:
        raise DataError("박스 기록 필드가 누락되었습니다.")
    selected = box.find("option", selected=True) or box.find("option")
    return {**expected, "amount": prices[-1], "shipping": shipping[0],
        "recipient": " ".join(shipping[1:]), "member": cells[3].get_text(" ", strip=True),
        "ware_err": memo("WareErrMemo"), "manager_memo": memo("MgrMemo"),
        "admin_memo": memo("AdmMemo"), "box_field": selected.get("value", "0") if selected else "0"}


class Jungpan:
    def __init__(self):
        self.client = httpx.Client(base_url=BASE, timeout=45, follow_redirects=False,
                                   headers={"User-Agent": "BL-Cost/1.0", "Referer": BASE + "/Admin/Login.asp"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def request(self, method, path, **kwargs):
        for attempt in range(3):
            try:
                response = self.client.request(method, path, **kwargs)
                if 300 <= response.status_code < 400:
                    raise DataError("중판 로그인이 만료되었거나 접속 경로가 변경되었습니다. 다시 조회해 주세요.")
                response.raise_for_status()
                if "Login.asp" in str(response.url) and path != "/Admin/Login.asp":
                    raise DataError("중판 로그인이 만료되었습니다. 다시 조회해 주세요.")
                return response.text
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError):
                if attempt == 2:
                    raise DataError("중판 조회가 일시적으로 실패했습니다. 진행 내역을 보존했습니다.") from None
                time.sleep(attempt + 1)

    def login(self):
        names = ("JUNGPAN_ADMIN_ID", "JUNGPAN_ADMIN_PW", "JUNGPAN_ADMIN_PWD2")
        values = [os.getenv(n, "") for n in names]
        if not all(values):
            raise DataError("Render에 중판 ID·PW·PWD2 환경변수를 설정해 주세요.")
        self.request("GET", "/Admin/Login.asp")
        result = self.request("POST", "/Admin/Login_Ajax.asp", data={
            "sMemId": values[0], "sMemPw": values[1], "sMemTEL": values[2], "rememberid": "N"})
        if result.strip() != "1":
            raise DataError("중판 로그인에 실패했습니다. ID·PW·PWD2와 접속 제한을 확인해 주세요.")

    def search(self, number):
        return self.request("POST", LIST, data={"shOrdNo": number, "shPageSize": "100",
             "shBeginDay": "2000-01-01", "shEndDay": datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
             "shSendType": "", "shStatusCd": "", "shChTrkNo": "", "shChOrdNo": ""})

    def collect(self, bl, checkpoint=lambda data, done, total: None, cached=None):
        self.login()
        seq, specs = parse_parent(self.search(bl), bl)
        products = parse_products(self.request("POST", "/Admin/1688/Order_Pro_V_Data.asp", data={"ORD_SEQ": seq}))
        valid = {c["seq"]: c for c in specs}
        children = {c["seq"]: c for c in (cached or {}).get("children", []) if c["seq"] in valid}
        for spec in specs:
            if spec["seq"] not in children:
                children[spec["seq"]] = parse_child(self.search(spec["number"]), spec)
            snapshot = {"bl": bl, "parent_seq": seq, "products": products,
                        "children": [children[s["seq"]] for s in specs if s["seq"] in children]}
            checkpoint(snapshot, len(children), len(specs))
            time.sleep(0.1)
        return {"bl": bl, "parent_seq": seq, "products": products,
                "children": [children[s["seq"]] for s in specs]}
