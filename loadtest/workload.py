"""The shared workload for both the eval and load harnesses.

See PLAN.md's "The workload (shared by both)" section for the rationale:
category-style questions where a brand could plausibly appear, mirroring
what Evertune's product actually measures (mention rates across repeated
queries) — not a generic "say hi" load-test prompt.

candidate_brands is the hand-written extraction list for the output-variance
experiment (PLAN.md's Decision 3) — deliberately simple substring matching,
not an LLM-based extractor. See that section for why: a second stochastic
process inside a variance measurement would make it impossible to attribute
observed variance to the model rather than the extractor.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkloadItem:
    category: str
    prompt: str
    candidate_brands: tuple


WORKLOAD = (
    WorkloadItem(
        category="running shoes",
        prompt="What are the best running shoes for marathon training?",
        candidate_brands=("Nike", "Brooks", "Hoka", "Asics", "New Balance", "Saucony", "Adidas"),
    ),
    WorkloadItem(
        category="project management tools",
        prompt="What are the top project management tools for a small team?",
        candidate_brands=("Asana", "Trello", "Monday", "Jira", "ClickUp", "Notion", "Basecamp"),
    ),
    WorkloadItem(
        category="robot vacuums",
        prompt="What are the most reliable robot vacuums under $500?",
        candidate_brands=("iRobot", "Roomba", "Roborock", "Eufy", "Shark", "Ecovacs", "Bissell"),
    ),
    WorkloadItem(
        category="noise-cancelling headphones",
        prompt="What are the best noise-cancelling headphones for frequent flyers?",
        candidate_brands=("Sony", "Bose", "Apple", "Sennheiser", "Beats", "Jabra"),
    ),
    WorkloadItem(
        category="password managers",
        prompt="What's the best password manager for a small business team?",
        candidate_brands=("1Password", "Bitwarden", "LastPass", "Dashlane", "Keeper", "NordPass"),
    ),
    WorkloadItem(
        category="standing desks",
        prompt="What are the most reliable standing desks for a home office?",
        candidate_brands=("Uplift", "Fully", "Autonomous", "Flexispot", "Vari", "IKEA"),
    ),
    WorkloadItem(
        category="meal kit services",
        prompt="What are the best meal kit delivery services for a busy family?",
        candidate_brands=("HelloFresh", "Blue Apron", "Home Chef", "EveryPlate", "Sunbasket", "Factor"),
    ),
    WorkloadItem(
        category="CRM software",
        prompt="What CRM software should a small sales team use?",
        candidate_brands=("Salesforce", "HubSpot", "Pipedrive", "Zoho", "Close", "Monday"),
    ),
    WorkloadItem(
        category="electric toothbrushes",
        prompt="What's the best electric toothbrush for sensitive teeth?",
        candidate_brands=("Oral-B", "Sonicare", "Philips", "Quip", "Colgate", "Burst"),
    ),
    WorkloadItem(
        category="budgeting apps",
        prompt="What's the best budgeting app for tracking shared household expenses?",
        candidate_brands=("YNAB", "Mint", "Monarch", "Copilot", "EveryDollar", "PocketGuard"),
    ),
    WorkloadItem(
        category="carry-on luggage",
        prompt="What's the most durable carry-on luggage for frequent travelers?",
        candidate_brands=("Away", "Samsonite", "Travelpro", "Monos", "Rimowa", "Briggs & Riley"),
    ),
    WorkloadItem(
        category="air purifiers",
        prompt="What are the best air purifiers for allergies?",
        candidate_brands=("Levoit", "Coway", "Blueair", "Honeywell", "Dyson", "Winix"),
    ),
)
