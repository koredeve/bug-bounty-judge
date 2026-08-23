from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded


def test_deploy_smoke():
    factory = get_contract_factory("BugBountyJudge")
    contract = factory.deploy(args=[])
    result = contract.get_rules(args=[]).call()
    assert result is not None
