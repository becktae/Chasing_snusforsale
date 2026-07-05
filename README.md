# chasing_snusforsale

국제우편 운송장의 배송 상황을 매일 1회 조회하고, 이전 조회 대비 새 이벤트를 요약해 텔레그램으로 보내는 스크립트입니다.

- 해외 구간: [PostNord Track & Trace API](https://developer.postnord.com)
- 국내 구간: [우정사업본부 EMS행방조회 OPEN API](https://www.data.go.kr) (공공데이터포털)

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env
```

`.env`를 열어 아래 값을 채웁니다.

| 변수 | 발급 방법 |
|---|---|
| `POSTNORD_API_KEY` | https://developer.postnord.com 가입 후 Free 플랜 신청 |
| `EMS_SERVICE_KEY` | https://www.data.go.kr 에서 "EMS행방조회 서비스" 활용신청 후 발급되는 인증키 |
| `TELEGRAM_TOKEN` | 텔레그램 [@BotFather](https://t.me/BotFather)에서 `/newbot`으로 봇 생성 |
| `TELEGRAM_CHAT_ID` | 생성한 봇에게 아무 메시지나 보낸 뒤, `https://api.telegram.org/bot<TOKEN>/getUpdates` 응답의 `chat.id` 확인 |
| `TRACKING_NUMBERS` | 추적할 운송장번호 (콤마로 여러 개, 예: `LA121547979SE,LA987654321SE`) |

## 실행

```bash
python tracker.py
```

- 조회 결과는 `last_status.json`에 저장되고, 다음 실행 시 이전 상태와 비교해 새 이벤트만 표시합니다.
- 텔레그램 전송이 성공했을 때만 `last_status.json`이 갱신됩니다. 전송에 실패하면 다음 실행에서 같은 이벤트로 재시도합니다.
- 변동이 없어도 "변동 없음" 메시지를 매일 보냅니다.

## cron 등록 (매일 오전 9시 실행)

```bash
crontab -e
```

아래 줄을 추가합니다 (경로는 실제 프로젝트 위치로 변경):

```cron
0 9 * * * cd /Users/becktae/project/chasing_snusforsale && /usr/bin/env python3 tracker.py >> tracker.log 2>&1
```

`.env`는 자동으로 로드되지 않으므로, cron 환경에서 실행하려면 셸 wrapper로 환경변수를 주입하거나 `python-dotenv` 같은 라이브러리를 추가해 `tracker.py` 상단에서 `.env`를 로드하도록 확장하세요.

## 테스트

실제 API 키와 텔레그램 설정을 `.env`에 채운 뒤 한 번 실행해 텔레그램 메시지가 오는지 확인합니다.

```bash
python tracker.py
```

## 참고 — 실제 응답 구조 검증

PostNord/EMS의 실제 응답 필드명이 문서와 다를 수 있습니다. 첫 실행에서 이벤트가 비어 있으면
로그에 남는 raw 응답(`WARNING` 레벨)을 확인하고 `tracker.py`의 `fetch_postnord()` / `fetch_ems()`
필드 매핑을 실제 응답에 맞게 조정하세요.
