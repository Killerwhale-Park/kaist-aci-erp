# Slack Student Expense Support ERP MVP

대학 공식 ERP로 전달하기 전 학생 비용·증빙 제출과 내부 승인을 처리하는 Slack-native 사전 정산 시스템입니다. 하나의 Slack App과 backend를 4개 학과가 공동 사용하며, 학생은 private approval channel에 참여하지 않습니다.

이 MVP는 공식 ERP, 결제, Google Drive 업로드, 회계 규정 판정 기능을 제공하지 않습니다. 파일은 Google Drive에 저장하고 이 시스템에는 HTTPS URL과 제출 상태만 저장합니다.

상세 설계는 [docs/design.md](docs/design.md)를 참고하세요.

## 제공 기능

- 역할별 Slack App Home과 개인 신청 목록
- `/expense`와 App Home 기반 2단계 Block Kit Modal
- Supplies, Lodging, Airfare, Conference Registration 카테고리
- PRE/POST 및 REQUIRED/OPTIONAL 증빙 정의와 request snapshot
- Google Drive folder URL 및 개별 evidence URL
- 학과·예산·카테고리별 generic N-step workflow
- 신청 시점 approval workflow snapshot
- Slack actor ID 기반 server-side authorization
- Approve, Request Changes, Reject, 같은 단계부터 Resubmit
- required POST evidence 완료 처리
- 학과별 private approval channel 메시지와 신청자/다음 승인자 DM
- append-only approval action log
- PostgreSQL 운영 구성과 SQLite 로컬 구성
- FastAPI health/readiness endpoint, Alembic, pytest

## 중요한 초기 정책

증빙 후보의 실제 필수 여부는 제공된 요구사항만으로 확정할 수 없습니다. 따라서 초기 seed는 모든 증빙 후보를 `OPTIONAL`로 넣습니다. 정책 책임자가 확인한 항목만 `EvidenceRequirementDefinition.requirement`를 `REQUIRED`로 변경한 뒤 운영해야 합니다.

승인선은 동작 예시입니다. Supplies는 Professor → Administration → Inspection, 나머지 카테고리는 Professor → Administration으로 seed됩니다. `.env`의 실제 Slack user ID와 channel ID를 설정하고 대학 정책에 맞게 definition과 rule을 확정해야 합니다. 이미 제출된 신청은 변경 전 snapshot을 유지합니다.

## 요구 환경

- Python 3.12+
- PostgreSQL 15+ 운영 권장
- Slack workspace에서 Custom App을 생성하고 설치할 권한
- HTTPS로 접근 가능한 배포 URL

## 로컬 실행

```bash
python -m venv .venv
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload --port 8000
```

기본 `.env.example`은 SQLite를 사용합니다. `AUTO_CREATE_SCHEMA=true`는 빈 로컬 DB의 편의를 위한 설정입니다. migration 검증을 위해서는 `AUTO_CREATE_SCHEMA=false`로 두고 `alembic upgrade head`를 실행하는 방식을 권장합니다.

확인 endpoint:

```text
GET http://localhost:8000/health
GET http://localhost:8000/ready
POST http://localhost:8000/slack/events
```

Slack이 로컬 서버에 접근하려면 ngrok 또는 같은 HTTPS tunnel을 사용하고 manifest의 `https://example.com`을 tunnel URL로 교체합니다.

## Slack App 설정

1. Slack API의 **Create New App → From an app manifest**를 선택합니다.
2. [slack-manifest.yaml](slack-manifest.yaml)의 `https://example.com`을 실제 공개 base URL로 교체해 등록합니다.
3. App Home의 Home Tab과 Messages Tab을 활성화합니다.
4. Interactivity를 활성화하고 Request URL을 `https://YOUR_DOMAIN/slack/events`로 설정합니다.
5. Event Subscriptions Request URL도 같은 endpoint로 설정하고 `app_home_opened` bot event를 등록합니다.
6. `/expense` slash command의 Request URL도 같은 endpoint로 설정합니다.
7. 앱을 workspace에 설치하고 Bot User OAuth Token과 Signing Secret을 `.env`에 넣습니다.
8. bot을 네 개의 private approval channel 각각에 초대합니다.
9. channel ID와 실제 승인자 member ID를 `.env`에 입력한 뒤 DB를 seed합니다.

