# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"

STATUS_SUBMITTED = "submitted"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"

SEVERITY_RANKS = {"critical": 3, "high": 2, "medium": 1, "low": 0}
POC_SNIPPET_CHARS = 3000


def _parse_llm_json(text) -> dict:
	import re
	if isinstance(text, dict):
		return text
	s = str(text)
	first = s.find("{")
	last = s.rfind("}")
	if first == -1 or last <= first:
		raise gl.vm.UserError(f"{ERROR_LLM} no JSON object found in LLM output")
	s = s[first : last + 1]
	s = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", s)
	try:
		parsed = json.loads(s)
	except Exception:
		raise gl.vm.UserError(f"{ERROR_LLM} malformed JSON from LLM")
	if not isinstance(parsed, dict):
		raise gl.vm.UserError(f"{ERROR_LLM} non-dict JSON from LLM")
	return parsed


def _coerce_bool(raw) -> bool:
	if isinstance(raw, bool):
		return raw
	s = str(raw).strip().lower()
	if s in ("true", "1", "yes"):
		return True
	if s in ("false", "0", "no"):
		return False
	raise gl.vm.UserError(f"{ERROR_LLM} non-boolean valid field in LLM output")


def _handle_leader_error(leaders_res, leader_fn) -> bool:
	leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
	try:
		leader_fn()
		return False
	except gl.vm.UserError as e:
		validator_msg = e.message if hasattr(e, "message") else str(e)
		if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
			return validator_msg == leader_msg
		if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
			return True
		return False
	except Exception:
		return False


@allow_storage
@dataclass
class Report:
	researcher: Address
	title: str
	description: str
	poc_url: str
	status: str
	severity: str
	award_atto: u256
	reasoning: str


@gl.evm.contract_interface
class _Recipient:
	class View:
		pass

	class Write:
		pass


