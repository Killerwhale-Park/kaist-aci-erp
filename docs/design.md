# Slack message-ledger design

## 1. Scope

This system supports student expense pre-settlement before records enter an official administrative process. One Slack App serves four departments. Slack is the only user interface. Evidence files remain in Google Drive; the application records HTTPS URLs only.

Unconfirmed accounting policy is configuration, not code. Initial evidence candidates are optional until a policy owner explicitly marks them required.

## 2. Architecture

```text
Slack Modal / App Home / Buttons / DM
                  |
          FastAPI + Async Bolt
                  |
         Slack handlers/renderers
                  |
   Pure Python workflow reducer + validation
                  |
     Private Slack message ledger
```

The backend is stateless. It has no SQL database, local persistence, migration process, or user-facing website.

## 3. Ledger channels

### Central ledger channel

A single private channel stores machine-readable records. Only the bot and system administrators need membership.

Root messages use one of these metadata types:

- `expense_record`: one root per expense; contains a compact materialized summary
- `configuration_record`: approval-rule or system-admin configuration version

Thread replies contain compressed, chunked metadata:

- `expense_event_chunk`
- `configuration_chunk`

The visible text is a human-readable audit description. Structured payloads are zlib-compressed JSON encoded as URL-safe base64. Chunks have a record ID, index, and count. A record is accepted only when every chunk is present.

### Department approval channels

The system posts a rendered mirror of the request into the configured department private channel. Approvers interact with its buttons. The mirror is a projection, not the source of truth; the central ledger thread is authoritative.

## 4. Expense event model

The first complete event must be `REQUEST_CREATED`. It contains:

- request/reference ID
- applicant identity and student ID
- department, budget, and category snapshot
- amount, vendor, date, purpose
- Drive folder/evidence URLs
- evidence requirement snapshot
- ordered approval workflow and approver snapshot
- submission time

Subsequent append-only events are:

- `APPROVAL_STEP_APPROVED`
- `CHANGES_REQUESTED`
- `REQUEST_REJECTED`
- `REQUEST_RESUBMITTED`
- `POST_EVIDENCE_SUBMITTED`
- `MIRROR_LINKED`

No event is edited or deleted by application code. The root summary and visible approval message may be updated because they are caches/projections.

## 5. State transition reducer

```text
REQUEST_CREATED
      |
      v
IN_APPROVAL(step 1)
  | approve              | changes              | reject
  v                      v                      v
next step / final   CHANGES_REQUESTED        REJECTED
                         |
                      resubmit
                         |
                         v
                 same step IN_APPROVAL

final approval
  | required POST missing       | complete
  v                             v
APPROVED_PENDING_POST_EVIDENCE  COMPLETED
```

The reducer does not know the number of approval steps. It advances through the ordered snapshot until no step remains.

Each step uses the `ANY` policy: any configured approver may complete it. Slack buttons and user selectors are not authorization boundaries; the actor Slack ID in the signed interaction is checked against the current snapshot.

## 6. Concurrency

Slack messages do not provide database-style compare-and-swap transactions. The design therefore uses deterministic event replay:

1. Events are ordered by Slack message timestamp.
2. The first action valid for the current state is applied.
3. A stale or conflicting later action remains in the audit thread but is ignored by the reducer.
4. The root summary and approval mirror are refreshed from the replayed state.

This makes simultaneous approvals deterministic without an external lock service.

## 7. Query model

The bot does not use `search.messages`, because that method requires a user token. It calls `conversations.history` on the known ledger channel with `include_all_metadata=true`.

The compact root summary supports filtering without replaying every request:

- applicant ID for App Home “My Requests”
- current approver IDs for “Pending Approvals”
- department/category/status/reference fields

Only matching requests require `conversations.replies` and full replay.

## 8. Runtime configuration

Static catalog data lives in `app/config/`:

- four departments
- budget programs
- expense categories
- initial optional evidence candidates
- sample step names

Approval channel, ordered steps, selected approvers, and system administrators are versioned configuration records in the ledger channel. A new request snapshots the latest complete rule. Later rule changes do not alter existing requests.

## 9. Failure behavior

- Missing event chunks: ignore the incomplete event.
- Stale action: retain for audit, ignore during state reduction.
- Approval mirror update failure: ledger remains authoritative and can regenerate the mirror.
- Ledger root cache update failure: a direct request load replays the thread and repairs the projection on the next mutation.
- Slack API outage: no local fallback writes are attempted.

## 10. Retention boundary

Slack message retention is the primary durability limit. This ledger is appropriate for low-volume student self-government workflow coordination, not as the final statutory accounting archive. The official administrative system remains the final record. If longer student-organization retention is required, export the private ledger periodically to Google Drive.
