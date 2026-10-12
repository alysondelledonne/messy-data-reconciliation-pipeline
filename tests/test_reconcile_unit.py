"""Reconciliation logic without a database: names, entity clustering, record linking and
survivorship."""

from collections import Counter
from datetime import date
from typing import Any

import pytest

from pipeline.domains.contacts import reconcile as contacts
from pipeline.domains.invoices import reconcile as invoices
from pipeline.reconcile.engine import _date_in, link_records
from pipeline.reconcile.entities import EntityCluster, NameGroup, PairVerdict, judge_pair, resolve
from pipeline.reconcile.names import lookalike_guard, normalize, similarity
from pipeline.reconcile.survive import choose
from pipeline.reconcile.types import Candidate

_ids = iter(range(1, 10_000))


def cand(values: dict[str, Any], source: str = "a.csv", fmt: str = "csv", **kw: Any) -> Candidate:
    return Candidate(
        id=kw.pop("id", next(_ids)), values=values, meta=kw.pop("meta", {}), source=source,
        format=fmt, kind=kw.pop("kind", "row"), when=kw.pop("when", None), **kw,
    )  # fmt: skip


# --- names -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Lakeshore HVAC Services Inc.", "LAKESHORE HVAC SVCS"),
        ("Kestrel Lock & Key LLC", "Kestrel Lock and Key"),
        ("Müller Gebäudetechnik GmbH", "Muller Gebaudetechnik"),
        ("Copperline Electrical Contractors Inc.", "Copperline Electrical Contr Inc."),
        ("Bluewater Janitorial Supplies LLC", "Bluewater Janitorial Supplies L"),
    ],
)
def test_spellings_of_one_name_normalize_alike(a: str, b: str) -> None:
    assert normalize(a) == normalize(b)
    assert similarity(a, b) == 100


def test_typos_score_high_without_a_guard() -> None:
    assert similarity("Kestrel Lock & Key", "Kestrrel Lock & Key") >= 92
    assert not lookalike_guard("Kestrel Lock & Key", "Kestrrel Lock & Key")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Summit Elevator Services", "Summit Electrical Services"),
        ("Bluewater Janitorial Services", "Bluewater Janitorial Supply"),
        ("Northgate Realty Partners", "Northgate Realty Advisors"),
        ("Crestview Commercial Group", "Crestview Realty"),
    ],
)
def test_lookalikes_are_guarded(a: str, b: str) -> None:
    assert lookalike_guard(a, b)


def test_full_date_headers_are_read() -> None:
    assert _date_in("Date: February 11, 2025") == date(2025, 2, 11)
    assert _date_in("Last updated May 2024") == date(2024, 5, 1)
    assert _date_in("Project Kickoff Memo") is None


# --- entities ----------------------------------------------------------------------------


def group(name: str, n: int = 1, same: set[str] | None = None, family: set[str] | None = None,
          docs: int = 0) -> NameGroup:  # fmt: skip
    return NameGroup(normalize(name), Counter({name: n}), same or set(), family or set(), docs)


def test_judge_pair() -> None:
    a = group("Bluewater Holdings", family={"prefix:BWH"})
    b = group("Bluewater Holding Group", family={"prefix:BHG"})
    # similar names but no evidence in common: the model or a human decides
    assert judge_pair(a, b, 92, 80).decision == "gray"
    c = group("Bluewatter Holdings", family={"prefix:BWH"})
    assert judge_pair(a, c, 92, 80).decision == "merge"
    shared = group("Bluewater Hold", same={"num:BWH1"})  # cut off: scores below 92
    a.same_keys.add("num:BWH1")
    assert judge_pair(a, shared, 92, 80).decision == "merge"  # an invoice number in common
    assert judge_pair(a, group("Northgate Realty"), 92, 80).decision == "separate"


def test_resolve_never_chains_across_a_gray_pair() -> None:
    # "Summit Elec" sits between two lookalikes; merging both ways would join them.
    groups = {
        g.norm: g
        for g in (
            group("Summit Elevator Services", 5),
            group("Summit Electrical Services", 5),
            group("Summit Elec Services", 1),
        )
    }
    clusters, verdicts = resolve(groups, 92, 80, lambda v, a, b: "pending")
    joined = [c for c in clusters if len(c.groups) > 1]
    for c in joined:
        norms = {g.norm for g in c.groups}
        assert not {"summit elevator services", "summit electrical services"} <= norms
    assert any(v.decision == "gray" for v in verdicts)


