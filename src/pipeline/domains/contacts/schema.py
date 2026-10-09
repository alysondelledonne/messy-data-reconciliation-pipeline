"""Target schema for the contacts pack."""

from pydantic import BaseModel, Field


class Contact(BaseModel):
    """A person at an external organization."""

    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = Field(default=None, description="E.164, e.g. +13125550143.")
    company: str | None = Field(default=None, description="Organization name as written.")
    job_title: str | None = None
    city: str | None = None
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2 code.")
