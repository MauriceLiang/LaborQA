from app.services.interaction_responses import (
    INTERACTION_REPLY_VERSION,
    RESPONSES,
    render_interaction_response,
)
from app.services.interaction_router import InteractionKind


def test_every_interaction_kind_has_a_stable_nonempty_response() -> None:
    assert INTERACTION_REPLY_VERSION == "interaction_v1"
    assert set(RESPONSES) == set(InteractionKind)
    assert all(render_interaction_response(kind).strip() for kind in InteractionKind)


def test_identity_and_disclaimer_do_not_claim_professional_status() -> None:
    identity = render_interaction_response(InteractionKind.IDENTITY)
    disclaimer = render_interaction_response(InteractionKind.DISCLAIMER)

    assert "LaborQA" in identity
    assert "不替代" in identity
    assert "我不是律师" in disclaimer
    assert "正式意见" in disclaimer
