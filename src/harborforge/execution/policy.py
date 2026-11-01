"""Pure observed-state classification; errors are never difficulty evidence."""

from __future__ import annotations


OBSERVED = {"all_pass", "mixed", "all_fail", "evidence_incomplete", "timed_out"}
AFTER_OBSERVATION = {"existing_child", "no_eligible_operator", "review_required", "limit_reached",
                     "child_created", "generation_failed", "no_semantic_change"}
PERMITTED = {
    "pending_quality": {"validated", "quality_failed", "validation_error", "human_review_required"},
    "validated": OBSERVED | {"limit_reached"},
    "quality_failed": {"quality_failed", "limit_reached"},
    "validation_error": {"validation_error", "limit_reached"},
    "all_pass": AFTER_OBSERVATION,
    "mixed": AFTER_OBSERVATION,
    "all_fail": AFTER_OBSERVATION,
}


def validate_transition(previous: str, state: str) -> None:
    if state not in PERMITTED.get(previous, set()):
        raise ValueError(f"Unsupported candidate transition: {previous} -> {state}")


def candidate_state(validation: dict, results: list[dict]) -> str:
    if not validation.get("valid"):
        return "validation_error" if validation.get("status") == "VALIDATION_ERROR" else "quality_failed"
    outcomes = [item["outcome"] for item in results]
    if not outcomes or any(value in {"PROVIDER_ERROR", "INFRA_ERROR", "INVALID_TASK", "UNSCORABLE"}
                           for value in outcomes):
        return "evidence_incomplete"
    if "AGENT_TIMEOUT" in outcomes:
        return "timed_out"
    if all(value == "PASS" for value in outcomes):
        return "all_pass"
    if all(value == "MODEL_FAIL" for value in outcomes):
        return "all_fail"
    return "mixed"


def outcome_signature(results: list[dict]) -> tuple:
    """Matched profile/outcome counts detect unchanged observations, not causal progress."""
    return tuple(sorted((item["model_profile_id"], item["repeat_index"], item["outcome"])
                        for item in results))


def reviewed_evidence(registry, task_id: str, finding_ids: tuple[str, ...]) -> list[dict]:
    """Only latest, append-only confirmed reviews of this exact task may guide mutation."""
    result = []
    for identifier in finding_ids:
        finding = registry.connection.execute(
            "SELECT f.finding_id,f.trial_id,f.category,t.task_version_id,t.outcome "
            "FROM failure_findings f JOIN trials t USING(trial_id) WHERE f.finding_id=?", (identifier,)
        ).fetchone()
        if finding is None or finding["task_version_id"] != task_id or finding["outcome"] != "MODEL_FAIL":
            continue
        review = registry.connection.execute(
            "SELECT * FROM finding_reviews WHERE finding_id=? ORDER BY rowid DESC LIMIT 1",
            (identifier,),
        ).fetchone()
        if review and review["decision"] == "confirmed" and review["reviewer"].strip():
            result.append({"finding_id": identifier, "trial_id": finding["trial_id"],
                           "category": finding["category"], "decision": "confirmed",
                           "reviewer": review["reviewer"], "review_id": review["review_id"],
                           "counterevidence_reference": review["review_id"]})
    return result


