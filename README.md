# AIC Expense ERP

AI Computing, AI System, AX, AI Future가 공동으로 사용하는 Slack 기반 사전 정산·승인 시스템입니다.

- 사용자 화면: Slack App Home, Modal, DM, 승인 메시지
- 원장: PostgreSQL(운영은 Neon, 로컬은 SQLite)
- 증빙 파일: Google Drive에 보관하고 HTTPS URL만 기록
- 실행 환경: FastAPI on Vercel

별도 웹 화면은 없습니다. 예산 분류, 증빙 양식, N단계 승인 Workflow는 서로 독립된 설정 축입니다.

## 구성

```text
Slack UI
   ↓
FastAPI + Slack Bolt
   ↓
Application work queue + lifecycle adapters
   ↓
Conversation defaults → fixed budget snapshot
   ↓
N-step approval workflow / validation
   ↓
SQLAlchemy repository
   ↓
PostgreSQL
```

Slack 메시지는 알림과 상태 화면입니다. 요청, Role, 승인 상태와 변경 이력은 PostgreSQL이 원본이며 메시지가 삭제되어도 보존됩니다.

신청자 구분과 학번·사번은 App Home의 `내 정보`에서 한 번 설정합니다. 이 Profile은
요청의 회계 학과·재원 선택과 독립적이며 정산 양식마다 다시 입력하지 않습니다.

## 배포 준비

Vercel 프로젝트의 Marketplace → Storage에서 Neon을 설치하고 무료 PostgreSQL을 연결합니다. 연결 후 다음 세 환경변수가 Production에 있어야 합니다.

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
DATABASE_URL=postgresql://...
```

Neon/Vercel 연동이 Vercel의 `DATABASE_URL`을 자동으로 추가합니다. 최초 DB 생성 때는 Neon의 **Connect → Direct connection** 문자열을 로컬 `.env`의 `DATABASE_URL`에도 넣고 마이그레이션을 한 번 실행합니다.

```text
# .env (Git에 포함되지 않음)
DATABASE_URL=postgresql://...
```

```powershell
.venv\Scripts\alembic upgrade head
```

마이그레이션 후 GitHub에 push하여 Vercel을 재배포합니다. 배포 확인 시 다음 응답이 모두 `ok`인지 확인합니다.

```text
https://kaist-aci-erp.vercel.app/health
https://kaist-aci-erp.vercel.app/ready
```

상시 uptime 모니터링은 DB를 깨우지 않는 `/health`만 사용합니다. `/ready`는 실제 DB 쿼리를 하므로 주기적으로 호출하지 않습니다.

기존 Slack System Config snapshot을 옮길 때만 아래 일회성 명령을 사용합니다.

```powershell
$env:DATABASE_URL="postgresql://..."
.venv\Scripts\python scripts\import_legacy_slack_config.py `
  --channel C0BPL5WFX51 `
  --actor U0BGPFFNR6W
```

## Slack 설정

Bot scopes:

```text
chat:write
commands
groups:read
users:read
```

Slash Commands, Interactivity, Event Subscriptions의 Request URL은 모두 다음과 같습니다.

```text
https://kaist-aci-erp.vercel.app/slack/events
```

배포 후 App Home에서 순서대로 설정합니다.

1. System Channels: Audit, Alerts, 추가 운영 채널
2. Access Roles: 신청 가능자, 학생 담당자, 교수, 행정팀, 시스템 관리자
3. Approval Procedure: 학과·재원 항목별 승인 절차와 요청 채널

각 사용자는 첫 정산 신청 전에 `내 정보`에서 학생/교수와 학번/사번을 저장합니다.

직접 정산, 구매 요청, 정산 업무 배정은 App Home에서 서로 독립적으로 시작합니다. 직접
정산 양식은 항상 열 수 있지만, 최종 제출에는 선택한 재원 항목의 승인 절차가
필요합니다. 받은 승인·구매·정산 업무는 `내가 받은 요청·할 일`에 모입니다.

정산 업무를 보낼 때 재원 항목을 확정하며 담당 학생은 이를 변경할 수 없습니다. 승인
채널과 증빙 양식은 확정된 재원 설정에서 자동으로 해석됩니다.

반복 입력을 줄이려면 사용할 채널 또는 앱 DM에서 관리자가 `/expense setup`을 실행해
학과·기본 재원을 한 번 저장합니다. 이후 `/expense`는 직접 정산, `/expense purchase`는
구매 요청, `/expense settlement`는 정산 업무 배정을 해당 기본값으로 시작합니다. 이 Context는
입력 기본값일 뿐 승인 경로를 변경하지 않습니다.

역할 기반 승인자는 `전역 Role ∩ 승인 채널 멤버`, 요청에서 명시적으로 지정된 담당자는
`전역 Role`로 검증됩니다. 따라서 명시적 담당자는 DM에서 받은 승인 요청도 처리할 수 있습니다.

## 로컬 개발

Python 3.12 이상이 필요합니다. Docker는 필요하지 않습니다.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:DATABASE_URL="sqlite+aiosqlite:///./aic_erp.db"
alembic upgrade head
pytest
ruff check .
ruff format --check .
```

상세 구조는 [설계 문서](docs/design.md)에 정리되어 있습니다.
