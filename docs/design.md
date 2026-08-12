# Student Expense Support ERP MVP Design

## 1. Scope and requirements

This application is a pre-settlement and internal approval system. It does not replace the university's official ERP, make payments, upload files, interpret accounting policy, or integrate with the official ERP.

The MVP supports one available budget program, `student_support`, and four configurable expense categories: supplies, lodging, airfare, and conference registration. AI Global Explorer and Resource Support appear in App Home as coming-soon programs only.

One Slack app and one database serve four configurable departments. Requesters only see their own requests. Approval messages are posted to department-specific private channels, and applicants do not need access to those channels.

Unconfirmed accounting rules are configuration, not code. The initial evidence candidates are seeded as optional until policy owners explicitly mark them required. Workflow step names are seeded, while approvers and channel IDs are configured by a system administrator in Slack.

## 2. Slack platform constraints

- Commands, actions, and modal submissions must be acknowledged within three seconds.
- A modal supports up to 100 blocks and a stack of up to three views.
- A modal can be updated from a submission with `response_action=update`; this is used to render category-specific evidence fields after the first form.
- A modal `trigger_id` expires after three seconds, so modal opening happens immediately after acknowledgement.
- App Home is published for a specific Slack user when `app_home_opened` is received.
- The bot must be a member of each private approval channel before it can post there.
- Slack user selections and visible buttons are not authorization controls. The backend validates the actor from the signed Slack interaction payload.
- Approval messages include top-level fallback text because screen readers and notifications may not consume Block Kit content.

## 3. Architecture

```text
Slack Events / Commands / Interactions
                  |
        FastAPI + Async Slack Bolt
                  |
        Slack handlers and renderers
                  |
   ExpenseService / ApprovalService
                  |
 ApprovalEngine + ApprovalAuthorizer
                  |
      SQLAlchemy repositories
                  |
 PostgreSQL (production) / SQLite (local)
```

The application is a modular monolith. Slack modules translate Slack payloads and render Block Kit. Application services own transactions and use cases. The approval engine owns state transitions. The authorizer owns permission checks. SQLAlchemy models persist configuration, immutable request snapshots, workflow instances, and audit events.

FastAPI exposes the Slack endpoint and operational health endpoints. Business services do not depend on Slack clients, allowing a future administrative API or dashboard to reuse the same behavior.

## 4. Configuration and persistence model

### Definition entities

- `Department`: bilingual names and private approval channel ID.
- `BudgetProgram`: bilingual names and availability state.
- `ExpenseCategory`: belongs to a budget program.
- `EvidenceRequirementDefinition`: category evidence key, timing, requirement level, waiver capability, descriptions, and display order.
- `ApprovalWorkflowDefinition`: named, versioned workflow configuration.
- `ApprovalStepDefinition`: ordered step name and approval policy. The number of rows is unrestricted.
- `ApprovalStepDefinitionApprover`: zero or more eligible Slack users for a definition step.
- `ApprovalRule`: resolves department + budget + category to one active workflow.

### Identity and authorization entities

- `UserProfile`: canonical Slack user ID, display name, optional applicant data, one MVP role, and department scope. The design can later normalize role assignments without changing request snapshots.

### Request instance entities

- `ExpenseRequest`: canonical applicant Slack ID and submission fields, request status, current step order, Slack message coordinates, and JSON snapshots of the selected definitions.
- `EvidenceSubmission`: one snapshot row per requirement, with names, timing, requirement level, URL, note, and submitted timestamp.
- `ApprovalStep`: one snapshot row per workflow step, including ordered policy and action data.
- `ApprovalStepApprover`: immutable eligible Slack users copied into a request step.
- `ApprovalActionLog`: append-only audit event with actor, optional step, timestamp, and JSON metadata.

Definition identifiers and instance identifiers remain separate. Foreign keys to definitions are retained for traceability, but request behavior reads instance rows rather than live definitions.

## 5. Evidence model

Evidence requirements use two independent dimensions:

- Timing: `PRE` or `POST`.
- Requirement: `REQUIRED` or `OPTIONAL`.

On request creation, every configured requirement becomes an `EvidenceSubmission` row. Missing optional evidence never blocks submission or completion. Missing required PRE evidence blocks initial submission and resubmission. Missing required POST evidence moves an approved request to `APPROVED_PENDING_POST_EVIDENCE` and blocks completion.

