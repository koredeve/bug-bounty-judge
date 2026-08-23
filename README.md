BugBountyJudge — AI-arbitrated bug bounty program for GenLayer
==============================================================

BugBountyJudge puts a bug bounty program on-chain. The owner publishes the
program rules and a payout per severity tier (`critical`/`high`/`medium`/
`low`, in atto-scale units). Researchers submit reports with title,
description, and a proof-of-concept URL; AI validators arbitrate each report
against the rules and the live PoC page. Accepted reports automatically
credit the researcher an internal balance that is cashed out with
`withdraw` — funds never move directly to users.

Architecture
------------

- **User action**: owner calls `set_program(rules, critical_atto, high_atto,
  medium_atto, low_atto)` once; researchers call `submit_report(report_id,
  title, description, poc_url)`; anyone triggers `adjudicate(report_id)`;
  researchers drain credits via `withdraw`.
- **Evidence source**: the stored program `rules_text`, the report's title +
  description, and the live proof-of-concept page fetched from `poc_url`
  (first 3000 characters of the decoded body) at adjudication time.
- **Nondet call**: `adjudicate` runs `gl.vm.run_nondet_unsafe(leader_fn,
  validator_fn)`. The leader fetches the PoC page (4xx → `[EXTERNAL]`,
  5xx → `[TRANSIENT]`) and prompts the model for JSON
  `{"valid": true/false, "severity": "critical|high|medium|low",
  "reasoning": "..."}` with `response_format="json"`, then defensively parses
  it via `_parse_llm_json` with key aliases (`valid` / `is_valid`), string
  coercion (`"true"` / `"yes"` / `"1"`), and severity normalization
  (lowercase + strip). A severity outside the four tiers raises `[LLM_ERROR]`.
- **Equivalence principle**: exact equality on validity plus ±1 severity-rank
  tolerance. The validator independently reruns `leader_fn` and accepts only
  when the fresh `valid` boolean EQUALS the leader's AND the severity ranks
  (critical=3, high=2, medium=1, low=0) differ by at most 1 — adjacent-tier
  disagreement (e.g. leader says `high`, fresh rerun says `critical`) still
  passes, while anything further apart rejects the proposal. Unknown or
  missing ranks never agree. Leader failures go through the canonical
  `_handle_leader_error` rules: transient errors on both sides agree,
  deterministic errors must match exactly. Leader output is never trusted
  without this independent rerun comparison.
- **Settlement effect**: valid → status `accepted`, award = payout of the
  leader's severity tier, credited to the researcher; invalid → status
  `rejected`, no credit. Severity, reasoning, and award are persisted on the
  report and readable via `get_report`; credits are readable via `credit_of`
  and cashed out with `withdraw`.
- **Appeal path**: GenLayer Optimistic Democracy provides
  leader-proposes / validator-check natively, including an appeal window in
  which stakers can challenge a settled adjudication before it becomes final.

Quickstart
----------

Requires Python 3.14.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Lint the contract
genvm-lint check contracts/BugBountyJudge.py --json

# Run direct-mode tests (web + LLM mocked)
pytest tests/direct/ -v
```

Integration tests target StudioNet via `gltest.config.yaml`.

Interface
---------

| Method | Type | Notes |
| --- | --- | --- |
| `set_program(rules, critical_atto, high_atto, medium_atto, low_atto)` | write | Owner only; non-empty rules and strictly positive payouts; empty `rules_text` means no program. |
| `submit_report(report_id, title, description, poc_url)` | write | Requires a live program; unique id; non-empty title and description; sender becomes researcher. |
| `adjudicate(report_id)` | write | Only on a `submitted` report; fetches the PoC page, runs AI arbitration with valid-equality + ±1 severity-rank validation; accepted reports auto-credit the award. |
| `withdraw()` | write | Drains the caller's credit balance via an emit transfer. |
| `owner()` | view | Program owner address. |
| `get_rules()` | view | Current program rules text (empty = no program). |
| `get_payout(sev)` | view | Payout for a severity tier (0 if unset). |
| `get_report(report_id)` | view | Full report record; researcher exposed as a string. |
| `credit_of(who)` | view | Withdrawable balance of an address. |
| `total_reports()` | view | Number of reports submitted. |

StudioNet note: transactions on StudioNet are gasless — holding 0 GEN is fine.
