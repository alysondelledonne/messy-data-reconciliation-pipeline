"""Target schema for the invoices pack."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Currency = Literal["USD", "EUR", "CAD"]
PaymentStatus = Literal["paid", "unpaid"]


class LineItem(BaseModel):
    description: str
    quantity: Decimal = Field(description="Units billed; may be fractional (hours).")
    unit_price: Decimal | None = Field(
        default=None, description="Price per unit; some layouts omit it."
    )
    amount: Decimal = Field(description="Line total as printed.")


class Invoice(BaseModel):
    """A vendor invoice as issued. Money is in the invoice's own currency."""

    invoice_number: str | None = Field(
        description='Vendor\'s invoice identifier ("Inv #", "Invoice No.", "Ref", ...).'
    )
    vendor_name: str = Field(description="Vendor name as written on the source.")
    invoice_date: date
    due_date: date | None = None
    currency: Currency
    po_number: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal
    payment_status: PaymentStatus | None = None
