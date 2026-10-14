"""Guide: how to use each page, what the terms mean, and FAQs."""

from collections import Counter

import streamlit as st
from sqlalchemy import select

from pipeline.review import queries, ui
from pipeline.store.models import GoldenRecord

# Every reason a record can be flagged: what it means and what to do about it.
REASONS: dict[str, tuple[str, str]] = {
    # reconciliation
    "source_conflict": (
        "Sources disagree on an important field (an amount, payment status, invoice date, "
        "currency, a last name). The message says which value won, by which rule, and "
        "what lost.",
        "Look at the losing source on the left. Approve if the winner is right; otherwise "
        "correct the field.",
    ),
    "not_in_ap_register": (
        "A vendor invoice (PDF) with no matching row in the AP register: possibly an "
        "unrecorded bill.",
        "Check whether it should be in the register. Approve to accept it as is.",
    ),
    "no_document": (
        "In the AP register but no vendor document was found. Informational.",
        "Nothing, unless the document should exist.",
    ),
    "duplicate_document": (
        "The same invoice arrived as more than one document (re-sent, re-scanned).",
        "Make sure it isn't paid twice, then approve.",
    ),
    "duplicate_row": (
        "The same row appears more than once in one file. Informational.",
        "Nothing; the copies are already merged.",
    ),
    "entity_match_pending": (
        "The organization's name is close to another one, and nobody has decided yet "
        "whether they're the same.",
        "Decide the pair on Review queue → Name matches, then re-run reconcile.",
    ),
    "ambiguous_match": (
        "This record could belong to two different people or invoices with the same name "
        "or details (namesakes), so it was kept on its own.",
        "Use the sources to decide; correct the record or approve it as a separate one.",
    ),
    "review_orphaned": (
        "Someone reviewed this record before, but the sources behind it have changed "
        "since (for example after re-extraction), so the old review wasn't applied.",
        "Check the record again. 'Re-apply the earlier corrections' copies the old "
        "corrections over.",
    ),
    # extraction checks
    "low_confidence": (
        "The model wasn't sure about a value, or the source is ambiguous (a 04/03/24 "
        "date, a phone number without a country code).",
        "Compare the value with the source on the left; approve or correct.",
    ),
    "required_missing": (
        "A required field isn't stated in any source.",
        "Fill it in with a correction if you know it; otherwise approve as incomplete.",
    ),
    "missing_year": (
        "A date was written without a year, so the year was guessed.",
        "Check the year and correct if needed.",
    ),
    "invalid_value": (
        "A value couldn't be read as the right type (a date that isn't a date).",
        "Correct the field.",
    ),
    "not_in_source": (
        "The evidence the model quoted wasn't found in the source text.",
        "Treat the value with suspicion; check it against the source.",
    ),
    "extraction_degraded": (
        "The bigger model failed on this document, so the result is the smaller model's.",
        "Check the values against the source.",
    ),
    "unidentifiable": (
        "Not enough to identify the record (no vendor or invoice number; no name, email "
        "or phone). Such source records are rejected.",
        "Usually nothing: it's noise in a source.",
    ),
    # invoice rules
    "totals_add_up": (
        "Subtotal plus tax doesn't equal the total.",
        "Find the source with the right numbers; correct the wrong field.",
    ),
    "line_items_sum": (
        "The line items don't add up to the subtotal.",
        "Check the line items in the source.",
    ),
    "line_amount": ("A line's quantity times unit price doesn't equal its amount.", "Check it."),
    "tax_rate": ("Tax is outside 0-30% of the subtotal.", "Check the tax and subtotal."),
    "non_positive_total": ("The total is zero or negative (a credit note?).", "Check it."),
    "invoice_date_range": ("The invoice date is implausible.", "Correct the date."),
    "due_before_invoice": ("The due date is before the invoice date.", "Check both dates."),
    "due_too_late": ("The due date is over a year after the invoice date.", "Check it."),
    # contact rules
    "email_format": ("The email address isn't valid.", "Correct it."),
    "phone_format": (
        "The phone number isn't in international form (+country code), often a national UK number.",
        "Correct it to +44…, or approve as is.",
    ),
    "country_format": ("The country isn't a two-letter ISO code.", "Correct it (US, GB…)."),
    "name_shape": ("A name contains characters a name shouldn't.", "Correct it."),
    "name_abbreviated": ("A name is only an initial (D.).", "Fill in the full name if known."),
    "name_case": ("A name is in all caps. Informational.", "Nothing."),
}

