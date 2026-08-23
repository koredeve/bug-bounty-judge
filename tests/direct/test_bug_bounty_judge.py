import json

CRITICAL = 50 * 10**18
HIGH = 20 * 10**18
MEDIUM = 8 * 10**18
LOW = 2 * 10**18

RULES_TEXT = "Only in-scope web assets. Duplicate submissions are not rewarded."

TITLE = "Stored XSS in comment field"
DESCRIPTION = "Persistent script injection via the comment form executes on page load."
POC_URL = "https://poc.example.com/xss-demo"

WEB_REGEX = r"https://poc\.example\.com/.*"
LLM_REGEX = r"You arbitrate a bug bounty report"

PAGE_BODY = "<html><body><script>alert(document.cookie)</script></body></html>"


def _deploy(direct_vm, direct_deploy, owner):
    direct_vm.sender = owner
    return direct_deploy("contracts/BugBountyJudge.py")


def _set_program(direct_vm, contract, owner):
    direct_vm.sender = owner
    contract.set_program(RULES_TEXT, CRITICAL, HIGH, MEDIUM, LOW)


def _submit(direct_vm, contract, researcher, report_id="report-1"):
    with direct_vm.prank(researcher):
        contract.submit_report(report_id, TITLE, DESCRIPTION, POC_URL)


def _mock_happy_path(direct_vm):
    direct_vm.mock_web(WEB_REGEX, {"status": 200, "body": PAGE_BODY})


def test_set_program_stores_rules_and_payouts(direct_vm, direct_deploy, direct_alice):
    """The owner defines program rules and per-severity payouts visible via views."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)

    assert len(str(contract.owner())) > 0
    assert contract.get_rules() == RULES_TEXT
    assert contract.get_payout("critical") == CRITICAL
    assert contract.get_payout("high") == HIGH
    assert contract.get_payout("medium") == MEDIUM
    assert contract.get_payout("low") == LOW


def test_set_program_reverts_for_non_owner(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Only the owner may configure the bounty program."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Only owner"):
            contract.set_program(RULES_TEXT, CRITICAL, HIGH, MEDIUM, LOW)

    assert contract.get_rules() == ""


def test_set_program_rejects_empty_rules_or_zero_payout(
    direct_vm, direct_deploy, direct_alice
):
    """Program setup requires non-empty rules and strictly positive payouts."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("[EXPECTED]"):
        contract.set_program("", CRITICAL, HIGH, MEDIUM, LOW)
    with direct_vm.expect_revert("[EXPECTED]"):
        contract.set_program(RULES_TEXT, 0, HIGH, MEDIUM, LOW)
    with direct_vm.expect_revert("[EXPECTED]"):
        contract.set_program(RULES_TEXT, CRITICAL, HIGH, MEDIUM, 0)

    assert contract.get_rules() == ""


def test_submit_report_stores_state_and_rejects_duplicate_id(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """A researcher files a report against the live program and state is visible."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)
    _submit(direct_vm, contract, direct_bob)

    stored = contract.get_report("report-1")
    assert len(stored["researcher"]) > 0
    assert stored["title"] == TITLE
    assert stored["description"] == DESCRIPTION
    assert stored["poc_url"] == POC_URL
    assert stored["status"] == "submitted"
    assert stored["severity"] == ""
    assert stored["award_atto"] == 0
    assert contract.total_reports() == 1

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Report id already exists"):
            contract.submit_report("report-1", TITLE, DESCRIPTION, POC_URL)
    assert contract.total_reports() == 1


def test_submit_report_requires_program_and_nonempty_fields(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Reports need an active program and non-empty title and description."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)

    with direct_vm.expect_revert("No bug bounty program defined"):
        contract.submit_report("report-early", TITLE, DESCRIPTION, POC_URL)

    _set_program(direct_vm, contract, direct_alice)

    with direct_vm.expect_revert("Title and description must not be empty"):
        contract.submit_report("report-blank", "", DESCRIPTION, POC_URL)
    with direct_vm.expect_revert("Title and description must not be empty"):
        contract.submit_report("report-blank", TITLE, "", POC_URL)

    assert contract.total_reports() == 0


def test_adjudicate_accepts_valid_high_and_credits_researcher(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """AI validates the PoC as a high-severity bug: the researcher is credited the high payout."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)
    _submit(direct_vm, contract, direct_bob)

    _mock_happy_path(direct_vm)
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps(
            {
                "valid": True,
                "severity": "high",
                "reasoning": "PoC page demonstrates stored XSS within scope",
            }
        ),
    )

    contract.adjudicate("report-1")

    judged = contract.get_report("report-1")
    assert judged["status"] == "accepted"
    assert judged["severity"] == "high"
    assert judged["award_atto"] == HIGH
    assert judged["reasoning"] == "PoC page demonstrates stored XSS within scope"
    assert contract.credit_of(direct_bob) == HIGH


