"""
Spot prices that are far outside what other hospitals list for the same procedure.

Hospital files contain data errors (a $0.01 chest X-ray, a $77,000 metabolic panel), and those would
otherwise become the "Min" and "Max" a patient sees first. A price is unusual when it is more than
FENCE_FACTOR times below the procedure's 5th percentile or above its 95th percentile. Anchoring on
the percentiles rather than the median keeps legitimately bimodal procedures intact, such as surgeries
where a surgeon's fee (~$600) and a facility fee (~$12,000) are both listed under the same code.
On the October 2026 Indiana data this flags under 1% of prices.

Unusual prices are still shown, but left out of ranges, averages, and min/max.
"""

import statistics

FENCE_FACTOR = 5
MIN_PRICES = 20  # with fewer prices the percentiles say too little to call anything unusual


def price_fences(values):
    """(low, high) bounds for usual prices, or None when there are too few prices to judge."""
    values = [v for v in values if v is not None and v > 0]
    if len(values) < MIN_PRICES:
        return None
    # 'inclusive' keeps a single extreme value from dragging the percentiles toward it in small samples
    q = statistics.quantiles(values, n=20, method='inclusive')
    return q[0] / FENCE_FACTOR, q[18] * FENCE_FACTOR


def unusual(value, fences):
    """'low', 'high', or None."""
    if fences is None or value is None:
        return None
    if value < fences[0]:
        return 'low'
    if value > fences[1]:
        return 'high'
    return None


def robust_stats(values):
    """min/max/avg over usual prices; count is every price, unusual is how many were left out."""
    values = [v for v in values if v is not None]
    if not values:
        return None
    fences = price_fences(values)
    usual = [v for v in values if not unusual(v, fences)] or values
    return {
        'min': min(usual),
        'max': max(usual),
        'avg': round(sum(usual) / len(usual), 2),
        'count': len(values),
        'unusual': len(values) - len(usual),
    }