WHY = {
    "all sources agree": "Every source that states this field says the same thing.",
    "single source": "Only one source states it.",
    "most sources agree": "The value most sources state (identity fields).",
    "most sources agree, arithmetic consistent": (
        "Amounts: the value most sources state; ties go to sources whose numbers add up."
    ),
    "system of record": "Payment status comes from the AP register when it has the invoice.",
    "most recent source": (
        "Contact details that change (email, phone, title, company) come from the newest "
        "dated source; the CRM export counts as current."
    ),
    "most complete source": "Line items come from the source that lists the most.",
    "corrected by human:<name>": "A reviewer set this value; the machine value is kept.",
    "(written as …)": (
        "The value was tidied after choosing it: a national phone number given its "
        "country code, a PO number without its label."
    ),
}

LINKS = {
    "seed": "the first record of the group",
    "num / po": "same invoice or PO number from the same vendor",
    "email / phone": "same email address or phone number, and the names agree",
    "soft": "same vendor and total within a few days, or same company and name",
    "name_only": "no company named; joined the only person with that name",
}

st.title("Guide")
with ui.session() as s:
    pack, _ = ui.sidebar(queries.packs(s))
    counts: Counter[str] = Counter()
    if pack:
        for g in s.scalars(select(GoldenRecord).where(GoldenRecord.pack == pack)):
            counts.update({f["rule"] for f in g.findings if f["severity"] != "info"})

st.markdown(
    """
This app is where a person checks the pipeline's work. The pipeline reads messy files
(spreadsheets, PDFs, scans, emails, notes, logs), extracts records with Claude, checks
them, and merges the records that describe the same real thing into one **golden
record**. Every golden record remains traceable to the source records it came from.
You approve what's right, correct what's wrong and settle the close calls the machine
couldn't.

All data here is synthetic.
"""
)

st.header("Getting started")
st.markdown(
    """
1. **Pick a domain pack** in the sidebar (for example *invoices* or *contacts*). Each
   pack is one kind of record with its own rules.
2. **Type your name** in the sidebar. It's saved with every approval, correction and
   decision, and buttons stay disabled until you enter it.
3. Work through **Review queue**, then use **Exceptions** for the overall picture.

The data comes from the pipeline's command line: `ingest`, `extract`, `validate`,
then `reconcile`. If a page is empty, those steps haven't been run for that pack yet.
"""
)

st.header("Overview")
st.markdown(
    """
The overall state at a glance.

- **Golden records:** merged records, one per real invoice or person.
- **Auto-approved:** passed every check; nobody needs to look.
- **Needs review:** at least one reason to look (see the glossary below).
- **Approved by a person:** reviewed and approved in this app.
- **Model cost:** total spent on Claude calls for extraction and name matching.
- **Source records by format:** how each *source* record (before merging) fared in
  validation, per file format. A source can need review while its golden record doesn't,
  because another source fills the gap.
"""
)

st.header("Review queue")
st.subheader("Records tab")
st.markdown(
    """
Filter by **status** and **reason**, then pick a **record**.

**Left side: the source.** Each linked source record is listed with why it was joined.
Click one to see it:
- a PDF or scan as page images (the text layer, with the evidence lines highlighted,
  is under the image);
- an email with its headers and body;
- spreadsheet or CSV rows around the record, with ▶ marking its row;
- notes and documents with line numbers, and highlighted lines containing the
  evidence.

*What this source says* lists the values extracted from that source, the model's
confidence and the text it quoted as evidence.

**Right side: the golden record.** Yellow boxes are the reasons it needs review. The
table shows each field's chosen value, **why** it won, the confidence, and the **other
values** sources gave (with their files).

**Actions**
- **Approve** marks the golden record approved. **Undo approval** reverses it.
- **Correct a field:** choose the field, enter the right value, and save. Use
  `YYYY-MM-DD` for dates and plain numbers for amounts (`1234.50`). Leave it empty to
  clear the field, and use JSON for line items. The value is checked against the schema
  before it's saved, and the record is re-checked against the rules right away.
- **Note:** optional, saved with your action in the audit trail.
"""
)
st.subheader("Name matches tab")
st.markdown(
    """
Pairs of organization names that are close, such as *Bluewater Holdings* and
*Bluewater Holding Group*. Neither the rules nor the model could say confidently whether
they're the same organization. Each side shows its spellings and how often they appear,
plus the model's reasoning.

- **Same organization** merges them on the next reconcile; **Different** keeps them
  apart for good. Your decision is never overwritten by the model.
- **Re-run reconcile now** applies decisions immediately. It makes no model calls and
  costs nothing.
"""
)

