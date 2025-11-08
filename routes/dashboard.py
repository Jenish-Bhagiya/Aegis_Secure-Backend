# Backend/routes/dashboard.py
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime
from .auth import get_current_user
from database import messages_col, sms_messages_col
from typing import Dict, Any

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

# Score ranges (0-100). We'll use these labels/order everywhere:
LABELS = ["Safe", "Less Safe", "Less Scam", "High Scam"]
# Boundaries for $bucket: [0, 26, 51, 76, 101] -> buckets 0..3
BUCKET_BOUNDS = [0, 26, 51, 76, 101]


def _format_response(counts: Dict[int, int]) -> Dict[str, Any]:
    """Return response in consistent label order."""
    values = [counts.get(i, 0) for i in range(len(LABELS))]
    return {"labels": LABELS, "values": values, "total": sum(values)}


async def _aggregate_collection_by_buckets(col, user_id_field, score_field, user_id, days: int = None):
    """Aggregate a single collection into the 4 score buckets for a user.
       col: motor collection
       user_id_field: name of user field (usually 'user_id')
       score_field: 'spam_score' or 'spam_prediction'
    """
    match_stage = {"$match": {user_id_field: user_id}}
    if days is not None:
        # filter by timestamp within last `days` days if a timestamp field exists
        # we try common fields 'timestamp' or 'created_at'
        from datetime import datetime, timedelta
        since_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
        match_stage["$match"].update(
            {"$or": [{"timestamp": {"$gte": since_ms}}, {"created_at": {"$gte": datetime.utcnow() - timedelta(days=days)}}]}
        )

    # Only include numeric scores in range [0,100]
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
        {
            "$match": {"score": {"$gte": 0, "$lte": 100}}
        },
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
    idx_map = {  # bucket boundary -> index
        0: 0,   # 0-25 -> Safe
        26: 1,  # 26-50 -> Less Safe
        51: 2,  # 51-75 -> Less Scam
        76: 3   # 76-100 -> High Scam
    }
    async for doc in cursor:
        b = doc.get("_id")
        if isinstance(b, (int, float)):
            # find the correct bucket start boundary
            # pick the nearest smaller boundary
            b_key = None
            for boundary in sorted(idx_map.keys(), reverse=True):
                if b >= boundary:
                    b_key = boundary
                    break
            if b_key is not None:
                counts[idx_map[b_key]] = counts.get(idx_map[b_key], 0) + doc.get("count", 0)
    return counts


@router.get("/")
async def get_dashboard(
    mode: str = Query("both", regex="^(sms|mail|both)$"),
    days: int = Query(None, ge=1),
    current_user: dict = Depends(get_current_user),
):
    """
    Return counts for risk-level buckets (Safe / Less Safe / Less Scam / High Scam)
    mode: sms | mail | both
    days: optional integer to limit to last N days
    """
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    total_counts = {}

    if mode in ("sms", "both"):
        # sms_messages_col uses 'user_id' and 'spam_score'
        sms_counts = await _aggregate_collection_by_buckets(
            sms_messages_col, "user_id", "spam_score", user_id, days
        )
        for k, v in sms_counts.items():
            total_counts[k] = total_counts.get(k, 0) + v

    if mode in ("mail", "both"):
        # messages_col uses 'user_id' and 'spam_prediction'
        mail_counts = await _aggregate_collection_by_buckets(
            messages_col, "user_id", "spam_prediction", user_id, days
        )
        for k, v in mail_counts.items():
            total_counts[k] = total_counts.get(k, 0) + v

    response = _format_response(total_counts)
    return response