def test_resolve_uses_the_decision_for_gray_pairs() -> None:
    a, b = group("Bluewater Holdings", 3), group("Bluewater Holding Group", 2)
    a.family_keys, b.family_keys = {"prefix:BWH"}, {"prefix:BHG"}
    groups = {a.norm: a, b.norm: b}
    calls: list[PairVerdict] = []

    def decide(v: PairVerdict, ga: NameGroup, gb: NameGroup) -> str:
        calls.append(v)
        return "merge"

    clusters, _ = resolve(groups, 92, 80, decide)
    assert len(calls) == 1 and len(clusters) == 1
    clusters, _ = resolve(groups, 92, 80, lambda *_: "separate")
    assert len(clusters) == 2


def test_canonical_name_prefers_the_full_well_formed_spelling() -> None:
    cluster = EntityCluster([
        NameGroup("lakeshore hvac services", Counter({
            "LAKESHORE HVAC SERVICES": 9, "Lakeshore HVAC Svcs Inc.": 4,
            "Lakeshore HVAC Services Inc.": 2, "Lakeshore HVAC Services": 3,
        })),
        NameGroup("lakeshore hvac servcies", Counter({"Lakeshore HVAC Servcies Inc.": 1})),
    ])  # fmt: skip
    assert cluster.canonical == "Lakeshore HVAC Services Inc."


# --- record linking ----------------------------------------------------------------------


def person(first: str, last: str, entity: int | None = 1, **values: Any) -> Candidate:
    c = cand({"first_name": first, "last_name": last, **values})
    c.entity = entity
    return c


def test_contacts_link_on_email_typos_and_nicknames_but_not_namesakes() -> None:
    crm = person("Jonathan", "Moreau", email="jonathanm@meridian.example", phone="+13035550145")
    form = person("Jon", "Moreau", email="jonathanm@meridain.example")  # domain typo
    memo = person("Jonathan", "Moreau", phone="+13035550154")  # an old number
    other = person("Casey", "Nguyen", email="casey@x.example", phone="+13035550154")
    clusters = link_records(contacts.SPEC, [crm, form, memo, other]).clusters
    by = {c.id: {x.id for x in cl} for cl in clusters for c in cl}
    assert by[crm.id] == {crm.id, form.id, memo.id}
    assert by[other.id] == {other.id}  # a shared phone number doesn't merge two people


def test_a_sparse_mention_between_namesakes_stays_alone() -> None:
    a = person("Sam", "Lee", email="slee@acme.example")
    b = person("Samuel", "Lee", email="slee2@acme.example")  # a namesake at the same company
    vague = person("Sam", "Lee")
    linking = link_records(contacts.SPEC, [a, b, vague])
    assert len(linking.clusters) == 3
    assert vague.id in linking.ambiguous


def test_a_mention_without_a_company_joins_the_one_person_with_that_name() -> None:
    crm = person("Nadia", "O'Brien", email="nobrien@bw.example")
    note = person("Nadia", "O'Brien", entity=None, phone="+19195550176")
    assert len(link_records(contacts.SPEC, [crm, note]).clusters) == 1
    elsewhere = person("Nadia", "O'Brien", entity=2, email="nadia@other.example")
    linking = link_records(contacts.SPEC, [crm, note, elsewhere])
    assert len(linking.clusters) == 3 and note.id in linking.ambiguous


def invoice(**values: Any) -> Candidate:
    c = cand(values, source=values.pop("source", "ap_register.csv"))
    c.entity = 1
    return c


def test_invoices_link_on_number_and_on_vendor_total_date() -> None:
    pdf = invoice(invoice_number="LSC-3177", total="100.00", invoice_date="2024-01-02",
                  source="pdf/a.pdf")  # fmt: skip
    reg = invoice(invoice_number="LSC3177", total="100.00", invoice_date="2024-01-02")
    unnumbered = invoice(total="100.00", invoice_date="2024-01-04")
    other = invoice(invoice_number="LSC-3178", total="100.00", invoice_date="2024-01-03")
    linking = link_records(invoices.SPEC, [pdf, reg, unnumbered])
    assert [len(c) for c in linking.clusters] == [3]
    # the unnumbered row now fits two different invoices: it stays alone, flagged
    linking = link_records(invoices.SPEC, [pdf, reg, unnumbered, other])
    assert sorted(len(c) for c in linking.clusters) == [1, 1, 2]
    assert linking.ambiguous == {unnumbered.id}


# --- survivorship ------------------------------------------------------------------------


