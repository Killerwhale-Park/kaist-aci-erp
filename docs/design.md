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

중앙 원장 채널은 없습니다. 각 승인 규칙은 비공개 채널 하나를 지정하며, 학과별·항목별로 같은 채널을 공유하거나 서로 다른 채널을 선택할 수 있습니다.

봇은 `conversations.list`로 자신이 참여한 비공개 채널을 찾고 각 채널의 메시지 metadata를 조회합니다. 따라서 채널 ID를 환경변수에 넣지 않습니다.

채널에는 세 종류의 root message가 저장됩니다.

- `expense_record`: 신청 한 건의 현재 상태와 승인 버튼
- `work_request_record`: 구매 요청 또는 학생 정산 요청의 현재 상태와 완료 버튼
- `configuration_record`: 승인 규칙 또는 관리자 설정

신청 메시지의 thread에는 `expense_event_chunk`, 설정 메시지의 thread에는 `configuration_chunk`가 append-only로 추가됩니다. 큰 payload는 zlib 압축 후 여러 metadata message로 나눕니다. 모든 chunk가 있는 event만 유효합니다.

구매 요청과 정산 요청은 생성할 때 게시 채널을 선택합니다. 같은 채널을 공유해도 되고 별도 채널로 분리해도 됩니다. 생성과 완료 이력은 `work_request_event_chunk`로 해당 메시지 thread에 저장합니다. 정산 요청 생성은 현재 승인 규칙의 승인자와 시스템 관리자에게만 허용합니다.

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

예산 분류도 단계 수를 미리 알지 않습니다. 각 노드는 `parent_id`로 상위 노드를 가리키며, 실제 정산 가능한 leaf만 Expense Category와 증빙 설정에 연결됩니다.

```text
학과예산 / Department Budget
└─ 학사계발비 / Academic Development Fund
   └─ 비품비 / Supplies
```

Slack 모달은 선택한 노드의 자식만 다음 selector로 추가합니다. leaf에 도달하기 전에는 제출 버튼을 표시하지 않으므로 2~N단계 분류를 같은 UI와 엔진으로 처리합니다.

## Query and configuration

App Home 조회 시 root metadata를 먼저 필터링합니다. 신청자 또는 현재 승인자가 일치하는 메시지만 thread를 replay합니다.

승인 규칙은 선택한 승인 채널에 저장됩니다. 시스템 관리자 설정은 봇이 참여한 업무 채널에 복제되며 최신 timestamp의 설정을 사용합니다. 새 신청은 최신 규칙을 snapshot으로 저장하므로 이후 규칙 변경이 기존 신청을 바꾸지 않습니다.

## Retention

Slack 보존 정책이 기록의 내구성 한계입니다. 이 시스템은 공식 회계 원장이 아닙니다. 장기 보존이 필요하면 관련 채널을 정기적으로 내보내 Google Drive에 보관합니다.
