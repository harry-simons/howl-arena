"""Role-conditional Glicko-2 scoring and per-model stat aggregation.

Separate from the runner so historical games can be re-scored offline without
re-running models (PLAN architecture). Consumes stored GameRecord dicts (as
written by storage.save_record), so it never touches the engine or any model.

Scoring model (PLAN): three numbers per model — a WEREWOLF rating (updated only
on games played as wolf, win = wolves won), a VILLAGER rating (village-side
seats: villager/seer/healer, win = villagers won), and an OVERALL = 50/50 blend
of the two (not an independent ladder). Glicko-2 carries an uncertainty band
(RD), so the site can show "within noise" honestly.

Modelling note: Werewolf is a team game, not 1v1. This is a pragmatic first
adaptation — each game is one Glicko-2 match for each seat against a default
(1500/350) opposing field, win = your team's result, scored in a single rating
period. It differentiates models by win record and volume (RD shrinks with
games). A multi-period / opponent-strength-aware refinement is future work.

Alternative ladders (added for cross-checking — the site exposes them as sort
options alongside Glicko, not as a replacement): two more whole-player rating
systems are computed per model, both processed game-by-game in play order
(game_id order) as genuine two-team matches (the winning team beats the losing
team), which is closer to the truth than the default-field hack above:
  * ELO — a classic head-to-head ladder. Each seat is updated against the
    opposing team's average rating; win = team result.
  * TRUESKILL — Microsoft's Bayesian team skill (mu, sigma). Implemented here
    as the closed-form two-team, no-draw update (no external dependency, so the
    package stays self-contained). The published number is the conservative
    rating mu - 3*sigma, the standard TrueSkill leaderboard convention.
These are OVERALL (whole-player) ladders, deliberately complementing the
role-conditional Glicko: Glicko answers "how good as a wolf / as a villager",
Elo and TrueSkill answer "how good a player overall". Like Glicko they are
within-season (ratings reset per season — seasons are not comparable ladders).
"""

from __future__ import annotations

import math
from collections import defaultdict

SCALE = 173.7178
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOL = 0.06
TAU = 0.5
CONVERGENCE = 1e-6

# Below this many games in a role, a rating is "provisional" (not yet published
# as a firm rank). Glicko-2 RD also gates this; see PLAN statistical-honesty.
PROVISIONAL_GAMES = 15

# Village-side roles all feed the villager ladder (seer/healer are villagers for
# scoring — PLAN "special roles, unranked variants").
WOLF_ROLE = "werewolf"

# --- Elo (overall head-to-head team ladder) --------------------------------
ELO_START = 1500.0
ELO_K = 24.0  # update step; moderate so ~95 games/model in S1 settle, not thrash