st.header("Exceptions")
st.markdown(
    """
Lists across all golden records of the pack, each downloadable as CSV:
- **Duplicates:** records that arrived more than once (re-sent PDFs, repeated rows).
- **Unmatched:** invoices missing from the AP register, register entries with no
  document, and records found in only one source.
- **Conflicts:** every field where sources disagreed: what was chosen, by which rule,
  and what lost.
- **Rule failures:** golden records failing a pack rule or an extraction check.
- **Audit trail:** every human action, newest first: who, when, what changed, and the
  note.
"""
)

st.header("Glossary")
st.subheader("Statuses")
ui.table(
    [
        {"status": "auto_approved", "meaning": "Passed every check; no review needed."},
        {"status": "needs_review", "meaning": "At least one reason for a person to look."},
        {"status": "rejected", "meaning": "Can't be identified (no usable fields)."},
        {"status": "approved", "meaning": "A person reviewed and approved it."},
    ]
)
st.subheader("Review reasons")
st.caption(
    "Counts are golden records in the selected pack with that reason."
    if pack
    else "Pick a pack to see counts."
)
ui.table(
    [
        {
            "reason": rule,
            "records": counts.get(rule, 0),
            "what it means": meaning,
            "what to do": todo,
        }
        for rule, (meaning, todo) in REASONS.items()
    ]
)
st.subheader("Why a value won")
ui.table([{"why": k, "meaning": v} for k, v in WHY.items()])
st.subheader("Why sources were linked")
ui.table([{"link": k, "meaning": v} for k, v in LINKS.items()])

st.header("FAQ")
FAQ = [
    (
        "Do my approvals and corrections survive when the pipeline runs again?",
        "Yes. Reconcile rebuilds every golden record, then puts reviews back. Each record "
        "is identified by the sources it was built from, not by its number, so record "
        "numbers can change while your work stays attached.",
    ),
    (
        "A record I corrected is back in the queue as 'review_orphaned'. Why?",
        "The sources behind it changed (for example, a re-extraction linked another "
        "document to it), so the pipeline won't silently apply your old correction to "
        "what may now be a different record. Check it, then use 'Re-apply the earlier "
        "corrections' if they still hold.",
    ),
    (
        "Does approving a golden record also approve its source records?",
        "No. Source records keep the machine's verdicts. Keeping the two apart lets the "
        "evals measure how well the machine's review queue works.",
    ),
    (
        "How do I undo a correction?",
        "Correct the field again. The audit trail (Exceptions → Audit trail) shows the old "
        "value of every change. Undo approval reverses an approval.",
    ),
    (
        "Does using the app call Claude or cost money?",
        "No. The app never calls the model. Even 'Re-run reconcile now' reuses stored "
        "decisions and leaves new close calls for a person.",
    ),
    (
        "Why is a record flagged when all its sources agree?",
        "Agreement isn't the only check: a value can still be low-confidence, break a rule "
        "(totals that don't add up), or be missing a required field. The yellow boxes say "
        "which.",
    ),
    (
        "What's the difference between a source record and a golden record?",
        "A source record is what one file says (one CSV row, one PDF). A golden record "
        "merges every source record about the same real invoice or person into one, with "
        "the best value for each field.",
    ),
    (
        "The preview says 'Source file not found'.",
        "The app can't find the original files. Set FILES_ROOT in .env to the corpus "
        "folder (for example data/generated) and restart the app. With Docker, the corpus "
        "is mounted automatically.",
    ),
    (
        "Who can see my name?",
        "Anyone using this app or the database: it's stored with each action in the audit "
        "trail. There's no login in this demo.",
    ),
    (
        "Why did a name pair I decided disappear from Name matches?",
        "Decided pairs leave the list. The decision takes effect on the next reconcile, "
        "which you can run with 'Re-run reconcile now'.",
    ),
    (
        "Is any of this real data?",
        "No. Every company, person and document is generated by the project's seeded "
        "synthetic data generator.",
    ),
]
for q, a in FAQ:
    with st.expander(q):
        st.write(a)
