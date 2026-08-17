"""E-commerce listing agent — raw product → reviewed listing.

Demonstrates: multi-step pipeline, human-in-the-loop, conditional routing,
token tracking, checkpointing, and LLM retry with circuit breaker.

Usage:
    pip install kitegen[openai]
    export LLM_API_KEY=sk-...
    python examples/ecommerce_listing.py
"""

import asyncio
import os
import textwrap

import kitegen as kg
from kitegen.llm import chat, TokenTracker

# ── Pricing table for different platforms ──────────────────────────────

PLATFORM_FEES = {
    "shopify": 0.029,  # 2.9% + $0.30
    "amazon": 0.15,    # 15% referral fee
    "etsy": 0.065,     # 6.5% transaction fee
}

# ── Node 1: Enrich — LLM generates SEO content ────────────────────────

async def enrich(state: dict) -> dict:
    """LLM generates title, bullet points, and category from raw product info."""
    raw = state["raw_product"]
    prompt = textwrap.dedent(f"""\
        You are an e-commerce listing expert. Given this raw product info,
        generate an optimized listing.

        Raw info:
        Name: {raw['name']}
        Description: {raw.get('description', '')}
        Price: ${raw.get('price', 0):.2f}

        Return ONLY a JSON object with these fields:
        {{"title": "SEO-optimized title",
          "bullet_points": ["feature 1", "feature 2", "feature 3"],
          "category": "suggested category",
          "tags": ["tag1", "tag2"]}}
    """)

    resp = await chat(
        system_prompt="You are helpful. Return only valid JSON.",
        user_message=prompt,
        model="deepseek-chat",
        tracker=state.get("_tracker"),
    )

    import json
    enriched = json.loads(resp.content.strip().lstrip("```json").rstrip("```"))

    state["title"] = enriched["title"]
    state["bullet_points"] = enriched["bullet_points"]
    state["category"] = enriched["category"]
    state["tags"] = enriched["tags"]
    return state


# ── Node 2: Calculate pricing ─────────────────────────────────────────

async def calculate_pricing(state: dict) -> dict:
    """Calculate platform-specific pricing for each marketplace."""
    base_price = state["raw_product"]["price"]
    state["platform_pricing"] = {}
    for platform, fee in PLATFORM_FEES.items():
        platform_price = base_price / (1 - fee)
        state["platform_pricing"][platform] = {
            "listing_price": round(platform_price, 2),
            "fee": fee,
            "net_revenue": round(base_price, 2),
        }
    return state


# ── Node 3: Validate ──────────────────────────────────────────────────

async def validate(state: dict) -> dict:
    """Check listing for completeness and policy compliance."""
    issues = []

    if not state.get("title"):
        issues.append("Missing title")
    if len(state["title"]) > 200:
        issues.append("Title too long (>200 chars)")
    if not state.get("bullet_points"):
        issues.append("Missing bullet points")
    if "dangerous" in (state.get("raw_product", {}).get("description", "")).lower():
        issues.append("⚠️ Prohibited item detected")

    state["validation_issues"] = issues

    # Simulate an LLM call that might fail (triggers retry)
    if issues and state.get("_retry_once"):
        state["_retry_once"] = False
    else:
        state["_retry_once"] = True

    return state


# ── Node 4: Router — decide next step ─────────────────────────────────

async def review_router(state: dict) -> str:
    """Route: if issues, go back to enrich. Otherwise, go to human review."""
    if state.get("validation_issues"):
        return "enrich"
    return "review"


# ── Node 5: Human review (pause) ──────────────────────────────────────

async def human_review(state: dict) -> dict:
    """Pause for human approval. Resume with steward's decision."""
    resume_data = state.get("_resume_data")
    if resume_data is not None:
        state["approved"] = resume_data["approved"]
        state["steward_note"] = resume_data.get("note", "")
        return state

    # First time — present the listing to the steward
    print("\n" + "=" * 60)
    print("  📦 LISTING FOR REVIEW")
    print("=" * 60)
    print(f"  Title:    {state['title']}")
    print(f"  Category: {state['category']}")
    print(f"  Bullets:  {', '.join(state['bullet_points'][:2])}...")
    print(f"  Pricing:  {state['platform_pricing']}")
    print(f"  Tags:     {state['tags']}")
    print("=" * 60)

    # Pause — wait for steward
    await kg.interrupt({
        "action": "review_listing",
        "listing": {
            "title": state["title"],
            "category": state["category"],
            "bullet_points": state["bullet_points"],
            "platform_pricing": state["platform_pricing"],
        },
    })


# ── Node 6: Publish ───────────────────────────────────────────────────

async def publish(state: dict) -> dict:
    """Simulate publishing to platforms."""
    print("\n  🚀 Publishing to platforms...")
    for platform in state.get("approved_platforms", ["shopify"]):
        price = state["platform_pricing"][platform]["listing_price"]
        print(f"    ✅ {platform}: ${price}")
    state["published"] = True
    state["published_at"] = "2025-07-24T12:00:00Z"
    return state


# ── Main — build and run the graph ────────────────────────────────────

async def main():
    tracker = TokenTracker()

    # Build graph
    g = kg.Graph()
    g.add_node("enrich", enrich)
    g.add_node("pricing", calculate_pricing)
    g.add_node("validate", validate)
    g.add_node("review", human_review)
    g.add_node("publish", publish)

    g.add_edge("enrich", "pricing")
    g.add_edge("pricing", "validate")
    g.add_conditional_edges(
        "validate", review_router, {"enrich": "enrich", "review": "review"},
    )
    g.add_edge("review", "publish")
    g.set_entry_point("enrich")

    agent = g.compile(checkpointer=kg.MemorySaver())

    # Start with raw product info
    state = {
        "raw_product": {
            "name": "Handmade Ceramic Coffee Mug",
            "description": "Artisanal stoneware mug, microwave safe, 12oz capacity",
            "price": 24.99,
        },
        "_tracker": tracker,
    }

    # Run (will pause at review)
    thread_id = "listing-session-1"
    state = await agent.invoke(state, thread_id=thread_id)

    if state.get("_interrupted_at") == "review":
        print("\n  ⏸  Awaiting steward approval...")
        print(f"  Token usage so far: {tracker.total_tokens():,} tokens")
        print(f"  Cost: ${tracker.total_cost():.4f}")

        # Simulate steward reviewing and approving
        print("\n  👩‍💼 Steward reviewing...")
        await asyncio.sleep(1)  # pretend steward is reading
        decision = {
            "approved": True,
            "approved_platforms": ["shopify", "etsy"],
            "note": "Looks great! Add a gift-wrap option.",
        }
        print(f"  ✅ Decision: approved for Shopify + Etsy")

        # Resume with steward's decision
        state = await agent.resume(decision, thread_id=thread_id)

    # Show results
    print(f"\n{'=' * 60}")
    print(f"  📊 FINAL REPORT")
    print(f"{'=' * 60}")
    print(f"  Published: {state.get('published')}")
    print(f"  Title: {state.get('title', 'N/A')[:60]}...")
    print(f"  Token usage: {tracker.total_tokens():,}")
    print(f"  Total cost: ${tracker.total_cost():.4f}")
    print(f"  Node history: {[t.node for t in state.get('_node_history', [])]}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
