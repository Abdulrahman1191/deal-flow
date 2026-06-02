"""
Seed synthetic MENA deep-tech leads for the test environment.

Inserts plausible-but-fake companies directly into the leads table (no Copper
sync), then queues assess_lead_task for each. The AI will assess them naturally
and they'll spread across YES/MAYBE/REJECT.

Also re-triggers any leads currently stuck in 'processing' so the Kanban
doesn't show indefinite spinners.

Usage: python scripts/seed_test_leads.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.lead import Lead


SEED_LEADS = [
    {
        "company_name": "NanoMed Diagnostics",
        "website": "nanomed.sa",
        "description": "Saudi-based biotech developing nanopore-based rapid disease screening kits with proprietary biosensor IP. 3 issued patents in MENA, 2 pending US.",
        "stage": "Seed",
        "region": "Saudi Arabia",
        "founder_names": ["Dr. Layla Al-Rashid"],
    },
    {
        "company_name": "QuantumGulf",
        "website": "quantumgulf.ae",
        "description": "UAE quantum computing startup building post-quantum cryptography hardware for regional banks. Spin-out from KAUST. Working prototype, Mubadala-backed.",
        "stage": "Series A",
        "region": "UAE",
        "founder_names": ["Omar Khalifa", "Sara Nasser"],
    },
    {
        "company_name": "DesertSun Robotics",
        "website": "desertsun-robotics.com",
        "description": "Egypt-based autonomous solar panel cleaning robots, computer vision + custom-designed brushless motors tuned for sandstorms. 12 patents filed, deployments at 4 GW of capacity.",
        "stage": "Pre-Seed",
        "region": "Egypt",
        "founder_names": ["Ahmed Tarek"],
    },
    {
        "company_name": "Halal Bites Delivery",
        "website": "halalbites.io",
        "description": "On-demand halal food delivery app for Riyadh and Jeddah. Marketplace connecting restaurants to drivers via mobile app.",
        "stage": "Seed",
        "region": "Saudi Arabia",
        "founder_names": ["Faisal Otaibi"],
    },
    {
        "company_name": "AraBERT Labs",
        "website": "arabert-labs.com",
        "description": "Arabic-first LLM for legal and financial document understanding. Custom training corpus of 200B Arabic tokens. Customers include Aramco, Emirates NBD pilot.",
        "stage": "Seed",
        "region": "UAE",
        "founder_names": ["Dr. Hisham Al-Zahrani", "Marwan El-Sheikh"],
    },
    {
        "company_name": "AgroSahara",
        "website": "agrosahara.tn",
        "description": "Precision agriculture IoT platform for North African date farms. Soil sensor network + ML yield prediction. Deployed across 8,000 hectares in Tunisia and Morocco.",
        "stage": "Seed",
        "region": "Tunisia",
        "founder_names": ["Yasmine Ben Ali"],
    },
    {
        "company_name": "TasteWeek",
        "website": "tasteweek.com",
        "description": "Weekly meal box subscription service. Curated MENA cuisine recipes shipped with ingredients. SaaS-style recurring revenue, Shopify backend.",
        "stage": "Seed",
        "region": "UAE",
        "founder_names": ["Reem Sultan"],
    },
    {
        "company_name": "HydraTech Desalination",
        "website": "hydratech.sa",
        "description": "Novel graphene-oxide membrane desalination technology delivering 40% energy savings vs. reverse osmosis. 3 patents granted, pilot plant with SWCC in Jeddah.",
        "stage": "Series A",
        "region": "Saudi Arabia",
        "founder_names": ["Dr. Fahad Al-Mansour", "Lina Habib"],
    },
    {
        "company_name": "SkyDrop Logistics",
        "website": "skydrop.eg",
        "description": "Drone delivery startup for medical supplies in remote Egyptian villages. Custom long-range fixed-wing drone, BVLOS regulatory approval in progress.",
        "stage": "Pre-Seed",
        "region": "Egypt",
        "founder_names": ["Karim Mostafa"],
    },
    {
        "company_name": "FinFlex Pay",
        "website": "finflex.pay",
        "description": "Payment gateway aggregator for SMBs in Jordan and Lebanon. Connects to local processors and offers a unified API. Standard PSP business model.",
        "stage": "Seed",
        "region": "Jordan",
        "founder_names": ["Tariq Haddad"],
    },
    {
        "company_name": "Bahri Marine Tech",
        "website": "bahrimarine.com",
        "description": "Autonomous underwater vehicle (AUV) for Red Sea oil pipeline inspection. Computer vision + sonar fusion, proprietary corrosion-detection model. Saudi Aramco LOI.",
        "stage": "Seed",
        "region": "Saudi Arabia",
        "founder_names": ["Eng. Mohammed Al-Qahtani"],
    },
    {
        "company_name": "PalmGenome",
        "website": "palmgenome.ae",
        "description": "Genomic sequencing service for date palm breeding. CRISPR-based varietal improvement for climate resilience. 2 patents on edited cultivars, partnership with UAE Ministry of Climate Change.",
        "stage": "Series A",
        "region": "UAE",
        "founder_names": ["Dr. Aisha Mubarak", "Dr. Khalid Al-Suwaidi"],
    },
    {
        "company_name": "MarrakechMart",
        "website": "marrakechmart.ma",
        "description": "E-commerce platform for traditional Moroccan handicrafts. Drop-shipping model, marketing-focused growth strategy.",
        "stage": "Seed",
        "region": "Morocco",
        "founder_names": ["Younes Benali"],
    },
    {
        "company_name": "NeuroLevant",
        "website": "neurolevant.com",
        "description": "Brain-computer interface for ALS patients. Non-invasive EEG headset with novel signal-processing IP (3 patents pending). Spin-out from American University of Beirut.",
        "stage": "Pre-Seed",
        "region": "Lebanon",
        "founder_names": ["Dr. Nadia Sleiman"],
    },
    {
        "company_name": "GulfLogic AI",
        "website": "gulflogic.ai",
        "description": "Reinforcement-learning-based supply chain optimizer for port operations. Live at Khalifa Port, 18% reduction in container dwell times. Custom RL framework, 1 patent issued.",
        "stage": "Series A",
        "region": "UAE",
        "founder_names": ["Saif Al-Suwaidi", "Dr. Patrick Karam"],
    },
    {
        "company_name": "SoukLoop",
        "website": "soukloop.com",
        "description": "Wholesale marketplace connecting MENA SMB retailers to Chinese suppliers. Aggregation play, no proprietary tech.",
        "stage": "Seed",
        "region": "Saudi Arabia",
        "founder_names": ["Bandar Al-Otaibi"],
    },
    {
        "company_name": "OasisCarbon",
        "website": "oasiscarbon.com",
        "description": "Direct air capture (DAC) technology optimized for hot-arid climates. Proprietary sorbent material with 3x capture density vs. conventional. Pilot underway with ADNOC.",
        "stage": "Seed",
        "region": "UAE",
        "founder_names": ["Dr. Rana Choueiri", "Eng. Walid Hammoud"],
    },
    {
        "company_name": "Kalimat Therapy",
        "website": "kalimattherapy.com",
        "description": "Telehealth platform offering Arabic-language mental health therapy. Marketplace connecting licensed therapists to patients via video.",
        "stage": "Seed",
        "region": "Jordan",
        "founder_names": ["Lara Khouri"],
    },
]


async def main() -> None:
    async with AsyncSessionLocal() as db:
        # Re-queue any stuck 'processing' leads first
        from app.tasks.assess_lead import assess_lead_task
        from sqlalchemy import text

        stuck = await db.execute(text(
            "SELECT id, company_name FROM leads WHERE status = 'processing'"
        ))
        for row in stuck.fetchall():
            assess_lead_task.delay(str(row[0]))
            print(f"  re-queued processing: {row[1]}")

        # Insert new seed leads
        added = 0
        for data in SEED_LEADS:
            # Skip if name already exists
            r = await db.execute(
                select(Lead).where(Lead.company_name == data["company_name"]).limit(1)
            )
            if r.scalar_one_or_none():
                print(f"  skip (already exists): {data['company_name']}")
                continue
            lead = Lead(status="pending", **data)
            db.add(lead)
            await db.flush()
            assess_lead_task.delay(str(lead.id))
            print(f"  added + queued: {data['company_name']}")
            added += 1
        await db.commit()

        # Final counts
        r1 = await db.execute(text("SELECT COUNT(*) FROM leads WHERE status NOT IN ('archived','approved')"))
        visible = r1.scalar()
        print(f"\nAdded {added} new leads. Visible in Kanban: {visible}")
        print("AI assessment is now running in Celery — expect ~15-30 min for all to complete.")


if __name__ == "__main__":
    asyncio.run(main())
