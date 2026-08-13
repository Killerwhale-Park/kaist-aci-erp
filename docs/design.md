# Slack message-ledger design

## Scope

이 시스템은 공식 행정 절차로 전달하기 전 학생 정산 요청과 내부 승인을 처리합니다. Slack이 유일한 사용자 화면이며, 증빙 파일은 Google Drive에 두고 HTTPS URL만 기록합니다.

## Architecture

```text
Slack Modal / App Home / Buttons / DM
                  |
          FastAPI + Async Bolt
                  |
      workflow reducer + validation
                  |
   private Slack approval channels
```

백엔드는 상태를 보관하지 않습니다. SQL 데이터베이스, 로컬 영속 파일, 별도 웹 화면은 없습니다.

## Channel model

채널은 읽기·쓰기 특성에 따라 분리합니다.

- System Config Channel: 최신 `system_configuration_snapshot`만 저장
- Audit Channel: 설정 변경 event를 append-only로 저장하며 요청 경로에서 읽지 않음
- Alerts Channel: 60초 단위로 중복 억제된 운영 경고를 저장하며 요청 경로에서 읽지 않음
- Operating Channel: `expense_record`, `work_request_record`와 각 상태 변경 thread를 저장

`SLACK_SYSTEM_CHANNEL_ID`는 설정 원장을 찾기 위한 유일한 배포 locator입니다. 운영·Audit·Alerts 채널 ID는 System Config snapshot에 저장합니다. 설정을 모든 운영 채널에 복제하지 않습니다.

System Config는 설정 변경마다 전체 snapshot을 새 root message로 기록합니다. 일반적인 크기는 root metadata에 압축 inline하고, 커지면 thread chunk로 분리합니다. 최초 cold read는 System Config history 한 번이며 warm instance는 TTL cache를 사용합니다. 구버전 `configuration_record`만 발견되면 전체 채널을 한 번 탐색해 최신 Role과 route를 새 snapshot으로 이관합니다.

신청 메시지의 thread에는 `expense_event_chunk`, 업무 요청 thread에는 `work_request_event_chunk`가 append-only로 추가됩니다. 큰 payload는 zlib 압축 후 여러 metadata message로 나눕니다. 모든 chunk가 있는 event만 유효합니다.

구매 요청과 정산 요청은 생성할 때 게시 채널을 선택합니다. 같은 채널을 공유해도 되고 별도 채널로 분리해도 됩니다. 생성과 완료 이력은 `work_request_event_chunk`로 해당 메시지 thread에 저장합니다. 정산 요청 생성은 교수·행정팀·시스템 관리자 Role에게만 허용합니다.

## Expense events

첫 event는 `REQUEST_CREATED`이며 다음 snapshot을 포함합니다.

- 신청자, 학과, 예산, 카테고리
- 금액, 거래처, 날짜, 목적
- Drive 증빙 URL
- 증빙 요구사항
- 순서가 있는 승인 단계와 승인자

후속 event:

- `APPROVAL_STEP_APPROVED`
- `CHANGES_REQUESTED`
- `REQUEST_REJECTED`
- `REQUEST_RESUBMITTED`
- `POST_EVIDENCE_SUBMITTED`

상태는 Slack timestamp 순서로 event를 replay해 계산합니다. 동시에 들어온 충돌 action은 처음 유효한 event만 반영하고 나머지는 감사 이력으로 남깁니다.

## Workflow

```text
IN_APPROVAL(step 1..N)
  | approve       | changes          | reject
  v               v                  v
next/final   CHANGES_REQUESTED     REJECTED
                    |
                 resubmit
                    v
             same approval step

final approval -> required POST evidence -> COMPLETED
```

엔진은 단계 수를 미리 알지 않습니다. 각 단계에서는 설정된 승인자 중 한 명이 승인할 수 있습니다. 권한은 Slack interaction의 actor ID와 신청 당시 workflow snapshot을 비교해 검사합니다.

## Budget configuration tree

예산 분류도 단계 수를 미리 알지 않습니다. 각 `BudgetNode`는 `parent_id`로 상위 노드를 가리킵니다. 증빙·입력 규칙은 별도 `ExpenseForm` 엔티티이며 `BudgetFormMapping`이 실제 정산 가능한 leaf와 양식을 연결합니다.

