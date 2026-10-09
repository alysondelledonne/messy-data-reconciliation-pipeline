"""Fictional organizations, names, titles and places for the contacts pack.

- Email domains use the reserved `.example` top-level domain (RFC 2606).
- North American phone numbers use the 555-0100..0199 block reserved for fiction.
- UK numbers use Ofcom's ranges reserved for drama.

No generated contact detail can reach a real person.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Company:
    company_id: str
    name: str
    domain: str
    city: str
    weight: int = 3
    lookalike_of: str | None = None


@dataclass(frozen=True)
class City:
    name: str
    country: str  # ISO 3166-1 alpha-2
    dial: str  # E.164 prefix up to the fictional block, e.g. "+1312555"


CITIES: dict[str, City] = {
    c.name: c
    for c in (
        City("Chicago", "US", "+1312555"),
        City("Denver", "US", "+1303555"),
        City("Austin", "US", "+1512555"),
        City("Portland", "US", "+1503555"),
        City("Atlanta", "US", "+1404555"),
        City("Boston", "US", "+1617555"),
        City("Seattle", "US", "+1206555"),
        City("Phoenix", "US", "+1602555"),
        City("Columbus", "US", "+1614555"),
        City("Raleigh", "US", "+1919555"),
        City("Toronto", "CA", "+1416555"),
        City("Vancouver", "CA", "+1604555"),
        City("Montréal", "CA", "+1514555"),
        City("Calgary", "CA", "+1403555"),
        City("London", "GB", "+44207946"),
        City("Manchester", "GB", "+44161496"),
        City("Bristol", "GB", "+44117496"),
        City("Leeds", "GB", "+44113496"),
    )
}

COMPANIES: tuple[Company, ...] = (
    Company("C01", "Northgate Realty Partners", "northgaterealty.example", "Chicago", 6),
    Company("C02", "Bluewater Holdings LLC", "bluewaterholdings.example", "Boston", 6),
    Company("C03", "Meridian Property Management Inc.", "meridianpm.example", "Denver", 5),
    Company("C04", "Crestview Commercial Group", "crestviewcg.example", "Austin", 5),
    Company("C05", "Oakline Construction Co.", "oakline.example", "Portland", 4),
    Company("C06", "Harborpoint Capital LLC", "harborpoint.example", "Seattle", 3),
    Company("C07", "Silverleaf Facilities Inc.", "silverleaf.example", "Atlanta", 4),
    Company("C08", "Redstone Development Corp.", "redstonedev.example", "Phoenix", 3),
    Company("C09", "Juniper Tenant Services LLC", "junipertenant.example", "Columbus", 3),
    Company("C10", "Brightwater Asset Management", "brightwateram.example", "Raleigh", 3),
    Company("C11", "Fieldstone Engineering Associates", "fieldstone-eng.example", "Denver", 3),
    Company("C12", "Kettle Creek Builders Ltd.", "kettlecreek.example", "Calgary", 3),
    Company("C13", "Ashgrove Insurance Brokers Inc.", "ashgrove.example", "Toronto", 2),
    Company("C14", "Westmark Logistics Inc.", "westmark.example", "Columbus", 2),
    Company("C15", "Elmhurst Legal Services LLP", "elmhurstlegal.example", "Chicago", 2),
    Company("C16", "Copperfield Architects", "copperfield-arch.example", "Seattle", 2),
    Company("C17", "Granite Peak Energy Co.", "granitepeak.example", "Calgary", 2),
    Company("C18", "Larkspur Health Systems", "larkspurhealth.example", "Boston", 3),
    Company("C19", "Mosaic Interiors Studio", "mosaicinteriors.example", "Austin", 2),
    Company("C20", "Tallgrass Hospitality Group", "tallgrass.example", "Phoenix", 2),
    Company("C21", "Quillon Data Systems Inc.", "quillon.example", "Raleigh", 2),
    Company("C22", "Foxhollow Property Trust", "foxhollow.example", "Atlanta", 2),
    Company("C23", "Bramblewood Senior Living", "bramblewood.example", "Portland", 2),
    Company("C24", "Stonebridge Municipal Services", "stonebridge.example", "Toronto", 2),
    Company("C25", "Cinderhill Manufacturing Co.", "cinderhill.example", "Vancouver", 2),
    Company("C26", "Driftwood Retail Partners", "driftwoodretail.example", "Vancouver", 2),
    Company("C27", "Halcyon Office Parks Ltd.", "halcyonparks.example", "London", 3),
    Company("C28", "Thistledown Estates Ltd.", "thistledown.example", "Manchester", 2),
    Company("C29", "Wexford Lane Lettings Ltd.", "wexfordlane.example", "Bristol", 2),
    Company("C30", "Maplecroft Properties Inc.", "maplecroft.example", "Montréal", 3),
    Company("C31", "Laurentian Building Group", "laurentianbg.example", "Montréal", 2),
    Company("C32", "Saltmarsh Environmental LLC", "saltmarsh.example", "Leeds", 2),
    # Lookalikes: different organizations whose names sit close to another's.
    Company("C33", "Northgate Realty Advisors", "northgateadvisors.example", "Chicago", 2,
            lookalike_of="C01"),
    Company("C34", "Bluewater Holding Group", "bluewatergroup.example", "Boston", 2,
            lookalike_of="C02"),
    Company("C35", "Meridian Property Services Inc.", "meridianps.example", "Denver", 2,
            lookalike_of="C03"),
    Company("C36", "Crestview Commercial Realty", "crestviewrealty.example", "Austin", 2,
            lookalike_of="C04"),
)  # fmt: skip

FIRST_NAMES = (
    "Robert", "William", "Katherine", "Jennifer", "Michael", "Elizabeth", "Daniel",
    "Alexander", "Christopher", "Margaret", "Jonathan", "Patricia", "Samuel", "Rebecca",
    "Thomas", "Victoria", "Benjamin", "Theodore", "Josephine", "Nicholas", "Abigail",
    "Matthew", "Stephanie", "Anthony", "Dana", "Priya", "Raj", "Mei", "Hiroshi", "Aisha",
    "Kwame", "Lucía", "José", "Zoë", "Siobhán", "Mateus", "Ingrid", "Tomasz", "Fatima",
    "Oluwaseun", "Camille", "Noor", "Diego", "Hannah", "Marcus", "Elena", "Sven", "Amara",
    "Yusuf", "Chloé", "Leila", "Andrés", "Grace", "Omar", "Nadia", "Ravi", "Sofia", "Ethan",
    "Jordan", "Taylor", "Morgan", "Casey", "Avery", "Quinn", "Rowan", "Parker",
)  # fmt: skip

LAST_NAMES = (
    "Whitfield", "Patel", "Nakamura", "Okafor", "García", "Lindqvist", "Kowalski",
    "O'Brien", "Haddad", "Mensah", "Fernández", "Chen", "Dubois", "Rossi", "Novak",
    "Abernathy", "Castellanos", "Delacroix", "Eriksen", "Fairbanks", "Gallagher",
    "Hollister", "Iverson", "Jablonski", "Kincaid", "Laurent", "MacAllister", "Nwosu",
    "Ortega", "Pemberton", "Quintero", "Ramaswamy", "Sorensen", "Thibodeaux", "Underwood",
    "Vasquez", "Wainwright", "Yilmaz", "Zielinski", "Ashworth", "Bellamy", "Carrington",
    "Donnelly", "Esposito", "Fitzgerald", "Grünewald", "Hargreaves", "Ibarra", "Jensen",
    "Kaur", "Lombardi", "Moreau", "Nguyen", "Oyelaran", "Petrov", "Rahman", "Sato",
    "Tanaka", "Van der Berg", "Whitaker", "Achebe", "Brennan", "Cho", "Duarte", "Ellison",
)  # fmt: skip

JOB_TITLES = (
    "Facilities Manager", "Facilities Lead", "Property Manager", "Senior Property Manager",
    "Procurement Manager", "Procurement Specialist", "Accounts Payable Lead",
    "Director of Operations", "VP, Real Estate", "Building Engineer", "Chief Engineer",
    "Office Manager", "Controller", "Leasing Director", "Project Manager",
    "Sustainability Coordinator", "Asset Manager", "Operations Analyst",
    "Head of Workplace", "Maintenance Supervisor",
)  # fmt: skip

COUNTRY_SPELLINGS = {
    "US": ["US", "USA", "United States", "U.S.", "United States of America"],
    "CA": ["CA", "Canada", "CAN"],
    "GB": ["GB", "UK", "United Kingdom", "England"],
}

SUBJECTS = (
    "Re: Q{q} service schedule", "Quote request: {thing}", "Following up on {thing}",
    "Invoice question", "Site access for next week", "Intro + new point of contact",
    "Updated contact details", "Re: Re: {thing}", "Meeting notes from {day}",
    "Can we reschedule?", "FW: {thing}", "Thanks for today",
)  # fmt: skip
THINGS = (
    "the HVAC contract", "lobby renovation", "parking lot resurfacing", "roof inspection",
    "the cleaning bid", "elevator modernization", "lease renewal", "fire drill",
    "badge access", "the Q3 budget",
)  # fmt: skip
BODY_LINES = (
    "Just wanted to follow up on our conversation from last week.",
    "Could you send over the updated proposal when you get a chance?",
    "We are good to proceed on our side pending final numbers.",
    "Please loop in whoever handles scheduling on your team.",
    "I've attached what we discussed.",
    "Let me know if Tuesday or Thursday works better.",
    "I'm taking over this account going forward, so please use my details below.",
    "Apologies for the delay. It has been a busy month.",
    "Can you confirm the access window for the contractors?",
    "Happy to jump on a call if that's easier.",
)