### OAuth scopes

- `commands`: `/expense` command와 app entry point 사용
- `chat:write`: private approval channel 메시지, 업데이트, ephemeral response, DM
- `users:read`: canonical Slack user ID에 연결된 display name 조회

`app_home_opened`, Modal open/update, App Home publish 자체에는 추가 OAuth scope가 필요하지 않습니다. Private channel에는 scope만으로 접근할 수 없으며 bot이 반드시 해당 채널의 멤버여야 합니다.

향후 관리 UI에서 private channel 멤버만 승인자 후보로 조회할 경우 `groups:read`를 추가하고 `conversations.members` 결과를 server-side로 검증해야 합니다. 현재 MVP는 최소 권한을 위해 이 scope와 해당 UI를 포함하지 않습니다.

공식 참고 문서:

- [Bolt FastAPI async adapter](https://docs.slack.dev/tools/bolt-python/reference/adapter/fastapi/async_handler.html)
- [Slack modals](https://docs.slack.dev/surfaces/modals/)
- [App Home](https://docs.slack.dev/surfaces/app-home/)
- [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)
- [commands scope](https://docs.slack.dev/reference/scopes/commands/)

## Configuration seed

학과, 프로그램, 카테고리, 증빙 후보, sample workflow는 `app/config/`에 있습니다. `python -m app.db.seed`는 누락된 definition만 추가하며 기존 runtime configuration을 덮어쓰지 않습니다.

```text
app/config/departments.py
app/config/budgets.py
app/config/categories.py
app/config/workflows.py
```

운영 정책 변경 순서:

1. 승인 채널과 승인자 Slack ID를 확정합니다.
2. Evidence의 REQUIRED/OPTIONAL 및 PRE/POST를 정책 책임자가 검토합니다.
3. 새 version의 `ApprovalWorkflowDefinition`과 ordered step을 추가합니다.
4. 해당 학과·예산·카테고리 `ApprovalRule`을 새 workflow로 연결합니다.
5. 새 신청으로 검증합니다. 기존 신청의 snapshot은 수정하지 않습니다.

`SYSTEM_ADMIN`은 App Home에서 관리 section을 볼 수 있습니다. 현재 MVP에는 write-capable 관리 UI가 없으므로 runtime definition 변경은 승인된 운영자가 migration/seed 또는 제한된 DB 운영 절차로 수행합니다. 일반 Slack 사용자가 설정을 변경하는 endpoint는 노출하지 않습니다.

## 테스트

```powershell
pytest
```

핵심 테스트는 다음을 포함합니다.

- 1, 2, 3단계 승인과 완료
- 승인 권한 없는 사용자 및 다른 학과 승인자 차단
- 중간 단계 반려
- 수정 요청 후 같은 단계부터 재개
- required POST evidence 대기 후 완료
- optional evidence 누락 허용
- definition 변경 후 기존 request snapshot 불변

## PostgreSQL 및 Docker 배포

로컬 PostgreSQL 또는 managed PostgreSQL의 URL을 설정합니다.

```text
DATABASE_URL=postgresql+psycopg://erp:password@db:5432/expense_erp
AUTO_CREATE_SCHEMA=false
SEED_CONFIGURATION=false
```

배포 순서는 다음과 같습니다.

```bash
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Docker Compose 예시는 다음 명령으로 실행합니다.

```bash
docker compose up --build
```

운영에서는 TLS를 reverse proxy 또는 cloud load balancer에서 종료하고, `.env`를 이미지에 포함하지 말고 secret manager를 사용하세요. DB backup, log retention, Slack token rotation, private channel membership을 운영 체크리스트에 포함해야 합니다.

## 프로젝트 구조

```text
app/
├── approvals/
├── config/
├── db/
├── expenses/
├── i18n/
├── slack/
├── users/
└── main.py
alembic/
docs/
tests/
```

