# Architecture

## 원칙

Slack은 사용자 인터페이스이고 PostgreSQL은 원장입니다. Google Drive는 증빙 파일 저장소입니다. 세 역할을 섞지 않습니다.

```text
Budget Configuration
        ↓ mapping
Evidence Form Configuration
        ↓ mapping
Approval Workflow Configuration
```

예산 트리는 2~N depth입니다. 최종 Budget Node가 독립적인 Expense Form과 Approval Workflow에 연결되며, 학과 범위 항목만 학과별 override를 허용합니다.

## 데이터 계층

주요 테이블은 다음과 같습니다.

- `expense_requests`: 현재 상태 projection과 Slack 표시 메시지 위치
- `expense_events`: 생성·승인·반려·수정·사후 증빙의 append-only 이력
- `work_requests`, `work_request_events`: 구매 요청과 정산 배정
- `role_assignments`: 전역 업무 Role과 Slack 사용자 ID
- `approval_routes`: 학과·재원 항목과 승인 채널 연결
- `system_settings`, `operating_channels`: 감사·경고·운영 채널
- `audit_events`: 설정 변경과 운영 경고

요청 상태 변경은 한 DB transaction 안에서 다음 순서로 처리합니다.

1. 요청 row lock
2. 기존 event replay
3. 도메인 전이 검증
4. 새 event append
5. 현재 상태 projection 갱신

버튼 값은 내부 요청 UUID입니다. Slack `message_ts`는 메시지를 갱신하기 위한 보조값일 뿐 식별자나 원장이 아닙니다. 메시지가 삭제되면 다음 갱신 시 새 메시지를 만들고 새 `message_ts`를 DB에 연결합니다.

## 승인 상태 전이

```text
IN_APPROVAL(step 1..N)
  ├─ approve → next step
  ├─ approve final → post evidence check → COMPLETED
  ├─ changes → CHANGES_REQUESTED → resubmit → same step
  └─ reject → REJECTED
```

엔진은 단계 수를 알지 못합니다. Workflow 설정의 step 목록을 순서대로 실행합니다. 각 요청은 제출 시점의 Workflow, 승인자, 증빙 요건 snapshot을 가지므로 이후 설정 변경이 기존 요청을 바꾸지 않습니다.

## Role과 채널

Role 정의와 capability는 코드 기반 업무 정책입니다. 사람 교체는 `role_assignments`만 변경합니다.

- `STUDENT_COORDINATOR`: 학생 담당자
- `PROFESSOR`: 교수, 정산 배정 가능
- `ADMIN_STAFF`: 행정팀, 정산 배정 가능
- `SYSTEM_ADMIN`: Role·채널·승인 route 관리

실제 권한은 전역 Role 보유자와 해당 운영 채널 멤버의 교집합입니다. 일반 신청자는 별도 allowlist 없이 운영 채널 멤버십으로 판정합니다.

## Slack 채널

- Audit: 설정 변경을 사람이 읽을 수 있게 표시
- Alerts: 처리 실패와 운영 경고 표시
- Operating: 신청·승인 메시지 표시

동일한 운영 채널을 여러 승인 route가 공유할 수 있습니다. 채널 분리는 접근 권한과 업무 맥락을 위한 것이며 저장소 분할이 아닙니다.

Slack 메시지는 보존 기간과 무관하게 삭제 가능한 projection입니다. DB의 event와 audit row가 공식 시스템 이력입니다.

## 배포와 마이그레이션

- 운영: Neon PostgreSQL의 pooled `DATABASE_URL`
- 스키마 변경: Alembic과 unpooled 연결 권장
- 로컬·테스트: SQLite + aiosqlite
- 증빙: DB에는 Google Drive HTTPS URL만 저장

애플리케이션 시작 시 자동 DDL은 실행하지 않습니다. 배포 전에 `alembic upgrade head`를 명시적으로 실행하여 concurrent serverless instance의 스키마 경쟁을 방지합니다.
