select txn_id, booked_gl, review_decision
from {{ source('raw', 'historical_reviews') }}