def test_amounts_follow_the_majority_and_arithmetic() -> None:
    good = cand({"total": "5862.43"}, source="register.csv")
    pdf = cand({"total": "5962.43"}, source="x.pdf", fmt="pdf", findings=("totals_add_up",))
    proc = cand({"total": "5862.43"}, source="procurement.csv")
    c = choose("total", [good, pdf, proc], invoices.SPEC)
    assert c.value == "5862.43" and c.alternatives[0]["value"] == "5962.43"
    # a tie: the arithmetically consistent source wins
    c = choose("total", [good, pdf], invoices.SPEC)
    assert c.value == "5862.43"


def test_payment_status_comes_from_the_system_of_record() -> None:
    pdf = cand({"payment_status": "unpaid"}, source="pdf/x.pdf", fmt="pdf")
    aging = cand({"payment_status": "unpaid"}, source="ap_aging.xlsx", fmt="xlsx")
    reg = cand({"payment_status": "paid"}, source="invoices/ap_register.csv")
    c = choose("payment_status", [pdf, aging, reg], invoices.SPEC)
    assert c.value == "paid" and c.rule == "system of record"


def test_contact_details_come_from_the_most_recent_source() -> None:
    old = cand({"job_title": "Analyst"}, source="memo.docx", fmt="docx", when=date(2023, 1, 1))
    new = cand({"job_title": "Director"}, source="note.md", fmt="md", when=date(2024, 6, 1))
    crm = cand({"job_title": "Director"}, source="crm.csv")  # undated, current snapshot
    assert choose("job_title", [old, new], contacts.SPEC).value == "Director"
    older_note = cand({"job_title": "Lead"}, source="n.md", fmt="md", when=date(2022, 1, 1))
    assert choose("job_title", [older_note, crm], contacts.SPEC).value == "Director"


def test_variants_are_grouped_and_shown_in_their_best_form() -> None:
    a = cand({"phone": "0117 4960 320"}, source="x.csv")
    b = cand({"phone": "+441174960320"}, source="y.eml", fmt="eml")
    c = choose("phone", [a, b], contacts.SPEC)
    assert c.value == "+441174960320" and not c.alternatives and c.variants == ["0117 4960 320"]
    n1 = cand({"first_name": "Mike"})
    n2 = cand({"first_name": "Mike"})
    n3 = cand({"first_name": "Michael"})
    assert choose("first_name", [n1, n2, n3], contacts.SPEC).value == "Michael"
    i1, i2 = cand({"invoice_number": "CM01966"}), cand({"invoice_number": "CM-01966"})
    assert choose("invoice_number", [i1, i2], invoices.SPEC).value == "CM-01966"


def test_national_phone_gets_the_country_code_of_the_record() -> None:
    assert contacts.finalize({"phone": "0117 4960 320", "country": "GB"}) == {
        "phone": "+441174960320"
    }
    assert contacts.finalize({"phone": "0117 4960 320", "country": None}) == {}
    assert contacts.finalize({"phone": "+13035550145", "country": "US"}) == {}


def test_email_typo_is_not_a_namesake_address() -> None:
    assert contacts.email_typo("oashworth@crestview.example", "oashworht@crestview.example")
    assert contacts.email_typo("a@kettlecreek.example", "a@keettlecreek.example")
    assert not contacts.email_typo("slee@acme.example", "slee2@acme.example")


def test_cluster_findings_for_invoices() -> None:
    pdf = cand({}, source="pdf/a.pdf", fmt="pdf")
    rules = {f.rule for f in invoices.SPEC.cluster_findings([pdf])}
    assert "not_in_ap_register" in rules
    reg = cand({}, source="invoices/ap_register.csv")
    rules = {f.rule for f in invoices.SPEC.cluster_findings([reg])}
    assert rules == {"no_document"}
    copy = cand({}, source="pdf/a_copy.pdf", fmt="pdf")
    rules = {f.rule for f in invoices.SPEC.cluster_findings([pdf, copy, reg])}
    assert "duplicate_document" in rules


def test_po_numbers_wrapped_in_memo_text_match_and_are_cleaned() -> None:
    memo = cand({"po_number": "PO PO-2489640"}, source="invoices/ap_register.csv")
    pdf = cand({"po_number": "PO-2489640"}, source="pdf/a.pdf", fmt="pdf")
    c = choose("po_number", [memo, pdf], invoices.SPEC)
    assert c.value == "PO-2489640" and not c.alternatives
    assert invoices.finalize({"po_number": "PO PO-2489640"}) == {"po_number": "PO-2489640"}
    assert invoices.finalize({"po_number": "PO-2489640"}) == {}
    assert invoices.finalize({"po_number": "4500012"}) == {}
    a, b = invoice(po_number="PO PO-2489640"), invoice(po_number="PO-2489640")
    assert invoices.strong_keys(a) == invoices.strong_keys(b)