# --- TrueSkill (overall Bayesian team skill) -------------------------------
TS_MU = 25.0
TS_SIGMA = TS_MU / 3.0       # 8.333 — initial uncertainty
TS_BETA = TS_SIGMA / 2.0     # 4.167 — per-game performance noise
TS_TAU = TS_SIGMA / 100.0    # 0.0833 — dynamics: skill may drift between games
TS_Z = 3.0                   # published rating = mu - 3*sigma (conservative skill)


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _expected(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def update_rating(rating: float, rd: float, vol: float, matches: list[tuple]):
    """Glicko-2 update for one player over a set of matches in a rating period.

    matches: list of (opponent_rating, opponent_rd, score) with score in {0,1}.
    Returns (new_rating, new_rd, new_vol). With no matches, RD inflates toward
    the default (rising uncertainty).
    """
    phi = rd / SCALE
    if not matches:
        phi_star = min(math.sqrt(phi * phi + vol * vol), DEFAULT_RD / SCALE)
        return rating, phi_star * SCALE, vol

    mu = (rating - DEFAULT_RATING) / SCALE
    v_inv = 0.0
    delta_sum = 0.0
    for opp_rating, opp_rd, score in matches:
        mu_j = (opp_rating - DEFAULT_RATING) / SCALE
        phi_j = opp_rd / SCALE
        gj = _g(phi_j)
        ej = _expected(mu, mu_j, phi_j)
        v_inv += gj * gj * ej * (1.0 - ej)
        delta_sum += gj * (score - ej)
    v = 1.0 / v_inv
    delta = v * delta_sum

    a = math.log(vol * vol)

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2.0 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / (TAU * TAU)

    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU
    fA, fB = f(A), f(B)
    while abs(B - A) > CONVERGENCE:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA = fA / 2.0
        B, fB = C, fC
    new_vol = math.exp(A / 2.0)

    phi_star = math.sqrt(phi * phi + new_vol * new_vol)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    new_mu = mu + new_phi * new_phi * delta_sum
    return new_mu * SCALE + DEFAULT_RATING, new_phi * SCALE, new_vol


def _elo_expected(rating: float, opp: float) -> float:
    """Classic Elo expected score of `rating` against `opp`."""
    return 1.0 / (1.0 + 10.0 ** ((opp - rating) / 400.0))


def _normal_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _ts_v_w(t: float) -> tuple[float, float]:
    """TrueSkill V/W truncation functions for a win (no draw margin).

    V = pdf(t)/cdf(t) is the mean update factor; W = V*(V+t) is the variance
    shrink factor. For an extreme upset (cdf -> 0) fall back to the asymptote so
    the update stays finite.
    """
    cdf = _normal_cdf(t)
    if cdf < 1e-10:
        v = -t
        return v, 1.0
    v = _normal_pdf(t) / cdf
    return v, v * (v + t)


def trueskill_update(winners: list[list], losers: list[list]) -> None:
    """One TrueSkill two-team match — `winners` beat `losers`, no draw.

    Each player is a mutable [mu, sigma]; updated IN PLACE (so passing the same
    list objects that live in a per-model dict updates that dict). Closed-form
    two-team solution (Herbrich et al.); avoids a full factor-graph dependency.
    """
    for p in winners + losers:
        p[1] = math.sqrt(p[1] * p[1] + TS_TAU * TS_TAU)  # dynamics
    n = len(winners) + len(losers)
    c2 = sum(p[1] * p[1] for p in winners + losers) + n * TS_BETA * TS_BETA
    c = math.sqrt(c2)
    t = (sum(p[0] for p in winners) - sum(p[0] for p in losers)) / c
    v, w = _ts_v_w(t)
    for p in winners:
        s2 = p[1] * p[1]
        p[0] += (s2 / c) * v
        p[1] = math.sqrt(s2 * max(1e-6, 1.0 - (s2 / c2) * w))
    for p in losers:
        s2 = p[1] * p[1]
        p[0] -= (s2 / c) * v
        p[1] = math.sqrt(s2 * max(1e-6, 1.0 - (s2 / c2) * w))


def assess_void(record) -> None:
    """Flag a degenerate game so it is excluded from ratings (PLAN Validity
    threat 6). A game is VOID if most turns failed (so the outcome is noise) or
    nothing actually happened (no deaths — a stalemate to max_rounds). Sets
    record.void / record.void_reason in place. A normal game (mostly accepted
    actions, real eliminations) is left scored.
    """
    actions = record.actions
    total = len(actions)
    if total == 0:
        record.void, record.void_reason = True, "no actions"
        return
    failed = sum(1 for a in actions if a.outcome.value != "accepted")
    deaths = sum(1 for e in record.events if e.kind in ("killed", "eliminated"))
    if failed / total > 0.5:
        record.void, record.void_reason = True, f"{failed/total:.0%} of turns failed"
    elif deaths == 0:
        record.void, record.void_reason = True, "no deaths (stalemate)"
    else:
        record.void, record.void_reason = False, None


def _blank_rating() -> dict:
    return {"rating": DEFAULT_RATING, "rd": DEFAULT_RD, "games": 0, "wins": 0,
            "provisional": True}


def score_games(games: list[dict]) -> dict:
    """Compute wolf/villager/overall ratings + behavioural stats per model.

    games: stored record dicts (one season's worth, or filtered). Void games are
    excluded from ratings but counted separately.
    Returns {model_id: {wolf, villager, overall, stats}}.
    """
    # Elo/TrueSkill are sequential (order-dependent); Glicko/stats are not.
    # Process in play order (game_id sorts as the seating scheduler emitted them)
    # so the two online ladders are deterministic and reproducible.
    games = sorted(games, key=lambda r: r.get("game_id") or "")

    wolf_matches: dict[str, list] = defaultdict(list)
    vil_matches: dict[str, list] = defaultdict(list)
    elo: dict[str, float] = defaultdict(lambda: ELO_START)
    ts: dict[str, list] = defaultdict(lambda: [TS_MU, TS_SIGMA])
    stats: dict[str, dict] = defaultdict(lambda: {
        "games": 0, "wolf_games": 0, "village_games": 0,
        "wolf_wins": 0, "village_wins": 0,
        "actions": 0, "refused": 0, "malformed": 0, "illegal": 0,
        "timeout": 0, "dead_votes": 0,
        "cost": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0,
        "void_games": 0, "providers": defaultdict(int), "quants": defaultdict(int),
    })

    for rec in games:
        result = rec.get("result")
        seat_models = {int(k): v for k, v in rec.get("seat_models", {}).items()}
        seat_roles = {int(k): v for k, v in rec.get("seat_roles", {}).items()}
        void = rec.get("void")
        winner = result.get("winner") if result else None

        for seat, model in seat_models.items():
            role = seat_roles.get(seat)
            st = stats[model]
            if void:
                st["void_games"] += 1
                continue
            st["games"] += 1
            if role == WOLF_ROLE:
                st["wolf_games"] += 1
                won = 1 if winner == "werewolves" else 0
                st["wolf_wins"] += won
                wolf_matches[model].append((DEFAULT_RATING, DEFAULT_RD, won))
            else:
                st["village_games"] += 1
                won = 1 if winner == "villagers" else 0
                st["village_wins"] += won
                vil_matches[model].append((DEFAULT_RATING, DEFAULT_RD, won))

        # Overall (whole-player) ladders: one two-team match per game in play
        # order. Skipped for void games and any game without a clean winner.
        if not void and winner in ("werewolves", "villagers"):
            wolves = [m for s, m in seat_models.items() if seat_roles.get(s) == WOLF_ROLE]
            village = [m for s, m in seat_models.items() if seat_roles.get(s) != WOLF_ROLE]
            if wolves and village:
                wolves_won = winner == "werewolves"
                win_team, lose_team = (wolves, village) if wolves_won else (village, wolves)
                # Elo: each seat vs the opposing team's average (snapshot first).
                opp_for_win = sum(elo[m] for m in lose_team) / len(lose_team)
                opp_for_lose = sum(elo[m] for m in win_team) / len(win_team)
                for m in win_team:
                    elo[m] += ELO_K * (1.0 - _elo_expected(elo[m], opp_for_win))
                for m in lose_team:
                    elo[m] += ELO_K * (0.0 - _elo_expected(elo[m], opp_for_lose))
                # TrueSkill: Bayesian two-team update (mutates ts entries in place).
                trueskill_update([ts[m] for m in win_team], [ts[m] for m in lose_team])

        # Behavioural signals from the audit trail (kept, not suppressed).
        for a in rec.get("actions", []) if not void else []:
            model = seat_models.get(a.get("seat_id"))
            if model is None:
                continue
            st = stats[model]
            st["actions"] += 1
            outcome = a.get("outcome")
            if outcome == "refused":
                st["refused"] += 1
            elif outcome == "malformed":
                st["malformed"] += 1
            elif outcome == "timeout":
                st["timeout"] += 1
            elif outcome == "illegal":
                st["illegal"] += 1
                if str(a.get("note", "")).startswith("target_dead"):
                    st["dead_votes"] += 1
            if a.get("provider"):
                st["providers"][a["provider"]] += 1
            if a.get("quant"):
                st["quants"][a["quant"]] += 1

        # Per-model cost from the recorded breakdown.
        for model, c in (rec.get("model_costs") or {}).items():
            st = stats[model]
            st["cost"] += c.get("total_cost", 0.0)
            st["input_tokens"] += c.get("input_tokens", 0)
            st["output_tokens"] += c.get("output_tokens", 0)
            st["calls"] += c.get("calls", 0)

    out: dict[str, dict] = {}
    models = set(stats) | set(wolf_matches) | set(vil_matches)
    for model in models:
        wolf = _blank_rating()
        if wolf_matches[model]:
            r, rd, _ = update_rating(DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOL, wolf_matches[model])
            wolf.update(rating=r, rd=rd, games=len(wolf_matches[model]),
                        wins=sum(m[2] for m in wolf_matches[model]),
                        provisional=len(wolf_matches[model]) < PROVISIONAL_GAMES)
        vil = _blank_rating()
        if vil_matches[model]:
            r, rd, _ = update_rating(DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOL, vil_matches[model])
            vil.update(rating=r, rd=rd, games=len(vil_matches[model]),
                       wins=sum(m[2] for m in vil_matches[model]),
                       provisional=len(vil_matches[model]) < PROVISIONAL_GAMES)

        overall_rating = 0.5 * wolf["rating"] + 0.5 * vil["rating"]
        overall_provisional = wolf["provisional"] or vil["provisional"]

        st = stats[model]
        games_played = st["games"]
        prov = games_played < PROVISIONAL_GAMES
        wins_total = st["wolf_wins"] + st["village_wins"]
        if games_played:
            elo_block = {"rating": round(elo[model], 1), "games": games_played,
                         "wins": wins_total, "provisional": prov}
            ts_mu, ts_sigma = ts[model]
            ts_block = {"rating": round(ts_mu - TS_Z * ts_sigma, 2),
                        "mu": round(ts_mu, 2), "sigma": round(ts_sigma, 2),
                        "games": games_played, "provisional": prov}
        else:  # only void games (or none) — leave at the unplayed defaults
            elo_block = {"rating": ELO_START, "games": 0, "wins": 0, "provisional": True}
            ts_block = {"rating": round(TS_MU - TS_Z * TS_SIGMA, 2), "mu": TS_MU,
                        "sigma": round(TS_SIGMA, 2), "games": 0, "provisional": True}

        providers = dict(st.pop("providers"))
        quants = dict(st.pop("quants"))
        out[model] = {
            "model": model,
            "wolf": wolf,
            "villager": vil,
            "overall": {"rating": round(overall_rating, 1), "provisional": overall_provisional},
            "elo": elo_block,
            "trueskill": ts_block,
            "stats": {**st, "providers": providers, "quants": quants,
                      "main_provider": max(providers, key=providers.get) if providers else None,
                      "main_quant": max(quants, key=quants.get) if quants else None},
        }
    return out
