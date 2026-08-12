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
- App Home에서 approval channel, N단계, 복수 승인자를 변경하는 관리자 flow
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

승인 단계 이름은 동작 예시입니다. Supplies는 Professor → Administration → Inspection, 나머지 카테고리는 Professor → Administration으로 seed되지만, 채널과 승인자는 비어 있습니다. 최초 관리자가 Slack App Home의 관리 flow에서 실제 값을 지정해야 새 신청을 제출할 수 있습니다. 이미 제출된 신청은 변경 전 snapshot을 유지합니다.

한 단계에 승인자를 여러 명 지정하면 그중 한 명의 승인으로 다음 단계로 진행하는 `ANY` 정책을 사용합니다. 승인자가 없는 단계도 임시 저장할 수 있지만 해당 rule은 미완성 상태이며, 그 rule을 사용하는 새 신청은 차단됩니다. 사람이 바뀌면 관리자가 새 rule version을 저장하므로 기존 신청의 승인자는 바뀌지 않습니다.

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
8. `.env`의 `BOOTSTRAP_SYSTEM_ADMIN_SLACK_USER_IDS`에 최초 관리자 Slack member ID를 넣고 DB를 seed합니다.
9. bot을 사용할 private approval channel에 초대합니다.
10. 최초 관리자가 App Home의 **Manage Approval Rules**에서 학과·카테고리별 채널, 단계, 승인자를 저장합니다.
11. 관리 flow에서 다른 `SYSTEM_ADMIN`을 지정한 뒤에는 bootstrap 환경 변수가 runtime 권한을 덮어쓰지 않습니다.

### OAuth scopes

- `commands`: `/expense` command와 app entry point 사용
- `chat:write`: private approval channel 메시지, 업데이트, ephemeral response, DM
- `groups:read`: bot이 참여한 private channel의 정보와 멤버를 확인하여 채널·승인자 설정 검증
- `users:read`: canonical Slack user ID에 연결된 display name 조회

`app_home_opened`, Modal open/update, App Home publish 자체에는 추가 OAuth scope가 필요하지 않습니다. Private channel에는 scope만으로 접근할 수 없으며 bot이 반드시 해당 채널의 멤버여야 합니다.

관리 화면의 Slack 사용자 selector는 workspace 사용자를 표시합니다. 저장할 때 backend가 `conversations.info`와 `conversations.members`로 채널이 private인지, bot과 지정 승인자 모두 채널 멤버인지 다시 검증합니다. scope를 추가하거나 변경한 경우 앱을 workspace에 다시 설치해야 새 권한이 적용됩니다.

공식 참고 문서:

- [Bolt FastAPI async adapter](https://docs.slack.dev/tools/bolt-python/reference/adapter/fastapi/async_handler.html)
- [Slack modals](https://docs.slack.dev/surfaces/modals/)
- [App Home](https://docs.slack.dev/surfaces/app-home/)
- [chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)
- [commands scope](https://docs.slack.dev/reference/scopes/commands/)
- [groups:read scope](https://docs.slack.dev/reference/scopes/groups.read/)

## Configuration seed

학과, 프로그램, 카테고리, 증빙 후보, sample workflow는 `app/config/`에 있습니다. `python -m app.db.seed`는 누락된 definition만 추가하며 기존 runtime configuration을 덮어쓰지 않습니다.

```text
app/config/departments.py
app/config/budgets.py
app/config/categories.py
app/config/workflows.py
```

최초 설정과 운영 정책 변경 순서:

1. private approval channel을 만들고 bot과 승인자를 초대합니다.
2. 관리자가 App Home → **Manage Approval Rules** → **Configure Approval Rules**를 엽니다.
3. 학과와 카테고리를 고른 뒤 채널, 단계 이름, 단계별 0명 이상의 승인자를 설정합니다.
4. **Add Approval Step** 또는 **Remove**로 N단계를 구성하고 저장합니다.
5. 필요하면 **Manage System Administrators**에서 운영 관리자를 교체하거나 추가합니다. 최소 한 명은 남아야 합니다.
6. Evidence의 REQUIRED/OPTIONAL 및 PRE/POST는 정책 책임자가 검토합니다.
7. 새 신청으로 검증합니다. 기존 신청의 snapshot은 수정하지 않습니다.

`SYSTEM_ADMIN`만 관리 section과 설정 modal을 사용할 수 있으며 모든 저장 요청은 backend에서 역할을 다시 확인합니다. `BOOTSTRAP_SYSTEM_ADMIN_SLACK_USER_IDS`는 관리자 계정이 DB에 하나도 없을 때만 적용되는 복구 가능한 최초 진입점입니다. 학과 채널과 승인자 정보는 환경 변수가 아니라 versioned DB configuration입니다.

## Slack에서 수동 테스트

1. 테스트용 private channel에 bot과 승인자 계정을 초대합니다.
2. 관리자 계정으로 App Home을 열고 학과·카테고리 rule을 저장합니다.
3. 학생 계정에서 `/expense`를 실행해 신청하고, 학과 channel에 승인 메시지가 생기는지 확인합니다.
4. 지정하지 않은 계정으로 Approve를 눌러 권한 오류와 상태 불변을 확인합니다.
5. 지정 승인자 중 한 명으로 Approve를 눌러 다음 단계와 current reviewer가 갱신되는지 확인합니다.
6. Request Changes 후 학생 DM의 Edit Request로 재제출하고 같은 단계가 재개되는지 확인합니다.
7. Airfare 등의 required POST 증빙 정책을 설정한 환경에서는 최종 승인 후 URL 제출 전후 상태를 확인합니다.

`/expense`가 보이지 않거나 Modal이 열리지 않으면 공개 tunnel URL, 세 곳의 `/slack/events` URL, 앱 재설치 여부를 먼저 확인하세요. 설정 저장이 거부되면 bot 또는 선택한 승인자가 private channel 멤버인지 확인합니다.

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
- runtime rule의 복수 승인자와 미완성 rule 차단
- bootstrap 관리자보다 DB runtime 관리자 우선

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
