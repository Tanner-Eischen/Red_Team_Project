"""Statistics with tasks, rather than repeated attempts, as independent units."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from statistics import mean


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("Expected 0 <= successes <= total")
    if total == 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [0.0 if successes == 0 else max(0.0, center - half),
            1.0 if successes == total else min(1.0, center + half)]


def _bootstrap_means(values: list[float], seed: int, samples: int = 2000) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    estimates = sorted(mean(rng.choices(values, k=len(values))) for _ in range(samples))
    return [estimates[int(0.025 * samples)], estimates[min(samples - 1, int(0.975 * samples))]]


def summarize_trials(trials: list[dict], *, include_timeouts: bool = False,
                     expected_repeats: int | None = None, seed: int = 0) -> dict:
    counts = dict(sorted(Counter(trial["outcome"] for trial in trials).items()))
    eligible_status = {"PASS", "MODEL_FAIL"} | ({"AGENT_TIMEOUT"} if include_timeouts else set())
    eligible = [trial for trial in trials if trial["outcome"] in eligible_status]
    clusters: dict[str, list[int]] = defaultdict(list)
    for trial in eligible:
        clusters[trial["task_version_id"]].append(int(trial["outcome"] == "PASS"))
    task_rates = [mean(values) for values in clusters.values()]
    repeated = any(len(values) > 1 for values in clusters.values())
    successes = sum(trial["outcome"] == "PASS" for trial in eligible)
    interval = (_bootstrap_means(task_rates, seed) if repeated else wilson_interval(successes, len(eligible)))
    complete = []
    if expected_repeats is not None:
        if expected_repeats < 1:
            raise ValueError("Expected repeats must be positive")
        for task_id, values in clusters.items():
            # Errors or repeated retries must not silently make a partial cohort complete.
            task_trials = [trial for trial in trials if trial["task_version_id"] == task_id]
            final_by_repeat: dict[int, dict] = {}
            for trial in task_trials:
                index = int(trial.get("repeat_index", 0))
                if index not in final_by_repeat or int(trial.get("attempt_number", 0)) > int(
                    final_by_repeat[index].get("attempt_number", 0)
                ):
                    final_by_repeat[index] = trial
            if set(final_by_repeat) == set(range(expected_repeats)) and all(
                item["outcome"] in eligible_status for item in final_by_repeat.values()
            ):
                complete.append([int(item["outcome"] == "PASS") for item in final_by_repeat.values()])
    return {
        "attempts": len(trials), "outcome_counts": counts,
        "eligible_trials": len(eligible), "passes": successes,
        "excluded_trials": len(trials) - len(eligible),
        "eligible_tasks": len(clusters),
        "trial_pass_rate": successes / len(eligible) if eligible else None,
        "task_mean_pass_rate": mean(task_rates) if task_rates else None,
        "interval_95": interval,
        "interval_method": "task_cluster_percentile_bootstrap_2000" if repeated else "wilson",
        "independent_unit": "task_version", "include_timeouts": include_timeouts,
        "expected_repeats": expected_repeats, "complete_repeated_tasks": len(complete),
        "empirical_pass_at_k": mean(int(any(values)) for values in complete) if complete else None,
        "empirical_pass_power_k": mean(int(all(values)) for values in complete) if complete else None,
        "uncertainty_note": "Exploratory small sample" if len(clusters) < 20 else None,
        "task_rates": dict(sorted((task_id, mean(values)) for task_id, values in clusters.items())),
    }


def paired_comparison(a_trials: list[dict], b_trials: list[dict], *, include_timeouts: bool = False,
                      seed: int = 0) -> dict:
    """Compare matched task means; callers must match cohort, family and harness first."""
    for key in ("protocol", "cohort", "harness_profile_id"):
        a_values = {trial.get(key) for trial in a_trials}
        b_values = {trial.get(key) for trial in b_trials}
        if len(a_values | b_values) > 1:
            raise ValueError(f"Paired comparisons require matching {key}")
    a = summarize_trials(a_trials, include_timeouts=include_timeouts)["task_rates"]
    b = summarize_trials(b_trials, include_timeouts=include_timeouts)["task_rates"]
    shared = sorted(set(a) & set(b))
    differences = [a[task_id] - b[task_id] for task_id in shared]
    return {"paired_tasks": len(shared), "a_unpaired_tasks": len(set(a) - set(b)),
            "b_unpaired_tasks": len(set(b) - set(a)),
            "mean_paired_difference": mean(differences) if differences else None,
            "interval_95": _bootstrap_means(differences, seed),
            "interval_method": "paired_task_cluster_percentile_bootstrap_2000", "task_ids": shared}