```text
학과예산 / Department Budget
└─ 학사계발비 / Academic Development Fund
   └─ 비품비 / Supplies
```

Slack 모달은 선택한 노드의 자식만 다음 selector로 추가합니다. leaf에 도달하기 전에 계속을 누르면 최종 항목을 선택하라는 검증 오류를 표시하므로 2~N단계 분류를 같은 UI와 엔진으로 처리합니다.

`BudgetNode`와 `ExpenseForm`은 독립적인 축입니다. 같은 이름의 항목도 재원 경로별로 서로 다른 node ID를 가지므로 각기 다른 양식에 연결할 수 있습니다. 반대로 하나의 양식을 여러 재원 leaf가 재사용할 수도 있습니다. `ExpenseCategory`는 저장되는 별도 설정 엔티티가 아니라 이 mapping을 Slack workflow가 소비할 수 있게 풀어낸 projection입니다.

`ApprovalWorkflowDefinition`도 양식과 독립적인 정책 축입니다. `BudgetWorkflowMapping`이 재원 node와 workflow를 연결하며 상위 node의 mapping은 하위 leaf에 상속됩니다. 따라서 학사계발비 아래에 새 지출 항목이 추가되어도 같은 workflow를 자동으로 사용하며, 필요하면 더 깊은 node에서 override할 수 있습니다.

`BudgetFormScope.DEPARTMENT`인 재원은 `department_id`별 mapping override를 허용하고, override가 없으면 공통 default mapping을 사용합니다. `BudgetFormScope.GLOBAL`인 단과대 예산 같은 재원은 학과별 override를 금지합니다.

## Access role configuration

접근 권한은 승인 workflow와 독립적인 전역 Role 축입니다. Role 정의는 ID·표시명·capability를 가진 configuration입니다.

- `STUDENT_COORDINATOR`: 학생 담당자
- `PROFESSOR`: 교수, 정산 요청 지정 가능
- `ADMIN_STAFF`: 행정팀, 정산 요청 지정 가능
- `SYSTEM_ADMIN`: System Channel·Role·승인 route 관리

일반 학생은 별도 Role allowlist에 등록하지 않으며 운영 채널 멤버십으로 신청 자격을 얻습니다. 학과 범위를 Role assignment에 중복 저장하지 않습니다. 운영 채널이 업무 Context를 나타내며 신청자는 해당 운영 채널 멤버여야 합니다. Workflow step의 실제 승인자는 `전역 Role 보유자 ∩ 승인 채널 멤버`로 계산해 신청 시점 snapshot에 고정합니다. 담당자가 교체되어도 기존 신청의 승인자는 바뀌지 않습니다.

현재 학사계발비 workflow는 `학생 담당자 확인 → 담당 교수 승인·검수 → 행정팀 검토`입니다. 엔진·Slack 모달·권한 검사는 구체적인 Role 목록이나 단계 수를 알지 못하며 configuration을 순회합니다. 학과장·학생회장 승인이 추가되어도 Role 정의와 workflow configuration만 변경하고, 사람 교체는 Slack Role assignment만 바꿉니다.

## Query and configuration

승인 버튼과 DM·App Home 버튼의 value에는 `channel_id + message_ts + record_id` locator를 넣습니다. 개별 신청 조회는 정확한 메시지 한 건을 직접 가져오며 전체 채널을 검색하지 않습니다. 구버전 UUID-only 버튼은 등록된 운영 채널을 조회하는 fallback으로 호환합니다.

App Home 목록은 System Config에 등록된 운영 채널만 조회하고 root metadata에서 신청자 또는 현재 승인자가 일치하는 메시지만 thread를 replay합니다. `conversations.list`로 임의의 채널을 발견하거나 Audit·Alerts 채널을 읽지 않습니다.

## Retention

Slack 보존 정책이 기록의 내구성 한계입니다. 이 시스템은 공식 회계 원장이 아닙니다. 장기 보존이 필요하면 관련 채널을 정기적으로 내보내 Google Drive에 보관합니다.