The folder URL and individual evidence URLs are stored as HTTPS references. Files are not uploaded to Slack or the application server. Non-Google Drive HTTPS links are accepted with a warning. Waiver fields are represented in definitions but waiver workflow is out of MVP scope.

## 6. N-step approval workflow

At submission, the rule resolver selects a workflow by department, budget, and category. Its ordered step definitions are copied into `ApprovalStep` rows. The engine only asks for the current and next row; it has no special handling for one, two, three, or more steps.

```text
First step: PENDING
Later steps: WAITING

approve current step
  -> mark APPROVED
  -> next WAITING step becomes PENDING
  -> if no next step, evaluate required POST evidence

request changes
  -> current step becomes CHANGES_REQUESTED
  -> request becomes CHANGES_REQUESTED

resubmit
  -> same step becomes PENDING
  -> request becomes IN_APPROVAL

reject
  -> current step becomes REJECTED
  -> request becomes REJECTED
```

## 7. Approval authorization

The actor is always `body.user.id` from a signature-verified Slack request. A request can be approved, rejected, or returned only when all of these conditions hold:

1. The request is in `IN_APPROVAL`.
2. The target step is the request's current `PENDING` step.
3. The actor ID is in the current step's snapshotted eligible approver set.

Channel membership and button visibility are irrelevant to this decision. System administrators manage configuration but receive no implicit approval override.

The MVP implements `ANY`: when a step has multiple eligible users, one matching user can complete it. A definition with no eligible user can be saved as incomplete but cannot be resolved for a new request. Saving a change creates a new workflow version; existing request snapshots remain unchanged.

## 8. Request state transitions

```text
DRAFT
  -> IN_APPROVAL                       submit with required PRE evidence

IN_APPROVAL
  -> IN_APPROVAL                       approve non-final step
  -> COMPLETED                         approve final step, no required POST missing
  -> APPROVED_PENDING_POST_EVIDENCE    approve final step, required POST missing
  -> CHANGES_REQUESTED                 current reviewer requests changes
  -> REJECTED                          current reviewer rejects

CHANGES_REQUESTED
  -> IN_APPROVAL                       applicant resubmits; same step resumes

APPROVED_PENDING_POST_EVIDENCE
  -> APPROVED_PENDING_POST_EVIDENCE    some required POST evidence remains
  -> COMPLETED                         all required POST evidence is submitted
```

`SUBMITTED` and `APPROVED` remain domain vocabulary for future asynchronous handoffs. The MVP performs workflow initialization and final evidence evaluation in the submission/approval transaction, so durable state advances directly to `IN_APPROVAL`, `APPROVED_PENDING_POST_EVIDENCE`, or `COMPLETED`.

## 9. Slack UI flow

### Requester

```text
App Home or /expense
  -> context modal: department, applicant type, student ID, budget, category
  -> response_action update
  -> details modal: amount, vendor, payment date, purpose, folder, PRE evidence
  -> request created and applicant profile refreshed
  -> confirmation DM and department approval message
```

App Home shows only requests whose applicant Slack ID matches the viewer. A request returned for changes is edited in a pre-populated modal. A request awaiting POST evidence exposes a dedicated evidence modal.

### Approver

The department channel message shows request data, evidence links, the complete progress chain, and the current reviewer. Approve acts immediately. Request Changes and Reject open a reason modal. Every action re-checks permission server-side, updates the persisted instance and audit log, refreshes the channel message, and notifies the applicant. The next specific reviewer receives a DM.

### Administrator

App Home adds pending approvals and a system administration section for `SYSTEM_ADMIN`. Its Block Kit flow edits the department private channel and a category's ordered N-step rule, supports multiple Slack users per step, and manages the system-admin set. The backend validates the admin role, private-channel status, bot membership, and selected approver membership before creating a new workflow version. The environment variable for bootstrap administrators is used only when the database has no system administrator.

## 10. Directory structure

```text
app/
  approvals/     engine, authorization, resolver, service
  config/        settings and seed definitions
  db/            base, models, session, seed
  expenses/      schemas, evidence, service
  i18n/          all user-facing strings
  slack/         app wiring, handlers, modals, messages, home
  users/         profile service
  main.py
alembic/
tests/
docs/
```

## 11. Deferred scope

No detailed rules are implemented for research funds, student council accounting, Global Explorer, Resource Support, transportation, Google Drive OAuth/upload, official ERP integration, payments, or a separate web UI.
