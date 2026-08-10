from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests


@dataclass
class EmailPayload:
    subject: str
    html: str
    sender: str
    recipient: str


class EmailProvider(ABC):
    @abstractmethod
    def send_html(self, payload: EmailPayload) -> None:
        raise NotImplementedError


class ResendEmailProvider(EmailProvider):
    api_url = "https://api.resend.com/emails"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def send_html(self, payload: EmailPayload) -> None:
        response = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": payload.sender,
                "to": [payload.recipient],
                "subject": payload.subject,
                "html": payload.html,
            },
            timeout=20,
        )
        if response.status_code >= 300:
            raise RuntimeError(f"Email send failed: {response.status_code} {response.text}")
