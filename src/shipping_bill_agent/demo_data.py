from __future__ import annotations

import io
from email.message import EmailMessage
from pathlib import Path

from openpyxl import Workbook


def build_charge_workbook(
    amount: float = 40,
    charge_name: str = "H/D Fee",
    currency: str = "USD",
    payment_term: str = "PP",
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Charges"
    sheet.append(["Charge Item", "Amount", "Currency", "Payment Term"])
    sheet.append([charge_name, amount, currency, payment_term])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def build_sample_email(amount: float = 40) -> bytes:
    message = EmailMessage()
    message["Subject"] = "Updated debit note DN-202609-001"
    message["From"] = "Origin Agent <ops@origin.example>"
    message["To"] = "Destination Agent <billing@destination.example>"
    message["Date"] = "Mon, 20 Oct 2026 10:00:00 +0800"
    message.set_content(
        "\n".join(
            [
                "Please find the updated debit note attached.",
                "",
                "Document Type: Debit Note",
                "Reference No.: DN-202609-001",
                "Date: 2026-09-20",
                "Issuer: Destination Agent",
                "Recipient: Origin Agent",
            ]
        )
    )
    message.add_attachment(
        build_charge_workbook(amount=amount),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="updated_bill.xlsx",
    )
    return message.as_bytes()


def write_sample_email(path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(build_sample_email())
    return destination
