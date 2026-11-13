-- G-2: one query, totals per request, per user, and per agent for a date range.
-- Run with psql variables `from` and `to` (timestamps, `to` exclusive), for example:
--   psql -v from="'2026-11-13'" -v to="'2026-11-14'" -f usage_totals.sql
-- The agent is the `agent:` tag of the calling virtual key (LiteLLM_VerificationToken), never the
-- request's metadata, so a caller cannot spoof it. A call with no `user` counts under `unknown`.
WITH usage AS (
  SELECT
    s.request_id,
    -- The request's `user` field lands in `end_user` (LiteLLM v1.103.0); `user` is the key owner.
    COALESCE(NULLIF(s.end_user, ''), 'unknown') AS usr,
    COALESCE(
      (SELECT substr(t, 7) FROM jsonb_array_elements_text(v.metadata -> 'tags') AS t
        WHERE t LIKE 'agent:%' LIMIT 1),
      'unknown') AS agent,
    s.prompt_tokens,
    s.completion_tokens,
    s.spend
  FROM "LiteLLM_SpendLogs" s
  LEFT JOIN "LiteLLM_VerificationToken" v ON v.token = s.api_key
  WHERE s."startTime" >= :from AND s."startTime" < :to
)
SELECT
  CASE
    WHEN GROUPING(request_id) = 0 THEN 'request'
    WHEN GROUPING(usr) = 0 THEN 'user'
    ELSE 'agent'
  END AS level,
  COALESCE(request_id, usr, agent) AS name,
  count(*) AS calls,
  sum(prompt_tokens) AS input_tokens,
  sum(completion_tokens) AS output_tokens,
  round(sum(spend)::numeric, 9) AS cost
FROM usage
GROUP BY GROUPING SETS ((request_id), (usr), (agent))
ORDER BY level, name;