class BugBountyJudge(gl.Contract):
	owner_addr: Address
	rules_text: str
	payouts: TreeMap[str, u256]
	reports: TreeMap[str, Report]
	report_ids: DynArray[str]
	credits: TreeMap[Address, u256]
	pool: u256
	approved_poc_domains: TreeMap[str, str]

	def __init__(self) -> None:
		self.rules_text = ""
		sender = gl.message.sender_address
		self.owner_addr = sender if isinstance(sender, Address) else Address(sender)

	def _require_owner(self) -> None:
		if gl.message.sender_address != self.owner_addr:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only owner")

	def _get_report(self, report_id: str) -> Report:
		report = self.reports.get(report_id)
		if report is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unknown report id")
		return report

	def _credit(self, who: Address, amount: u256) -> None:
		self.credits[who] = self.credits.get(who, u256(0)) + u256(amount)

	@gl.public.view
	def owner(self) -> Address:
		return self.owner_addr

	@gl.public.view
	def get_rules(self) -> str:
		return self.rules_text

	@gl.public.view
	def get_payout(self, sev: str) -> u256:
		return self.payouts.get(str(sev), u256(0))

	@gl.public.write
	def approve_poc_domain(self, url_prefix: str, name: str) -> None:
		self._require_owner()
		if not str(url_prefix).startswith("https://"):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} PoC domain prefix must be an https URL")
		self.approved_poc_domains[str(url_prefix)] = str(name)

	@gl.public.write
	def revoke_poc_domain(self, url_prefix: str) -> None:
		self._require_owner()
		if str(url_prefix) not in self.approved_poc_domains:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} PoC domain prefix is not approved")
		del self.approved_poc_domains[str(url_prefix)]

	@gl.public.view
	def get_poc_domains(self) -> dict:
		prefixes = []
		for prefix in self.approved_poc_domains.keys():
			prefixes.append(str(prefix))
		return {"prefixes": prefixes}

	@gl.public.view
	def get_pool(self) -> u256:
		return self.pool

	@gl.public.write.payable
	def fund_pool(self) -> None:
		if gl.message.value == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Send GEN with the call")
		self.pool = self.pool + gl.message.value

	@gl.public.write
	def set_program(
		self,
		rules: str,
		critical_atto: u256,
		high_atto: u256,
		medium_atto: u256,
		low_atto: u256,
	) -> None:
		self._require_owner()
		if len(rules) == 0:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Rules text must not be empty")
		for label, amount in (
			("critical", critical_atto),
			("high", high_atto),
			("medium", medium_atto),
			("low", low_atto),
		):
			if u256(amount) == u256(0):
				raise gl.vm.UserError(
					f"{ERROR_EXPECTED} Payout for '{label}' must be greater than zero"
				)
		self.rules_text = rules
		self.payouts["critical"] = u256(critical_atto)
		self.payouts["high"] = u256(high_atto)
		self.payouts["medium"] = u256(medium_atto)
		self.payouts["low"] = u256(low_atto)

	@gl.public.write
	def submit_report(
		self, report_id: str, title: str, description: str, poc_url: str
	) -> None:
		if len(self.rules_text) == 0:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} No bug bounty program defined")
		clean_id = str(report_id).strip()
		clean_title = str(title).strip()
		clean_desc = str(description).strip()
		if len(clean_id) == 0 or len(clean_title) == 0 or len(clean_desc) == 0:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Title and description must not be empty"
			)
		if clean_id in self.reports:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Report id already exists")
		url = str(poc_url).strip()
		approved = False
		for prefix in self.approved_poc_domains.keys():
			if url.startswith(str(prefix)):
				approved = True
				break
		if not approved:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} PoC URL domain is not owner-approved")
		self.reports[clean_id] = Report(
			researcher=gl.message.sender_address,
			title=clean_title,
			description=clean_desc,
			poc_url=url,
			status=STATUS_SUBMITTED,
			severity="",
			award_atto=u256(0),
			reasoning="",
		)
		self.report_ids.append(clean_id)

	@gl.public.write
	def adjudicate(self, report_id: str) -> None:
		report = self._get_report(report_id)
		if report.status != STATUS_SUBMITTED:
			raise gl.vm.UserError(
				f"{ERROR_EXPECTED} Report has already been adjudicated"
			)
		rules_text = str(self.rules_text)
		title = str(report.title)
		description = str(report.description)
		poc_url = str(report.poc_url)
		ranks = SEVERITY_RANKS

		def leader_fn() -> dict:
			page = gl.nondet.web.get(poc_url)
			status = int(page.status)
			if 400 <= status < 500:
				raise gl.vm.UserError(
					f"{ERROR_EXTERNAL} proof-of-concept URL returned HTTP {status}"
				)
			if status >= 500:
				raise gl.vm.UserError(
					f"{ERROR_TRANSIENT} proof-of-concept URL returned HTTP {status}"
				)
			raw_body = page.body
			if raw_body is None:
				raise gl.vm.UserError(
					f"{ERROR_TRANSIENT} empty response body from proof-of-concept URL"
				)
			text = bytes(raw_body).decode("utf-8")[:POC_SNIPPET_CHARS]
			out = gl.nondet.exec_prompt(
				f"You arbitrate a bug bounty report.\n"
				f"RULES: <rules>{rules_text}</rules>\n"
				f"REPORT: {title} — {description}\n"
				f"PROOF OF CONCEPT PAGE: <poc>{text}</poc>\n"
				f'Reply JSON {{"valid": true/false, "severity": "critical|high|medium|low", "reasoning": "..."}}',
				response_format="json",
			)
			parsed = _parse_llm_json(out)
			raw_valid = None
			for key in ("valid", "is_valid"):
				if key in parsed:
					raw_valid = parsed[key]
					break
			if raw_valid is None:
				raise gl.vm.UserError(f"{ERROR_LLM} missing valid field in LLM output")
			is_valid = _coerce_bool(raw_valid)
			severity = str(parsed.get("severity", "")).strip().lower()
			if severity not in ranks:
				raise gl.vm.UserError(
					f"{ERROR_LLM} unknown severity '{severity}' in LLM output"
				)
			reasoning = str(parsed.get("reasoning", ""))
			return {"valid": bool(is_valid), "severity": severity, "reasoning": reasoning}

		def validator_fn(leaders_res: gl.vm.Result) -> bool:
			if not isinstance(leaders_res, gl.vm.Return):
				return _handle_leader_error(leaders_res, leader_fn)
			leader_data = leaders_res.calldata
			fresh = leader_fn()
			leader_sev = str(leader_data.get("severity", "")).strip().lower()
			fresh_sev = str(fresh.get("severity", "")).strip().lower()
			return bool(leader_data.get("valid")) == bool(fresh.get("valid")) and leader_sev == fresh_sev

		result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

		report.severity = str(result["severity"])
		report.reasoning = str(result["reasoning"])
		if bool(result["valid"]):
			award = self.payouts.get(report.severity, u256(0))
			if self.pool < u256(award):
				raise gl.vm.UserError(
					f"{ERROR_EXPECTED} Insufficient bounty pool to fund this award"
				)
			self.pool = self.pool - u256(award)
			report.status = STATUS_ACCEPTED
			report.award_atto = u256(award)
			self._credit(report.researcher, u256(award))
		else:
			report.status = STATUS_REJECTED
			report.award_atto = u256(0)

	@gl.public.write
	def withdraw(self) -> None:
		who = gl.message.sender_address
		amount = self.credits.get(who, u256(0))
		if amount == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Nothing to withdraw")
		self.credits[who] = u256(0)
		_Recipient(who).emit_transfer(value=u256(amount))

	@gl.public.view
	def get_report(self, report_id: str) -> dict:
		report = self._get_report(report_id)
		return {
			"researcher": str(report.researcher),
			"title": report.title,
			"description": report.description,
			"poc_url": report.poc_url,
			"status": report.status,
			"severity": report.severity,
			"award_atto": report.award_atto,
			"reasoning": report.reasoning,
		}

	@gl.public.view
	def credit_of(self, who: Address) -> u256:
		return self.credits.get(Address(who), u256(0))

	@gl.public.view
	def total_reports(self) -> u256:
		return u256(len(self.report_ids))
