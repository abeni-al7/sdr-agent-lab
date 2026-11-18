"""One place to look up a lead and its company, whether it is a fictional demo lead (data/*.json)
or a real lead a user added (the `leads` table, researched live from free public sources)."""
import time

from . import db
from .config import COMPANIES, LEADS


def get(lead_id: str) -> dict | None:
    if lead_id in LEADS:
        return {**LEADS[lead_id], "live": False}
    with db.engine.connect() as conn:
        r = conn.execute(db.select(db.leads).where(db.leads.c.id == lead_id)).first()
    return {**db.row(r), "live": True} if r else None


def require(lead_id: str) -> dict:
    lead = get(lead_id)
    if not lead:
        raise KeyError(f"unknown lead {lead_id}")
    return lead


def company(lead: dict) -> dict:
    """Company facts known before research. Live leads start with what the user typed."""
    if not lead.get("live"):
        return COMPANIES[lead["domain"]]
    return {"name": lead["company"], "industry": lead.get("industry"), "employees": lead.get("employees"),
            "hq": lead.get("hq")}


def all_leads() -> list[dict]:
    with db.engine.connect() as conn:
        live = [{**db.row(r), "live": True} for r in conn.execute(db.select(db.leads).order_by(db.leads.c.created_at.desc()))]
    return live + [{**l, "live": False} for l in LEADS.values()]


def add(*, name: str, title: str, company: str, domain: str, industry: str | None = None,
        employees: int | None = None, hq: str | None = None, topic: str | None = None, source: str = "") -> dict:
    lead_id = "R-" + db.new_id("x")[2:10]
    row = {"id": lead_id, "name": name, "title": title, "company": company, "domain": domain,
           "industry": industry or None, "employees": employees, "hq": hq or None, "topic": topic or None,
           "source": source or "Added in the console", "created_at": time.time()}
    with db.engine.begin() as conn:
        conn.execute(db.insert(db.leads).values(**row))
    return {**row, "live": True}