def test_adjudicate_invalid_report_is_rejected_without_credit(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """AI rejects the report via the is_valid alias key: no credit is issued."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)
    _submit(direct_vm, contract, direct_bob)

    _mock_happy_path(direct_vm)
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps(
            {
                "is_valid": False,
                "severity": "low",
                "reasoning": "asset out of scope per program rules",
            }
        ),
    )

    contract.adjudicate("report-1")

    judged = contract.get_report("report-1")
    assert judged["status"] == "rejected"
    assert judged["severity"] == "low"
    assert judged["award_atto"] == 0
    assert contract.credit_of(direct_bob) == 0


def test_double_adjudicate_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    """An already-adjudicated report cannot be adjudicated again."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)
    _submit(direct_vm, contract, direct_bob)

    _mock_happy_path(direct_vm)
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"valid": True, "severity": "medium", "reasoning": "in scope"}),
    )
    contract.adjudicate("report-1")

    with direct_vm.expect_revert("already been adjudicated"):
        contract.adjudicate("report-1")
    assert contract.credit_of(direct_bob) == MEDIUM


def test_unknown_id_reverts_and_views_are_safe(
    direct_vm, direct_deploy, direct_alice
):
    """Unknown report ids revert; unknown addresses simply have zero credit."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)

    with direct_vm.expect_revert("Unknown report id"):
        contract.adjudicate("missing-report")
    with direct_vm.expect_revert("Unknown report id"):
        contract.get_report("missing-report")

    assert contract.credit_of(direct_alice) == 0
    assert contract.total_reports() == 0


def test_llm_severity_outside_tiers_raises_llm_error(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """An unrecognized severity label surfaces as [LLM_ERROR] and leaves the report open."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)
    _submit(direct_vm, contract, direct_bob)

    _mock_happy_path(direct_vm)
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"valid": True, "severity": "severe", "reasoning": "very bad"}),
    )

    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.adjudicate("report-1")

    assert contract.get_report("report-1")["status"] == "submitted"
    assert contract.credit_of(direct_bob) == 0


def test_unreachable_poc_page_raises_external_error(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """A dead proof-of-concept URL aborts adjudication with an [EXTERNAL] error."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)
    _submit(direct_vm, contract, direct_bob)

    direct_vm.mock_web(WEB_REGEX, {"status": 404, "body": "not found"})

    with direct_vm.expect_revert("[EXTERNAL]"):
        contract.adjudicate("report-1")

    assert contract.get_report("report-1")["status"] == "submitted"


def test_withdraw_drains_credit_balance(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """An accepted researcher drains their credit once; others have nothing to withdraw."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _set_program(direct_vm, contract, direct_alice)
    _submit(direct_vm, contract, direct_bob)

    _mock_happy_path(direct_vm)
    direct_vm.mock_llm(
        LLM_REGEX,
        json.dumps({"valid": True, "severity": "critical", "reasoning": "RCE in scope"}),
    )
    contract.adjudicate("report-1")
    assert contract.credit_of(direct_bob) == CRITICAL

    with direct_vm.expect_revert("Nothing to withdraw"):
        contract.withdraw()

    with direct_vm.prank(direct_bob):
        contract.withdraw()
    assert contract.credit_of(direct_bob) == 0
