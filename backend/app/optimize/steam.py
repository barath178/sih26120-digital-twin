"""Field-level steam allocation.

Steam generators are shared. Each well needs a generator for `inj_days` at `inj_rate_tpd`
once its production period ends. The scheduler builds a calendar over a horizon, assigning
injections to the earliest free generator slot. When wells compete, the one with the higher
value of steam (expected net value per tonne, from the twin) and the earlier due date wins."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SteamRequest:
    well_id: str
    due_day: float           # days from now when the well wants steam (<=0 = overdue)
    inj_days: float
    steam_t: float
    rate_tpd: float
    value_per_t: float       # INR of net value per tonne of steam (priority)
    optimal_day: float | None = None


@dataclass
class ActiveInjection:
    well_id: str
    generator: str
    remaining_days: float
    steam_t: float
    rate_tpd: float


def schedule(generators: list[dict], active: list[ActiveInjection], requests: list[SteamRequest], horizon: float = 180.0,
             step: float = 0.25) -> dict:
    free_at = {g["id"]: 0.0 for g in generators}
    cap = {g["id"]: g["capacity_tpd"] for g in generators}
    events = []
    for a in active:
        free_at[a.generator] = max(free_at[a.generator], a.remaining_days)
        events.append(dict(well_id=a.well_id, generator=a.generator, start_day=0.0, end_day=a.remaining_days, steam_t=a.steam_t,
                           rate_tpd=a.rate_tpd, status="injecting", delay_days=0.0))
    # priority: earliest due first; among wells due at the same time, the more valuable first
    order = sorted(requests, key=lambda r: (max(r.due_day, 0.0), -r.value_per_t))
    unscheduled = []
    for r in order:
        best = None
        for gid in free_at:
            if cap[gid] + 1e-9 < r.rate_tpd:
                continue
            start = max(free_at[gid], max(r.due_day, 0.0))
            start = round(start / step) * step
            if best is None or start < best[1]:
                best = (gid, start)
        if best is None or best[1] > horizon:
            unscheduled.append(r.well_id)
            continue
        gid, start = best
        free_at[gid] = start + r.inj_days
        events.append(dict(well_id=r.well_id, generator=gid, start_day=start, end_day=start + r.inj_days, steam_t=r.steam_t,
                           rate_tpd=r.rate_tpd, status="scheduled", delay_days=max(start - max(r.due_day, 0.0), 0.0),
                           due_day=r.due_day, optimal_day=r.optimal_day, value_per_t=r.value_per_t))
    busy = {gid: 0.0 for gid in free_at}
    for e in events:
        s, t = max(e["start_day"], 0.0), min(e["end_day"], horizon)
        if t > s:
            busy[e["generator"]] += t - s
    util = {gid: busy[gid] / horizon for gid in busy}
    total_steam = sum(e["steam_t"] for e in events if e["start_day"] < horizon)
    return dict(horizon_days=horizon, events=sorted(events, key=lambda e: e["start_day"]), utilization=util,
                unscheduled=unscheduled, total_steam_t=total_steam)
