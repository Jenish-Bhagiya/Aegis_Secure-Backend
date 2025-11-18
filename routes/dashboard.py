import os,re,json
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timedelta
from .auth import get_current_user
from database import messages_col, sms_messages_col
from typing import Dict, Any
from groq import Groq


client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

LABELS = ["Secure", "Suspicious", "Threat", "Critical"]
BUCKET_BOUNDS = [0, 26, 51, 76, 101]

# Static Cybersecurity Trends
CYBER_TRENDS = [
    "Beware of SMS phishing links claiming lottery wins",
    "Do not open emails from unknown senders with attachments",
    "Enable Multi-factor Authentication (MFA)",
    "Recent phishing campaigns mimic banking institutions"
]


def _format_response(counts: Dict[int, int], summary: str = "", trends: list = []) -> Dict[str, Any]:
    """Return response in consistent label order, with summary and trends."""
    values = [counts.get(i, 0) for i in range(len(LABELS))]
    return {
        "labels": LABELS,
        "values": values,
        "total": sum(values),
        "summary": summary,
        "trends": trends
    }


async def _aggregate_collection_by_buckets(col, user_id_field, score_field, user_id, days: int = None):
    """Aggregate a single collection into the 4 score buckets for a user."""
    match_stage = {"$match": {user_id_field: user_id}}
    if days is not None:
        since_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
        match_stage["$match"].update({
            "$or": [
                {"timestamp": {"$gte": since_ms}},
                {"created_at": {"$gte": datetime.utcnow() - timedelta(days=days)}}
            ]
        })

    pipeline = [
        match_stage,
        {
            "$project": {
                "score": {
                    "$convert": {
                        "input": f"${score_field}",
                        "to": "double",
                        "onError": 0.0,
                        "onNull": 0.0
                    }
                }
            }
        },
        {"$match": {"score": {"$gte": 0, "$lte": 100}}},
        {
            "$bucket": {
                "groupBy": "$score",
                "boundaries": BUCKET_BOUNDS,
                "default": "other",
                "output": {"count": {"$sum": 1}}
            }
        }
    ]

    cursor = col.aggregate(pipeline)
    counts = {}
    idx_map = {0: 0, 26: 1, 51: 2, 76: 3}

    async for doc in cursor:
        b = doc.get("_id")
        if isinstance(b, (int, float)):
            b_key = None
            for boundary in sorted(idx_map.keys(), reverse=True):
                if b >= boundary:
                    b_key = boundary
                    break
            if b_key is not None:
                counts[idx_map[b_key]] = counts.get(idx_map[b_key], 0) + doc.get("count", 0)

    return counts

async def generate_cyber_facts_ai() -> dict:
    try:
        prompt = """
        You are a cybersecurity assistant. Output a single JSON object with keys fact1 and fact2.
        Each fact must be a short cybersecurity insight or tip.
        Only output JSON, no explanations, no Markdown.
        Example:
        {"fact1": "Enable MFA to prevent account theft.", "fact2": "Do not click suspicious email links."}
        """
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
        )

        content = ""
        if hasattr(response, "choices") and response.choices:
            msg = getattr(response.choices[0], "message", None)
            if msg:
                content = getattr(msg, "content", "") or getattr(msg, "reasoning", "")

        content = content.strip()
        match = re.search(r"{.*}", content, re.DOTALL)
        if match:
            content = match.group(0)

        try:
            parsed = json.loads(content)
            if "fact1" in parsed and "fact2" in parsed:
                return parsed
        except json.JSONDecodeError as e:
            print(f"JSON parsing failed: {e}, content: {content}")

        return {
            "fact1": "*** Unable to fetch cybersecurity insights right now.",
            "fact2": "Please try again later."
        }
    
    except Exception as e:
        # print(f"*** Groq AI call failed: {e}")
        return {"fact1": "","fact2": ""}


@router.get("")
@router.get("/")
async def get_dashboard(
    mode: str = Query("both", regex="^(sms|mail|both)$"),
    days: int = Query(None, ge=1),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Prepare per-mode counts
    sms_counts, mail_counts = {}, {}
    if mode in ("sms", "both"):
        sms_counts = await _aggregate_collection_by_buckets(
            sms_messages_col, "user_id", "spam_score", user_id, days
        )
    if mode in ("mail", "both"):
        mail_counts = await _aggregate_collection_by_buckets(
            messages_col, "user_id", "spam_prediction", user_id, days
        )

    # Combine if mode is "both"
    if mode == "both":
        combined_counts = {i: sms_counts.get(i, 0) + mail_counts.get(i, 0) for i in range(len(LABELS))}
        counts_to_send = combined_counts
    elif mode == "sms":
        counts_to_send = sms_counts
    elif mode == "mail":
        counts_to_send = mail_counts

    # Ensure all 4 buckets exist
    for i in range(len(LABELS)):
        counts_to_send.setdefault(i, 0)

    insights = await generate_cyber_facts_ai()

    return {
        "labels": LABELS,
        "values": [counts_to_send[i] for i in range(len(LABELS))],
        "total": sum(counts_to_send.values()),
        "insights": insights
    }