def run_discovery(config, paths, output, registry, quality, attempt, stop_requested, summary):
    """Schema-2 controller: bounded transitions, immutable children and matched retests."""
    import json
    from harborforge.authoring.mutations import NoSemanticChange, revise_task, select_mutation
    from harborforge.authoring.provenance import read_manifest
    from harborforge.storage.registry import utc_now

    policy = config.discovery_policy
    if policy is None:
        raise ValueError("An explicit schema-2 policy is required")
    summary.update(policy_version=policy.policy_version, candidate_transitions=[],
                   generation_attempts=0, exploration_promotions=0)
    previous_states = {}
    children = {}
    active = paths
    tiers = sorted({model.tier for model in config.model_profiles})

    def transition(manifest, tier, state, action, reason, results=(), **details):
        key = (manifest["task_version_id"], tier)
        previous = previous_states.get(key, "pending_quality")
        validate_transition(previous, state)
        record = {"sequence": len(summary["candidate_transitions"]) + 1,
                  "policy_version": policy.policy_version, "task_version_id": key[0], "tier": tier,
                  "from_state": previous, "state": state,
                  "action": action, "reason": reason,
                  "triggering_trial_ids": [item["trial_id"] for item in results],
                  "recorded_utc": utc_now(), **details}
        previous_states[key] = state
        # Durable append-only content artifact survives controller interruption.
        encoded = (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode()
        stored = registry.store_artifact(encoded, artifact_type="policy_transition", visibility="private")
        summary["candidate_transitions"].append({**record, "artifact_sha256": stored["sha256"]})
        with (output / "candidate-transitions.jsonl").open("ab") as stream:
            stream.write(encoded)
        for result in results:
            registry.append_event(result["trial_id"], "candidate_transition", record)

    for tier in tiers:
        if stop_requested():
            break
        tier_models = [model for model in config.model_profiles if model.tier == tier]
        next_tier = []
        tier_attempts = 0
        seen = set()
        for root in active:
            path = root
            previous_signature = None
            no_progress = 0
            promote_lineage = False
            explored_lineage = False
            while not stop_requested():
                manifest, validation, validation_id = quality(path)
                task_id = manifest["task_version_id"]
                if task_id in seen:
                    break
                seen.add(task_id)
                if tier == 3 and config.promotion.require_human_review_for_tier3 and \
                        task_id not in config.promotion.human_reviewed_task_ids:
                    transition(manifest, tier, "human_review_required", "stop_candidate",
                               "Tier 3 requires explicit human task review")
                    break
                transition(manifest, tier, "validated" if validation.get("valid") else candidate_state(validation, []),
                           "run_registered_attempts" if validation.get("valid") else "quarantine",
                           "Exact task quality evidence recorded before model execution")
                results = []
                for model in tier_models:
                    for repeat in range(model.repeats):
                        for retry in range(config.budgets.retry_provider_errors + 1):
                            if stop_requested():
                                break
                            if tier_attempts >= policy.max_attempts_per_tier:
                                summary.update(status="policy_stopped", stop_reason="per-tier attempt ceiling reached")
                                transition(manifest, tier, "limit_reached", "stop_campaign", summary["stop_reason"], results)
                                return
                            result = attempt(path, manifest, validation, validation_id, model, repeat, retry)
                            if result is not None:
                                results.append(result)
                                tier_attempts += 1
                            if result is None or result["outcome"] != "PROVIDER_ERROR":
                                break
                        if stop_requested():
                            break
                    if stop_requested():
                        break
                if stop_requested():
                    transition(manifest, tier, "limit_reached", "stop_campaign",
                               summary.get("stop_reason") or "Campaign reservation stopped execution", results)
                    return
                state = candidate_state(validation, results)
                failures = [item for item in results if item["outcome"] == "MODEL_FAIL"]
                informative = state in {"mixed", "all_fail"} and \
                    len(failures) >= config.promotion.minimum_model_failures
                if informative:
                    promote_lineage = True
                elif state == "all_pass" and tier != tiers[-1] and not explored_lineage and \
                        summary["exploration_promotions"] < policy.exploration_quota:
                    promote_lineage = explored_lineage = True
                    summary["exploration_promotions"] += 1
                transition(manifest, tier, state, "consider_bounded_revision" if state in {"all_pass", "mixed", "all_fail"}
                           else "stop_candidate", "Observed outcomes; no universal model ranking or causal claim", results,
                           eligible_for_confirmation=informative, exploration_selected=explored_lineage,
                           exploration_rule="stable configured task order within preregistered quota")
                summary["promotions"].append({"task": task_id, "tier": tier,
                    "eligible": informative or explored_lineage, "failures": len(failures), "attempts": len(results)})
                if state not in {"all_pass", "mixed", "all_fail"}:
                    break
                signature = outcome_signature(results)
                no_progress = no_progress + 1 if signature == previous_signature else 0
                previous_signature = signature
                # Revisiting a selected lineage tests its existing child under this
                # same tier/harness, even if the parent now passes. Never regenerate.
                if task_id in children:
                    transition(manifest, tier, "existing_child", "matched_retest", "Reuse immutable child", results,
                               child_task_version_id=read_manifest(children[task_id])["task_version_id"])
                    path = children[task_id]
                    continue
                limit = ("lineage revision ceiling reached" if manifest["version"] - 1 >= policy.max_revisions_per_lineage
                         else "total revision ceiling reached" if len(summary["revisions"]) >= policy.max_revisions_total
                         else "generation attempt ceiling reached" if summary["generation_attempts"] >= policy.max_generation_attempts
                         else "no-progress ceiling reached" if no_progress >= policy.max_no_progress else None)
                if limit:
                    transition(manifest, tier, "limit_reached", "freeze_observations", limit, results)
                    break
                evidence = reviewed_evidence(registry, task_id, policy.reviewed_finding_ids) if informative else []
                selection = select_mutation(manifest, mode="all_pass" if state == "all_pass" else "reviewed_failure",
                                            all_pass_operator_ids=policy.all_pass_operator_ids, reviewed_evidence=evidence)
                if selection is None:
                    transition(manifest, tier, "no_eligible_operator" if state == "all_pass" else "review_required",
                               "freeze_observations", "No eligible preregistered dimension or confirmed task-specific evidence",
                               results)
                    break
                summary["generation_attempts"] += 1
                try:
                    child = revise_task(path, output / "revisions", mutation=selection["operator_id"],
                                        max_iterations=policy.max_revisions_per_lineage, selection=selection)
                except NoSemanticChange as error:
                    transition(manifest, tier, "no_semantic_change", "freeze_observations", str(error), results,
                               operator_id=selection["operator_id"], difficulty_measured=False)
                    break
                except (OSError, ValueError) as error:
                    summary.update(status="generation_error", stop_reason=f"{type(error).__name__}: {error}")
                    transition(manifest, tier, "generation_failed", "stop_campaign", summary["stop_reason"], results)
                    return
                child_manifest = read_manifest(child)
                registry.register_task(child_manifest, child)
                children[task_id] = child
                revision = {"parent": task_id, "child": child_manifest["task_version_id"],
                            "evidence_trials": [item["trial_id"] for item in results], "selection": selection,
                            "difficulty_measured": False}
                summary["revisions"].append(revision)
                transition(manifest, tier, "child_created", "revalidate_before_retest",
                           "Unmeasured hypothesis; every child receives fresh exact-hash quality controls", results, **revision)
                path = child
            if promote_lineage:
                next_tier.append(root)
            if stop_requested():
                break
        active = list(dict.fromkeys(next_tier))
