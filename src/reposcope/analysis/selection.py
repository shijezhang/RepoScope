"""Deterministic weighted coverage with explicit conservative fallbacks."""


def select_tests(nodeids, targets, coverage, durations=None, budget=120, fallback_reasons=()):
    durations = durations or {}
    pool = sorted(set(nodeids))
    if fallback_reasons or not coverage:
        return {
            "nodeids": pool,
            "strategy": "all",
            "reasons": list(fallback_reasons) or ["Coverage unavailable"],
            "uncovered": sorted(targets),
            "estimated_seconds": sum(durations.get(n, 1) for n in pool),
        }
    remaining, selected, seconds = set(targets), [], 0.0
    while remaining:
        choices = []
        for nid in pool:
            if nid in selected:
                continue
            cost = max(0.001, durations.get(nid, 1))
            gain = sum(targets[t] for t in remaining.intersection(coverage.get(nid, [])))
            if gain and seconds + cost <= budget:
                choices.append((-gain / cost, nid, cost))
        if not choices:
            break
        _, nid, cost = min(choices)
        selected.append(nid)
        seconds += cost
        remaining.difference_update(coverage[nid])
    return {
        "nodeids": selected,
        "strategy": "weighted-coverage",
        "estimated_seconds": seconds,
        "uncovered": sorted(remaining),
        "reasons": ["Version-bound coverage weighted by target importance and historical duration"],
    }
