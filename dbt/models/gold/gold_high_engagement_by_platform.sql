-- High-engagement signals by platform and date.
-- Materialised as a table (see dbt_project.yml). Re-runs are idempotent.

with base as (
    select
        cast(captured_at as date) as dt,
        platform,
        platform_tier,
        signal_id,
        engagement_total,
        is_high_engagement
    from {{ source('silver', 'signals') }}
)

select
    dt,
    platform,
    platform_tier,
    count(*) as signal_count,
    count_if(is_high_engagement) as high_engagement_count,
    avg(engagement_total) as avg_engagement,
    max(engagement_total) as max_engagement
from base
group by 1, 2, 3
order by 1 desc, signal_count desc
