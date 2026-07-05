#!/usr/bin/env python3
"""배송 추적 알림 에이전트: EMS행방조회 API(해외 발송~국내 도착 전 구간 커버) 조회 후 텔레그램 발송.

EMS는 만국우편연합(UPU) 국제 네트워크라 국내 우정사업본부 API만으로
발송국 출고 시점부터의 이벤트가 함께 조회된다. 위치 코드(nowLc)가
"KR"로 시작하지 않으면 해외 구간으로 간주해 아이콘을 구분한다.
"""
import json
import logging
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("tracker")

STATE_FILE = Path(__file__).parent / "last_status.json"

EMS_URL = (
    "http://openapi.epost.go.kr/trace/retrieveLongitudinalEMSService/"
    "retrieveLongitudinalEMSService/getLongitudinalEMSList"
)
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def normalize_time(raw: str) -> str:
    """EMS 응답의 다양한 시각 포맷을 'YYYY-MM-DD HH:MM' 으로 정규화."""
    if not raw:
        return ""
    raw = raw.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    logger.warning("알 수 없는 시각 포맷, 원본 유지: %s", raw)
    return raw


def fetch_ems(tracking_number: str, service_key: str) -> list[dict]:
    """우정사업본부 EMS행방조회 OPEN API 조회 (국내 구간). 실패 시 빈 리스트 반환."""
    events: list[dict] = []
    try:
        resp = requests.get(
            EMS_URL,
            params={"serviceKey": service_key, "rgist": tracking_number},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("EMS 조회 실패 (%s): %s", tracking_number, e)
        return events

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        logger.error("EMS 응답 XML 파싱 실패 (%s): %s / raw=%s", tracking_number, e, resp.text[:500])
        return events

    header = root.find("cmmMsgHeader")
    if header is not None and (header.findtext("successYN") or "") != "Y":
        logger.error(
            "EMS 응답 실패 (%s): returnCode=%s errMsg=%s",
            tracking_number,
            header.findtext("returnCode"),
            header.findtext("errMsg"),
        )
        return events

    rows = root.findall(".//longitudinalEMSList")

    def find_text(row, *tags):
        for tag in tags:
            el = row.find(tag)
            if el is not None and el.text:
                return el.text.strip()
        return ""

    for row in rows:
        raw_time = find_text(row, "processDe")
        location = find_text(row, "nowLc")
        events.append({
            "time": normalize_time(raw_time),
            "status": find_text(row, "processSttus"),
            "description": find_text(row, "detailDc"),
            "location": location,
            "leg": "domestic" if location.upper().startswith("KR") else "intl",
        })

    if not events:
        logger.warning(
            "EMS: %s 이벤트 없음. 실제 응답 구조 확인 필요: %s",
            tracking_number, resp.text[:500],
        )
    return events


def collect_events(tracking_number: str, ems_key: str) -> list[dict]:
    events = fetch_ems(tracking_number, ems_key)
    events.sort(key=lambda e: e["time"])
    return events


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            logger.warning("last_status.json 파싱 실패, 빈 상태로 시작")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def diff_new_events(previous_events: list[dict], current_events: list[dict]) -> list[dict]:
    seen = {(e["time"], e["status"], e["description"]) for e in previous_events}
    return [e for e in current_events if (e["time"], e["status"], e["description"]) not in seen]


def build_message(tracking_number: str, new_events: list[dict]) -> str:
    lines = [f"<b>📦 {tracking_number}</b>"]
    if not new_events:
        lines.append("변동 없음")
    else:
        for e in new_events:
            icon = "✈️" if e["leg"] == "intl" else "🚚"
            loc = f" ({e['location']})" if e["location"] else ""
            lines.append(f"{icon} {e['time']} {e['status']}{loc} - {e['description']}")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error("텔레그램 전송 실패: %s", e)
        return False


def main() -> None:
    ems_key = os.environ.get("EMS_SERVICE_KEY", "")
    telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    tracking_numbers = [t.strip() for t in os.environ.get("TRACKING_NUMBERS", "").split(",") if t.strip()]

    if not tracking_numbers:
        logger.error("TRACKING_NUMBERS 환경변수가 비어 있습니다.")
        sys.exit(1)
    if not telegram_token or not telegram_chat_id:
        logger.error("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID 가 설정되지 않았습니다.")
        sys.exit(1)

    state = load_state()
    message_blocks = []
    new_state = {}

    for tn in tracking_numbers:
        try:
            current_events = collect_events(tn, ems_key)
        except Exception:
            logger.exception("운송장 %s 처리 중 예외 발생", tn)
            current_events = []

        previous_events = state.get(tn, [])
        new_events = diff_new_events(previous_events, current_events)
        message_blocks.append(build_message(tn, new_events))
        new_state[tn] = current_events

    text = "\n\n".join(message_blocks)

    if send_telegram(telegram_token, telegram_chat_id, text):
        save_state(new_state)
        logger.info("텔레그램 전송 성공, 상태 갱신 완료")
    else:
        logger.error("텔레그램 전송 실패 - 상태를 갱신하지 않음 (다음 실행에서 재시도)")
        sys.exit(1)


if __name__ == "__main__":
    main()
