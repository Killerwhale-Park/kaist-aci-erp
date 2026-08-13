# AIC Expense Approval

AI Computing, AI System, AX, AI Future가 공동으로 사용하는 Slack 기반 사전 정산·승인 앱입니다.

- 사용자 화면: Slack App Home, Modal, DM
- 증빙: Google Drive URL
- 승인 및 상태 저장: 관리자가 지정한 비공개 채널의 메시지와 스레드
- 업무 요청: 학생의 구매 요청, 교수·행정 담당자의 학생 정산 요청
- 실행 환경: FastAPI on Vercel

별도 웹 화면과 데이터베이스는 사용하지 않습니다. 승인 단계는 설정에 따라 N단계로 동작합니다. 내부 구조는 [설계 문서](docs/design.md)에 정리되어 있습니다.

## Slack 준비

다음 비공개 채널을 분리합니다.

- System Config: 관리자와 봇만 참여하며 최신 전체 설정 snapshot만 저장
- Audit: 설정 변경 이력만 저장하며 애플리케이션의 읽기 대상이 아님
- Alerts: 중복 억제된 운영 오류만 저장하며 애플리케이션의 읽기 대상이 아님
- Operating: 실제 신청·승인 메시지와 상태 변경 thread를 저장

학과별·재원별 운영 채널은 필요한 만큼 만들 수 있습니다. 하나의 채널을 여러 승인 route가 공유해도 됩니다.

Bot scopes:

```text
chat:write
commands
groups:history
groups:read
users:read
```

scope를 변경했다면 앱을 workspace에 다시 설치합니다. 봇은 사용하는 모든 승인 채널에 초대해야 합니다.

## 환경변수

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_SYSTEM_CHANNEL_ID=C...
```

`SLACK_SYSTEM_CHANNEL_ID`만 고정 locator로 사용합니다. 학과·재원별 운영 채널, Audit, Alerts 채널은 Slack UI에서 관리합니다. 앱이 아직 비공개 채널 하나에만 참여한 초기 상태에서는 그 채널을 임시 System Config 채널로 자동 인식하지만, 다른 채널을 초대하기 전에 이 환경변수를 반드시 등록합니다.

최초 복구 관리자는 `app/config/roles.py`에 명시합니다. 학생 담당자·교수·행정팀·시스템 관리자는 App Home의 `Access Roles / 접근 역할`에서 워크스페이스 공통 Role로 관리합니다. 일반 학생은 별도 등록하지 않고 운영 채널 멤버십으로 신청 자격을 얻습니다. 실제 승인 권한은 `Role 보유자 ∩ 운영 채널 멤버`로 계산됩니다.

## Vercel 배포

1. 이 저장소를 Vercel에 Import합니다.
2. Framework Preset은 `FastAPI`, Root Directory는 저장소 루트로 둡니다.
3. 위 환경변수를 Production 환경에 등록합니다. 토큰과 signing secret은 Sensitive로 지정합니다.
4. 배포 후 다음 주소를 확인합니다.

```text
https://kaist-aci-erp.vercel.app/health
https://kaist-aci-erp.vercel.app/ready
```

둘 다 `{"status":"ok"}`를 반환해야 합니다.

Slack App 설정의 다음 세 Request URL에는 모두 아래 주소를 넣습니다.

- Slash Commands → `/expense`
- Interactivity & Shortcuts
- Event Subscriptions

```text
https://kaist-aci-erp.vercel.app/slack/events
```

배포 후 설정 순서:

1. `System Configuration → System Channels`에서 Audit, Alerts, 추가 운영 채널을 지정합니다.
2. `Access Roles`에서 워크스페이스 공통 Role을 지정합니다.
3. `Approval Routing`에서 학과·재원별 운영 채널을 지정합니다.

Workflow 단계는 `app/config/workflows.py`, 재원→workflow 연결은 `app/config/workflow_mappings.py`에 독립적으로 정의됩니다.

## Slack 사용

App Home에서 다음 작업을 시작합니다.

- `Purchase Request / 구매 요청`: 구매 담당자와 게시할 비공개 채널을 선택합니다.
- `Assign Settlement / 정산 요청 보내기`: 승인자 또는 시스템 관리자만 사용할 수 있으며, 정산할 학생과 게시 채널을 선택합니다. 학생이 요청 카드에서 정산 작성을 누르면 전달된 구매 정보가 정산 폼에 채워집니다.
- `New Expense Request / 새 정산 신청`: 상위 분류를 고르면 다음 분류가 나타납니다. 현재 실제 경로는 `학과예산 → 학사계발비 → 비품비`입니다.

예산 분류는 고정된 단계 수를 사용하지 않습니다. 설정된 트리에 따라 2단계, 3단계, 5단계 모두 같은 방식으로 표시되며 최종 지출항목을 선택해야 제출할 수 있습니다.

재원 항목과 증빙 양식은 독립된 설정입니다. 최종 재원 항목과 양식을 mapping하므로 같은 항목명도 재원 경로에 따라 다른 양식을 사용할 수 있고, 같은 양식을 여러 경로가 재사용할 수도 있습니다.

학과별 예산과 학과 학생회 예산은 공통 기본 양식을 사용하되 학과별 override를 둘 수 있습니다. 단과대 예산은 global scope이므로 신청자 학과와 관계없이 하나의 mapping을 사용합니다.

구매 요청과 정산 요청은 같은 채널 또는 서로 다른 채널로 보낼 수 있습니다. Approval Routing에 없는 업무 채널은 `System Channels → Additional Operating Channels`에 등록합니다. 운영 채널에는 봇과 해당 업무 참여자를 초대합니다.

신청자 구분에서 학생을 선택하면 학번, 교수를 선택하면 사번을 입력합니다.

## 개발 및 검사

Python 3.12 이상이 필요합니다.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```

## 데이터 보존

Slack 메시지 보존 정책이 곧 기록 보존 기간입니다. 이 앱은 공식 회계 원장이 아니라 행정 전달 전 협업 도구입니다. 장기 보존이 필요하면 승인 채널을 정기적으로 내보내 Google Drive에 보관해야 합니다.
