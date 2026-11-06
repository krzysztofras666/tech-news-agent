"""Travel deals scraper agent.

Fetches listing/last-minute pages from a curated list of Polish travel
portals, uses an LLM to extract structured offers from the messy HTML,
then aggregates and groups them by destination so the user can spot the
cheapest deals at a glance.

Designed to run alongside the gmail_agent in this repo; it reuses the
same .venv and the same ``OPENAI_API_KEY`` from ``.env``.
"""

__version__ = "0.1.0"
