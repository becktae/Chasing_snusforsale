#!/usr/bin/env python3
"""배송 추적 알림 에이전트: PostNord(해외 구간) + EMS(국내 구간) 통합 조회 후 텔레그램 발송.

주의: PostNord/EMS 실제 응답 필드명이 문서와 다를 수 있음.
첫 실행에서 이벤트가 비어 있으면 로그에 남는 raw 응답을 확인하고
fetch_postnord()/fetch_ems() 의 필드 매핑을 조정할 것.
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

POSTNORD_URL = "https://api2.postnord.com/rest/shipment/v2/trackandtrace/findByIdentifier.json"
EMS_URL = (
    "http://openapi.epost.go.kr/trace/retrieveLongitudinalEMSService/"
    "retrieveLongitudinalEMSService/getLongitudinalEMSList"
)
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def normalize_time(raw: str) -> str:
    """PostNord(ISO8601)/EMS(다양한 포맷) 시각 문자열을 'YYYY-MM-DD HH:MM' 으로 정규화."""
    if not raw:
        return ""
    raw = raw.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass
    for fmt in ("%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    logger.warning("알 수 없는 시각 포맷, 원본 유지: %s", raw)
    return raw


def fetch_postnord(tracking_number: str, api_key: str) -> list[dict]:
    """PostNord Track & Trace API 조회 (해외 구간). 실패 시 빈 리스트 반환."""
    events: list[dict] = []
    try:
        resp = requests.get(
            POSTNORD_URL,
            params={"id": tracking_number, "locale": "en", "apikey": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error("PostNord 조회 실패 (%s): %s", tracking_number, e)
        return events
    except ValueError as e:
        logger.error("PostNord 응답 JSON 파싱 실패 (%s): %s", tracking_number, e)
        return events

    try:
        shipments = data.get("TrackingInformationResponse", {}).get("shipments") or []
        for shipment in shipments:
            for item in shipment.get("items") or []:
                for ev in item.get("events") or []:
                    location = ev.get("location")
                    if isinstance(location, dict):
                        location = location.get("displayName", "")
                    events.append({
                        "time": normalize_time(ev.get("eventTime", "")),
                        "status": ev.get("status", ""),
                        "description": ev.get("eventDescription") or ev.get("description", ""),
                        "location": location or "",
                        "leg": "intl",
                    })
    except (AttributeError, TypeError) as e:
        logger.error(
            "PostNord 응답 구조가 예상과 다릅니다 (%s): %s / raw=%s",
            tracking_number, e, json.dumps(data, ensure_ascii=False)[:500],
        )

    if not events:
        logger.warning(
            "PostNord: %s 이벤트 없음. 실제 응답 구조 확인 필요: %s",
            tracking_number, json.dumps(data, ensure_ascii=False)[:500],
        )
    return events


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

    rows = root.findall(".//item") or root.findall(".//cmsLongitudinalEMSDVO") or root.findall(".//trackList")

    def find_text(row, *tags):
        for tag in tags:
            el = row.find(tag)
            if el is not None and el.text:
                return el.text.strip()
        return ""

    for row in rows:
        raw_time = find_text(row, "processDate", "trackingDate", "regDate", "applDate")
        events.append({
            "time": normalize_time(raw_time),
            "status": find_text(row, "processStatus", "trackingKindDetail", "kindDetail"),
            "description": find_text(row, "processDetail", "detail", "kindDetail"),
            "location": find_text(row, "processLocation", "location", "officeName"),
            "leg": "domestic",
        })

    if not events:
        logger.warning(
            "EMS: %s 이벤트 없음. 실제 응답 구조 확인 필요: %s",
            tracking_number, resp.text[:500],
        )
    return events


def collect_events(tracking_number: str, postnord_key: str, ems_key: str) -> list[dict]:
    events = fetch_postnord(tracking_number, postnord_key) + fetch_ems(tracking_number, ems_key)
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
    postnord_key = os.environ.get("POSTNORD_API_KEY", "")
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
            current_events = collect_events(tn, postnord_key, ems_key)
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
