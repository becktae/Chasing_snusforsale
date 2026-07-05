# chasing_snusforsale

국제우편 운송장의 배송 상황을 매일 1회 조회하고, 이전 조회 대비 새 이벤트를 요약해 텔레그램으로 보내는 스크립트입니다.

- 데이터 소스: [우정사업본부 EMS행방조회 OPEN API](https://www.data.go.kr) (공공데이터포털)
  - EMS는 만국우편연합(UPU) 국제 네트워크라 발송국 출고 시점부터의 이벤트까지 이 API 하나로 조회됩니다.
  - 위치 코드(`nowLc`)가 `KR`로 시작하지 않으면 해외 구간으로 간주해 아이콘(✈️/🚚)을 구분합니다.

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env
```

`.env`를 열어 아래 값을 채웁니다.

| 변수 | 발급 방법 |
|---|---|
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

## 매일 오전 9시 자동 실행 (launchd)

macOS에서는 `crontab` 쓰기가 TCC(개인정보 보호) 제한으로 막히는 경우가 있어, 표준 스케줄러인
`launchd`를 사용합니다. `run_tracker.sh`가 `.env`를 로드한 뒤 `tracker.py`를 실행합니다.

`~/Library/LaunchAgents/com.becktae.chasing-snusforsale-tracker.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.becktae.chasing-snusforsale-tracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/becktae/project/chasing_snusforsale/run_tracker.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/becktae/project/chasing_snusforsale</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/becktae/project/chasing_snusforsale/tracker.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/becktae/project/chasing_snusforsale/tracker.log</string>
</dict>
</plist>
```

등록/해제/수동 실행 명령:

```bash
# 등록 (최초 1회, 또는 plist 수정 후 재등록)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.becktae.chasing-snusforsale-tracker.plist

# 상태 확인
launchctl print gui/$(id -u)/com.becktae.chasing-snusforsale-tracker

# 지금 바로 한 번 실행해보기 (스케줄과 무관하게 즉시 트리거)
launchctl kickstart -k gui/$(id -u)/com.becktae.chasing-snusforsale-tracker

# 해제
launchctl bootout gui/$(id -u)/com.becktae.chasing-snusforsale-tracker
```

`run_tracker.sh`가 `.env`를 직접 로드하므로 별도 환경변수 주입이 필요 없습니다. 실행 로그는
`tracker.log`에 쌓입니다.

## 테스트

실제 API 키와 텔레그램 설정을 `.env`에 채운 뒤 한 번 실행해 텔레그램 메시지가 오는지 확인합니다.

```bash
python tracker.py
```

## 참고 — 실제 응답 구조 검증

EMS의 실제 응답 필드명이 문서와 다를 수 있습니다. 첫 실행에서 이벤트가 비어 있으면
로그에 남는 raw 응답(`WARNING` 레벨)을 확인하고 `tracker.py`의 `fetch_ems()` 필드 매핑을
실제 응답에 맞게 조정하세요.
