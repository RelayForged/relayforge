"""
Integrity sub-policy shared by every strategy.

Whether deliveries are signed is a deployment setting, not something that
varies with endpoint health, so all strategies (A, B, C and the Tier 3
fallback) derive it the same way from one explicit boolean. Signing then
survives a fall back from AEDE to a baseline strategy.
"""
from relay.kernel.policies.delivery_policy import IntegrityPolicy

SIGNATURE_TTL_SECONDS = 300


def integrity_policy(hmac_required: bool) -> IntegrityPolicy:
    if not hmac_required:
        return IntegrityPolicy(hmac_required=False)
    return IntegrityPolicy(
        hmac_required=True,
        signature_ttl_seconds=SIGNATURE_TTL_SECONDS,
        replay_protection=True,
    )
