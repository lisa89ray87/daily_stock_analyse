from src.daily_stock_analyse.email_provider import EmailPayload


def test_email_payload_fields():
    payload = EmailPayload(
        subject="sub",
        html="<h1>x</h1>",
        sender="from@example.com",
        recipient="to@example.com",
    )
    assert payload.subject == "sub"
    assert payload.sender.endswith("@example.com")
