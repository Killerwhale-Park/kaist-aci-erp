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
- `applicant_profiles`: 정산 양식에서 재사용하는 학생/교수 구분과 학번/사번
- `request_contexts`: Slack 대화별 학과·기본 재원 입력값
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

`case_id`, `parent_request_id`, `source_work_request_id`가 구매 요청 → 정산 업무 → 정산 신청의 계보를 연결합니다. 인계 transaction은 선행 작업 완료 event와 후속 작업 생성 event를 함께 기록합니다.

정산 업무 생성 event에는 배정자가 확정한 Budget Node와 당시 경로 이름을 snapshot으로
저장합니다. 담당 학생은 이를 상속할 뿐 변경하지 않습니다. Expense Form은 독립된 mapping으로
해석하므로 재원 엔티티와 양식 엔티티를 합치지 않습니다.

## 승인 상태 전이

```text
IN_APPROVAL(step 1..N)
  ├─ approve → next step
  ├─ approve final → post evidence check → COMPLETED
  ├─ changes → CHANGES_REQUESTED → resubmit → same step
  └─ reject → REJECTED
```

엔진은 단계 수를 알지 못합니다. Workflow 설정의 step 목록을 순서대로 실행합니다. 각 요청은 제출 시점의 Workflow, 승인자, 증빙 요건 snapshot을 가지므로 이후 설정 변경이 기존 요청을 바꾸지 않습니다.

동일한 승인 체인을 정산 신청과 구매 요청이 공유합니다. 엔진은 엔티티 종류나 Role 이름을 모르며, workflow resolver가 Role·명시적 담당자·채널 멤버십을 실제 Slack 사용자 ID snapshot으로 변환합니다.

## 작업함과 인계

App Home은 역할별 전용 화면이 아니라 사용자와 엔티티의 관계를 공통 `WorkItem`으로 투영합니다.

- 내가 올린 처리 중 요청: 신청자·원 요청자·인계 요청자 관계
- 내가 처리할 요청: 현재 승인자·결제 담당자·정산 담당자 관계

후속 동작은 lifecycle adapter가 결정합니다.

```text
일반 완료 adapter: action complete → close
구매 adapter: approval 1..N → payment → settlement handoff
정산 업무 adapter: settlement assignment → expense request
정산 신청 adapter: approval 1..N → post-evidence/close
```

따라서 core 작업함과 승인 엔진에 교수·학생·행정팀 분기문을 추가하지 않습니다. 새 엔티티나 종료/인계 방식은 policy와 adapter를 추가해 확장합니다.

Home의 조회 조립은 application 계층의 `UserDashboard`가 담당하고 Slack 계층은 Block Kit
표현만 담당합니다. 같은 요청이 `내가 처리할 일`에 있으면 `진행 중인 요청`에는 중복 표시하지
않습니다.

`ApplicantProfile`은 신청자의 신분 정보만 보관합니다. 요청의 `department_id`는 회계·승인
승인 채널을 정하는 요청 문맥이므로 Profile과 합치지 않습니다. 이 분리는 단과대 예산처럼 신청자 소속 학과와
양식·승인 경로가 무관한 경우를 보장합니다.

## Role과 채널

Role 정의와 capability는 코드 기반 업무 정책입니다. 사람 교체는 `role_assignments`만 변경합니다.

- `STUDENT_COORDINATOR`: 학생 담당자
- `PROFESSOR`: 교수, 정산 배정 가능
- `ADMIN_STAFF`: 행정팀, 정산 배정 가능
- `SYSTEM_ADMIN`: Role·채널·승인 route 관리

역할 기반 승인자는 전역 Role 보유자와 승인 채널 멤버의 교집합입니다. 요청에서 명시적으로
지정된 담당자는 전역 Role로 검증하여 DM에서도 처리할 수 있습니다. 구매 신청자는
`Eligible Requester` 자격과 선택한 운영 대화 멤버십을 모두 충족해야 합니다.

## Slack 채널

- Audit: 설정 변경을 사람이 읽을 수 있게 표시
- Alerts: 처리 실패와 운영 경고 표시
- Operating: 신청·승인 메시지 표시

동일한 운영 채널을 여러 승인 route가 공유할 수 있습니다. 채널 분리는 접근 권한과 업무 맥락을 위한 것이며 저장소 분할이 아닙니다.

`RequestContext`는 채널 또는 앱 DM에 붙는 반복 입력 기본값입니다. 승인 route나 저장 위치가
아니며, `/expense setup`으로 저장한 학과·재원을 `/expense`, `/expense purchase`,
`/expense settlement`가 재사용합니다.

Slack 메시지는 보존 기간과 무관하게 삭제 가능한 projection입니다. DB의 event와 audit row가 공식 시스템 이력입니다.

## 배포와 마이그레이션

- 운영: Neon PostgreSQL의 pooled `DATABASE_URL`
- 스키마 변경: Alembic과 unpooled 연결 권장
- 로컬·테스트: SQLite + aiosqlite
- 증빙: DB에는 Google Drive HTTPS URL만 저장

애플리케이션 시작 시 자동 DDL은 실행하지 않습니다. 배포 전에 `alembic upgrade head`를 명시적으로 실행하여 concurrent serverless instance의 스키마 경쟁을 방지합니다.

빈번한 uptime 확인은 DB를 조회하지 않는 `/health`를 사용합니다. `/ready`는 migration table을 조회하므로 배포 검증과 장애 진단 때만 사용합니다.
