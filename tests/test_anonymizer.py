from app.anonymizer import PseudonymState, anonymize_text
from app.models import Span


def test_replace():
    out, applied = anonymize_text(
        "mail a@b.com!",
        [Span(5, 12, "EMAIL_ADDRESS", 1.0)],
        mode="replace",
        salt="",
        state=PseudonymState(),
    )
    assert out == "mail <EMAIL_ADDRESS>!"
    assert applied[0].token == "<EMAIL_ADDRESS>"


def test_pseudonym_same_value_same_token():
    st = PseudonymState()
    t = "A A B"
    out, _ = anonymize_text(
        t,
        [
            Span(0, 1, "VN_NAME", 0.6),
            Span(2, 3, "VN_NAME", 0.6),
            Span(4, 5, "VN_NAME", 0.6),
        ],
        mode="pseudonym",
        salt="",
        state=st,
    )
    assert out == "<VN_NAME_1> <VN_NAME_1> <VN_NAME_2>"


def test_hash_stable_with_salt_and_hides_value():
    a, _ = anonymize_text(
        "0901234567",
        [Span(0, 10, "VN_PHONE_NUMBER", 0.85)],
        mode="hash",
        salt="s3",
        state=PseudonymState(),
    )
    b, _ = anonymize_text(
        "0901234567",
        [Span(0, 10, "VN_PHONE_NUMBER", 0.85)],
        mode="hash",
        salt="s3",
        state=PseudonymState(),
    )
    assert a == b and "0901234567" not in a and a.startswith("<VN_PHONE_NUMBER_")


def test_offsets_applied_right_to_left():
    out, _ = anonymize_text(
        "ab cd",
        [Span(0, 2, "VN_NAME", 0.6), Span(3, 5, "VN_NAME", 0.6)],
        mode="replace",
        salt="",
        state=PseudonymState(),
    )
    assert out == "<VN_NAME> <VN_NAME>"
