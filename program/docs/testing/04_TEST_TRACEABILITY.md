> 0.8.3 presentation update, 28 October 2026: the original 501 tests were rerun
> with 37 added presentation tests (538 passed, 0 skipped). The dated mapping
> below remains the 0.8.2 baseline; its core/audio behavior is unchanged.
> New coverage is in `test_builder_review.py` (34 cases), three added GUI cases
> in `test_builder.py`, and extra immutability assertions in the existing real
> declared-range integration case. See `../UI_REPORT_0.8.3.md` and
> `../../reports/verification.md`.

# Test Plan Traceability

**Updated:** 28 October 2026
**Code:** version 0.8.2, scoring profile `default-v7`, commit `f148497`
(`source_revision` `sha256:90716ba4…b021`)
**Plans traced:** [01 Audio analysis](01_AUDIO_ANALYSIS_TEST_PLAN.md) (AU, AI),
[02 Scoring](02_SCORING_TEST_PLAN.md) (SU, SI),
[03 Program correctness](03_PROGRAM_CORRECTNESS_TEST_PLAN.md) (PC, plus §2.3 and §2.4)

This file maps every numbered case in the three test plans to the automated
tests that check it. Tests are named `file::Class.method`, relative to
`program/tests/`. Use it to answer "which test proves case X?" and to see
which cases are not yet automated.

## Run behind this table

CPython 3.13.7 on macOS, `REQUIRE_AUDIO_BACKENDS=1`,
`C2PA_SUNO_SAMPLE_DIR` set to the verified Suno downloads, run under
coverage.py as described in the README's "Coverage report" section:

- 496 non-GUI tests in one process (52 s): all passed, no skips.
- 5 `test_builder.GuiTests` cases, one process each: all passed.
- **Total: 501 tests, 501 passed, 0 skipped.**
- Coverage: 74.7% of lines, 65.0% of branches; 85.0% of lines without the
  policy-disabled `physical_audio_passes.py` and `audio_derivation.py`
  (`reports/coverage/`).

## Status key

| Status | Meaning |
|---|---|
| **Automated** | Each expected result in the plan is asserted by the listed test(s). |
| **Partial** | The behaviour is tested, but some of the plan's expected results, inputs or entry points are not asserted (see Notes). |
| **Manual** | Checked by a recorded manual run (`reports/verification.md`), not by the test suite. |
| **Gap** | No automated test asserts the expected result. |

## Summary

| Plan | Cases | Automated | Partial | Manual | Gap |
|---|---|---|---|---|---|
| AU: audio unit | 10 | 9 | 0 | 0 | 1 |
| AI: audio integration | 9 | 8 | 1 | 0 | 0 |
| SU: scoring unit | 25 | 25 | 0 | 0 | 0 |
| SI: scoring integration | 11 | 9 | 1 | 1 | 0 |
| PC: program correctness | 31 | 26 | 5 | 0 | 0 |
| **Total** | **86** | **77** | **7** | **1** | **1** |

Open items are listed in [Gaps and partial cases](#gaps-and-partial-cases).

---

## AU: audio analysis unit tests (plan 01, §2.1)

| ID | Case | Test(s) | Status | Notes |
|---|---|---|---|---|
| AU-01 | A04 alignment collapse | `test_audio_calibration.py::AlignmentGateTests.test_twelve_seconds_collapsed_to_one_timestamp_is_rejected`; `test_remediation.py::AudioRegressionTests.test_compressed_alignment_reports_ratio_and_beat_collapse` | Automated | Both reasons asserted. |
| AU-02 | A05 speed fit | `test_remediation.py::AudioRegressionTests.test_alignment_tempo_candidate_uses_known_time_mapping` | Automated | The test uses a known 1.12 mapping, not the plan's 1.2 example; it checks the same function. |
| AU-03 | Two speed estimators disagree | `test_audio_calibration.py::AlignmentGateTests.test_beat_octave_or_alias_error_cannot_release_a_speed` | Automated | `tempo_estimators_disagree`, no speed released. |
| AU-04 | Ambiguous transposition | `test_audio_calibration.py::AlignmentGateTests.test_pitch_tie_and_custom_features_abstain` | Automated | |
| AU-05 | Constant material is not split | `test_audio_calibration.py::AcousticBoundaryTests.test_constant_material_is_one_section_not_forced_sixteen_beats` | Automated | |
| AU-06 | Non-grid boundary survives small noise | `test_audio_calibration.py::AcousticBoundaryTests.test_non_grid_changes_and_small_feature_noise` | Automated | |
| AU-07 | Feature parameters outside calibration | `test_audio_calibration.py::AlignmentGateTests.test_pitch_tie_and_custom_features_abstain` | Automated | Same test as AU-04. |
| AU-08 | Calibration profile tampering | — | **Gap** | `test_audio_calibration.py:62` only checks that `freeze_sha256` is set. No test edits the profile and expects `ValueError("Audio calibration profile digest mismatch")` (`calibration.py:15`). |
| AU-09 | Range validation | `test_remediation.py::RangeRegressionTests.test_both_sides_reject_negative_nonfinite_reversed_zero_and_past_eof`, `.test_audio_reader_uses_the_same_end_tolerance_and_rejects_invalid_ranges` | Automated | |
| AU-10 | Report-only boundary | `test_audio_diagnostics.py::AudioPolicyTests.test_protocol_rejects_score_eligibility` | Automated | |

## AI: audio analysis integration tests (plan 01, §3.2)

| ID | Case | Test(s) | Status | Notes |
|---|---|---|---|---|
| AI-01 | 1.12x speed and +2 semitones | `test_audio_diagnostics.py::MusicSemanticsTests.test_final_version_is_recognised_through_transposition_tempo_and_timbre` | **Partial** | Transposition is asserted as exactly 2. Tempo is asserted to ±0.05, but the plan's tolerance is ±0.03. |
| AI-02 | 20 s excerpt from a 180 s source at 97.3 s | `test_audio_diagnostics.py::RealAudioBackendTests.test_long_source_excerpt_is_found_by_both_fingerprint_providers` | Automated | Both providers, ±0.15 s. |
| AI-03 | Declared crop mapped to the original timeline | `test_audio_diagnostics.py::RealAudioBackendTests.test_declared_crop_times_are_restored_to_original_file_timeline` | Automated | |
| AI-04 | Only `source_start_seconds` declared | `test_audio_diagnostics.py::RealAudioBackendTests.test_partial_ranges_are_not_silently_inferred` | Automated | |
| AI-05 | Silent file | `test_audio_diagnostics.py::RealAudioBackendTests.test_silence_is_unavailable_without_contradiction` | Automated | |
| AI-06 | Sustained single tone | `test_audio_calibration.py::CalibratedBackendTests.test_sustained_tone_does_not_force_a_transposition_or_tempo` | Automated | |
| AI-07 | Same source, two targets | `test_audio_calibration.py::CalibratedBackendTests.test_music_section_detection_is_exactly_target_independent`; `test_remediation.py::AudioRegressionTests.test_structure_of_identical_source_is_exactly_equal_for_different_targets` | Automated | |
| AI-08 | librosa missing | `test_audio_diagnostics.py::AudioIntegrationTests.test_missing_backend_is_unavailable_not_a_failed_scored_parser` | Automated | |
| AI-09 | Beat tracker returns zero beats | `test_audio_calibration.py::CalibratedBackendTests.test_low_beat_count_falls_back_to_time_grid_without_dividing_none` | Automated | |

## SU: scoring unit tests (plan 02, §2.1)

| ID | Case | Test(s) | Status | Notes |
|---|---|---|---|---|
| SU-01 | Weighted mean | `test_target.py::TargetTests.test_default_scoring_profile_reproduces_plain_average`, `.test_integrity_layers_carry_default_profile_weights` | Automated | Equal weights give the plain mean; skewed weights move it. |
| SU-02 | Fact de-duplication | `test_scoring_remediation.py::FactAggregationTests.test_any_failure_wins_regardless_of_copy_count_order_or_local_document_labels`; `test_scoring_remediation.py::ProductionScoringRegressionTests.test_good_and_bad_cosmetic_report_copies_cannot_move_integrity`; `test_remediation.py::EvidenceRegressionTests.test_duplicate_audit_covers_correct_and_incorrect_copies_without_mutation` | Automated | 1, 3, 10 and 30 copies. |
| SU-03 | Contradiction is not diluted | `test_scoring_remediation.py::FactAggregationTests.test_any_failure_wins_regardless_of_copy_count_order_or_local_document_labels` | Automated | `passed: False`, `conflicting_outcomes: True`. |
| SU-04 | Order independence | Same test as SU-03 | Automated | Reversed rows give the same `fact_id` and `passed`. |
| SU-05 | Renaming is not a new vote | `test_scoring_remediation.py::FactAggregationTests.test_renaming_cue_or_selection_does_not_add_votes` | Automated | |
| SU-06 | Different recordings stay different | `test_scoring_remediation.py::FactAggregationTests.test_different_recordings_remain_different_facts` | Automated | |
| SU-07 | Release gate, ordinary contradiction | `test_scoring_remediation.py::ReleaseAndDisclosureTests.test_ordinary_contradiction_does_not_release_previously_withheld_integrity` | Automated | |
| SU-08 | Severe cap lowers | `test_target.py::TargetTests.test_severe_contradiction_caps_the_weighted_mean` | Automated | `value_lowered: true`. |
| SU-09 | Cap is a ceiling | `test_target.py::TargetTests.test_severe_contradiction_cap_does_not_raise_an_already_low_value` | Automated | |
| SU-10 | Non-scoring finding | `test_scoring_remediation.py::ProductionScoringRegressionTests.test_embedded_metadata_can_release_integrity_with_a_non_scoring_origin_finding` | Automated | |
| SU-11 | Unavailable tier cannot promote | `test_scoring_remediation.py::FactAggregationTests.test_unavailable_verified_tier_cannot_promote_an_assessed_document_fact` | Automated | |
| SU-12 | Profile validation | `test_target.py::ScoringProfileTests` (`test_duplicate_json_key_is_rejected`, `test_unknown_layer_weight_is_rejected`, `test_non_positive_weight_is_rejected`, `test_negative_weight_is_rejected`, `test_non_finite_weight_is_rejected`, `test_severe_cap_out_of_range_is_rejected`, `test_severe_rules_without_cap_is_rejected`, `test_mismatched_denominator_layers_is_rejected`, and 10 more) | Automated | |
| SU-13 | Profile without severe release | `test_scoring_remediation.py::ReleaseAndDisclosureTests.test_profile_without_severe_release_reproduces_withheld_value` | Automated | |
| SU-14 | One vote per declared property | `test_target.py::TargetTests.test_file_and_relationship_claim_on_one_property_count_once` | Automated | |
| SU-15 | Technical claims leave file integrity | `test_target.py::TargetTests.test_technical_claims_do_not_replace_observed_properties`; `test_audio_formats.py::EvaluatorTests.test_mp3_unmeasurable_fields_are_unavailable_not_contradicted` | Automated | |
| SU-16 | One vote per repeated claim | `test_scoring_remediation.py::OneVotePerClaimTests.test_same_claim_in_two_documents_is_one_fact`, `.test_either_document_disagreeing_fails_the_merged_fact`, `.test_different_recordings_and_other_rules_are_never_merged`; `ProductionScoringRegressionTests.test_report_and_aaf_repeating_scene_take_and_tracks_count_once` | Automated | |
| SU-17 | Independence never guessed | `test_scoring_remediation.py::OneVotePerClaimTests.test_summary_separates_documents_facts_and_sources_and_never_guesses_independence` | Automated | |
| SU-18 | Breadth gate | `test_scoring_remediation.py::ProductionScoringRegressionTests.test_optional_share_withholds_for_missing_evidence_only`, `.test_contradicted_recording_counts_as_checked_not_missing` | Automated | |
| SU-19 | Profile validation (v6 keys) | `test_scoring_remediation.py::OneVotePerClaimTests.test_profile_flag_must_be_boolean`; `SupportBreadthTests.test_share_must_be_a_number_in_range` | Automated | |
| SU-20 | AAF take joins the denominator | `test_scoring_remediation.py::SupportBreadthTests.test_unsubmitted_aaf_take_joins_the_denominator_once` | Automated | |
| SU-21 | Unmatched reference is provisional | `test_scoring_remediation.py::SupportBreadthTests.test_unmatched_reference_is_provisional_when_a_submission_lacks_identity` | Automated | |
| SU-22 | No required recordings | `test_scoring_remediation.py::SupportBreadthTests.test_aaf_takes_do_not_apply_when_the_workflow_requires_no_recordings`, `.test_no_relevant_recordings_is_not_applicable` | Automated | |
| SU-23 | Format conversion claim | `test_format_conversion.py::ConversionEvaluationTests.test_lossless_conversion_checks_parameters_and_finds_identical_samples` | Automated | |
| SU-24 | Contradicted conversion | `test_format_conversion.py::ConversionEvaluationTests.test_a_changed_sample_rate_contradicts_the_conversion_claim` | Automated | |
| SU-25 | Sample identity is report-only | `test_format_conversion.py::ConversionEvaluationTests.test_different_samples_are_reported_not_scored` | Automated | |

## SI: scoring integration tests (plan 02, §3.1)

| ID | Case | Test(s) | Status | Notes |
|---|---|---|---|---|
| SI-01 | Correct sound report + BWF recorder WAV | `test_scoring_remediation.py::ProductionScoringRegressionTests.test_embedded_metadata_can_release_integrity_with_a_non_scoring_origin_finding`; `test_production_documents.py::ProductionDocumentTests.test_recorder_corroboration_releases_integrity_end_to_end` | Automated | |
| SI-02 | 1 / 3 / 10 / 30 cosmetic copies | `test_scoring_remediation.py::ProductionScoringRegressionTests.test_good_and_bad_cosmetic_report_copies_cannot_move_integrity` | Automated | Value, confidence and `check_count` unchanged. |
| SI-03 | Wrong start timecode | `test_production_documents.py::ProductionDocumentTests.test_shifted_report_timecode_is_a_recorder_contradiction`, `.test_timecode_mismatch_is_explained_not_capped`, `.test_contradiction_count_leads_the_integrity_reasoning` | Automated | |
| SI-04 | Declared output sample rate 44100 on a 22050 Hz file | `test_scoring_remediation.py::ReleaseAndDisclosureTests.test_ordinary_contradiction_does_not_release_previously_withheld_integrity` | Automated | |
| SI-05 | Source bytes tampered after hashing | `test_scoring_remediation.py::ReleaseAndDisclosureTests.test_severe_contradiction_releases_the_cap_without_qualifying_evidence`, `ProductionScoringRegressionTests.test_default_severe_ceiling_still_applies_when_other_qualifying_evidence_exists` | Automated | |
| SI-06 | Required evidence removed | `test_scoring_remediation.py::ReleaseAndDisclosureTests.test_ordinary_contradiction_does_not_release_previously_withheld_integrity` (baseline); `test_target.py::TargetTests.test_submitter_documents_alone_cannot_release_integrity`; `test_production_documents.py::ProductionDocumentTests.test_documents_without_recorder_match_keep_integrity_withheld` | Automated | |
| SI-07 | Explanation consistency | `test_target.py::TargetTests.test_every_axis_has_a_score_explanation`; `test_production_documents.py::ProductionDocumentTests.test_counts_survive_without_reasoning` | **Partial** | Checks that every axis has an explanation with the same `value`, on one request. Confidence and `integrity_layers` numbers are not compared, and the check is not run over every response. |
| SI-08 | Creation-method disclosure | `test_scoring_remediation.py::ReleaseAndDisclosureTests.test_complete_recorded_and_complete_ai_declarations_both_mean_complete_coverage` | Automated | `content_ai_detection_performed: false` asserted. |
| SI-09 | FLAC and MP3 layers | `test_audio_formats.py::EvaluatorTests.test_mp3_unmeasurable_fields_are_unavailable_not_contradicted`, `.test_flac_md5_mismatch_is_a_file_integrity_contradiction` | Automated | |
| SI-10 | Breadth on Project Rock Ep01 | — | **Manual** | Run on the client's Project Rock data, which is not in the repository; results in `reports/verification.md` (22 and 24 October). The same rules are unit-tested by SU-20 to SU-22. |
| SI-11 | File check not finished before the time limit | `test_sample_validation.py::EvaluationTests.test_time_limit_shows_incomplete_everywhere`; `IncompleteWithholdsReleaseTests.test_value_released_only_when_every_file_was_checked`; `ScanWavTests.test_time_limit_is_not_completed_never_a_pass` | Automated | |

## PC: program correctness (plan 03)

### Boundary tests (§2.1)

| ID | Case | Test(s) | Status | Notes |
|---|---|---|---|---|
| PC-01 | Audio on does not change the core result | `test_audio_diagnostics.py::AudioIntegrationTests.test_all_three_observations_append_without_changing_any_core_result` (via `assert_core_unchanged`); `RealAudioBackendTests.test_actual_three_worker_processes_preserve_core_axes`; `NonFinalVersionTests.test_demo_mix_that_differs_from_the_master_changes_nothing_in_the_core` | Automated | Called in process through `augment_response`, not through the CLI as in plan §1.1. |
| PC-02 | Audio analysis times out | `test_audio_diagnostics.py::AudioIntegrationTests.test_expired_budget_is_only_diagnostic_unavailability`, `.test_timeout_keeps_rows_finished_before_it` | Automated | `caller_timeout`; schema-valid response. |
| PC-03 | Worker crashes mid-run | `test_audio_diagnostics.py::AudioIntegrationTests.test_provider_crash_keeps_earlier_rows_and_its_stderr`, `.test_provider_start_failure_marks_only_that_task` | Automated | |
| PC-04 | Worker prints invalid JSON | `test_audio_diagnostics.py::AudioIntegrationTests.test_worker_invalid_json_is_diagnostic_only`, `.test_protocol_violation_inside_worker_is_structured_not_a_crash` | Automated | |
| PC-05 | Backend not installed | `test_audio_diagnostics.py::AudioIntegrationTests.test_missing_backend_is_unavailable_not_a_failed_scored_parser` | Automated | |
| PC-06 | Invalid audio policy file | `test_audio_diagnostics.py::AudioIntegrationTests.test_invalid_policy_does_not_turn_the_core_response_into_an_error` | Automated | |
| PC-07 | Audio leaking into Integrity | `test_audio_diagnostics.py::NonFinalVersionTests.test_demo_mix_that_differs_from_the_master_changes_nothing_in_the_core`; `AudioIntegrationTests.test_all_three_observations_append_without_changing_any_core_result`; `test_bandwidth.py::EvaluatorTests.test_bandwidth_rows_and_flags_do_not_change_scores`; `test_target.py::TargetTests.test_integrity_excludes_policy_disabled_physical_layer` | Automated | |
| PC-08 | Audio trying to lift a severe cap | `test_audio_diagnostics.py::AudioIntegrationTests.test_audio_cannot_release_a_severe_cap` | Automated | |
| PC-09 | File changes before the audio snapshot | `test_audio_diagnostics.py::AudioIntegrationTests.test_snapshot_rejects_content_changed_after_core_assessment` | Automated | |
| PC-10 | Object path escapes the bundle | `test_target.py::TargetTests.test_path_traversal_rejected`, `.test_symlink_escape_rejected_even_with_valid_filename`; `InputValidationTests.test_nonfinal_path_escape_is_isolated_without_opening_the_file`; `test_audio_diagnostics.py::AudioIntegrationTests.test_snapshot_rejects_path_escape` | Automated | |

### End-to-end input handling (§2.2)

| ID | Case | Test(s) | Status | Notes |
|---|---|---|---|---|
| PC-20 | `examples/valid-generated` | `test_builder.py::AssessmentTests.test_full_assessment_uses_real_worker_and_four_dimensions`; `test_builder.py::GuiTests.test_load_export_assess_and_save_result_end_to_end` | **Partial** | The example runs and succeeds, but no test compares the output with the stored `response.json`. |
| PC-21 | `examples/csec-wav` | `test_target.py::TargetTests.test_csec_wav_mapping_does_not_invent_music_roles`, `.test_csec_unknown_relationship_is_reported_while_wav_files_are_checked` | **Partial** | The CSEC mapping is tested on a synthetic bundle; the `examples/csec-wav` request itself is not run, and `C2PA_PARSER_UNAVAILABLE` without the SDK is not asserted. |
| PC-22 | Final hash mismatch | `test_target.py::TargetTests.test_modified_bytes_are_rejected` (runs `examples/invalid-hash`); `InputValidationTests.test_final_hash_mismatch_stops_and_reports_only_the_final` | Automated | |
| PC-23 | Unsupported final type | `test_target.py::TargetTests.test_csec_png_returns_unsupported_with_null_axes` (runs `examples/unsupported-png`) | Automated | |
| PC-24 | Non-final object missing | `test_target.py::TargetTests.test_missing_nonfinal_file_is_not_an_integrity_contradiction`, `.test_workflow_completeness_ignores_unrequired_missing_raw_object` | Automated | |
| PC-25 | Non-final WAV corrupted | `test_target.py::TargetTests.test_nonfinal_unsupported_wav_is_isolated_to_dependent_checks`, `.test_nonfinal_truncated_wav_fails_the_file_check`, `.test_nonfinal_wav_with_a_partial_final_frame_fails_the_file_check` | Automated | |
| PC-26 | FLAC / MP3 bytes saved as `.wav` | `test_audio_formats.py::EvaluatorTests.test_flac_named_wav_is_evaluated_as_flac`; `DetectionTests.test_containers_are_decided_by_content`; `test_target.py::InputValidationTests.test_final_malformed_flac_named_wav_is_read_as_flac`, `.test_final_ogg_named_wav_returns_unsupported`, `.test_other_audio_containers_are_unsupported_by_content` | Automated | |
| PC-27 | Misspelled field | `test_target.py::InputValidationTests.test_unknown_fields_skip_only_dependent_checks_with_suggestions`, `.test_misspelled_fade_is_not_turned_into_a_contradiction` | Automated | |
| PC-28 | Unknown non-final `artefact_type` | `test_target.py::TargetTests.test_unknown_nonfinal_artefact_is_quarantined_and_other_files_are_checked` | Automated | |
| PC-29 | Malformed JSON / dangling hash / duplicate node | `test_target.py::TargetTests.test_duplicate_nodes_are_not_silently_combined`, `.test_missing_reference_rejected`, `.test_cycle_is_rejected_by_library`, `.test_parallel_edges_are_not_silently_overwritten`; `InputValidationTests.test_edge_hash_case_mismatch_explains_the_dangling_reference` | **Partial** | Dangling hashes and duplicate nodes are covered. No test submits a request file that is not valid JSON, or asserts that the error has no stack trace. |
| PC-30 | Evaluation exceeds `options.timeout` | `test_target.py::TargetTests.test_timeout_terminates_an_actual_sleeping_worker`, `.test_requested_timeout_is_not_capped_at_sixty_seconds`; `test_builder.py::AssessmentTests.test_real_timeout_enforces_one_second_limit` | **Partial** | `execution_status: timeout` is asserted; the `TIMEOUT` finding (`run.py:178`) is not. |
| PC-31 | Size limits | `test_target.py::InputValidationTests.test_size_limits_skip_nonfinal_objects_but_stop_on_the_final`, `.test_default_size_limits_are_512_mib_and_1_gib`; `test_builder.py::ManifestLimitTests.test_builder_limits_match_the_evaluator` | Automated | |
| PC-32 | FLAC and MP3 finals, native and public | `test_audio_formats.py::PublicContractTests.test_public_bundles_accept_flac_and_mpeg_audio`; `EvaluatorTests.test_flac_md5_mismatch_is_a_file_integrity_contradiction`, `.test_mp3_unmeasurable_fields_are_unavailable_not_contradicted` | Automated | |
| PC-33 | Fake formats with `--audio-analysis` | `test_bandwidth.py::EvaluatorTests.test_bandwidth_rows_and_flags_do_not_change_scores`; `DetectorTests.test_upsampled_audio_stops_at_the_original_nyquist_frequency`; `AssessmentTests.test_mp3_content_band_is_compared_with_its_bitrate` | **Partial** | End to end only for a FLAC made from MP3-like audio; the 320 kbps MP3 and the upsampled 24/96 file are tested at detector level. |
| PC-34 | MP3 / plain FLAC edge to a BWF WAV | `test_audio_formats.py::EvaluatorTests.test_undated_audio_edge_is_not_comparable_rather_than_absent` | Automated | |
| PC-35 | Workflow policy loaded once | `test_scoring_remediation.py::SupportBreadthTests.test_engine_loads_the_workflow_policy_once`, `.test_workflow_recording_types_come_from_the_workflow_policy` | Automated | |
| PC-36 | STREAMINFO sample count ±1000 | `test_audio_formats.py::EvaluatorTests.test_wrong_streaminfo_sample_count_cannot_pass_the_md5_check` | Automated | |
| PC-37 | Mixed formats | `test_format_conversion.py::ConversionEvaluationTests.test_capabilities_advise_on_recordings_without_recorder_metadata` | Automated | |
| PC-38 | Unknown `transformation` | `test_format_conversion.py::ConversionEvaluationTests.test_unknown_transformation_value_is_an_input_error` | Automated | |
| PC-39 | WAV and FLAC of the same samples | `test_format_conversion.py::ConversionEvaluationTests.test_lossless_conversion_checks_parameters_and_finds_identical_samples`; `ConversionDiagnosticsTests.test_diagnostics_summarise_alignment_and_target_bandwidth` | Automated | `aligned_at_zero` asserted. |
| PC-40 | Format scope of FLAC and MP3 | `test_format_conversion.py::FormatScopeTests` (3 tests) | Automated | |

### Report consistency (§2.3, unnumbered)

| Check | Test(s) | Status |
|---|---|---|
| 1. Response validates against the schema | `validate(…, "scoring-response.schema.json")` in 11 test files, e.g. `test_target.py::TargetTests.test_cli_worker_returns_contract_valid_result` | Automated per test, not over one shared matrix |
| 2. No NaN / Infinity | `test_remediation.py::RangeRegressionTests.test_nonfinite_numbers_rejected_at_file_and_python_entry_points`; `test_sample_validation.py::EvaluationTests.test_time_limit_shows_incomplete_everywhere` (`allow_nan=False`) | Partial: not checked on every response |
| 3. Explanations agree with `axes` | See SI-07 | Partial |
| 4. Failed checks in explanations match failed facts | `test_c2pa_integrity.py::C2paValidationFailureTests.test_released_integrity_lists_the_facts_that_qualified_it`; `test_production_documents.py::ContradictionSummaryTests.test_counts_and_wording_name_each_contradicted_layer` | Partial |
| 5. `null` means not assessed | `test_builder.py::GuiTests.test_result_shows_null_as_not_assessed_and_preserves_failure_status`; `test_builder_results.py::AttentionDisplayTests.test_workflow_unknown_status_is_not_assumed_to_fail_or_pass` | Automated |
| 6. No overall score or verdict | `test_target.py::TargetTests.test_valid_graph_uses_library_and_exposes_four_observations`; `test_builder.py::AssessmentTests.test_full_assessment_uses_real_worker_and_four_dimensions` (`overall_score` absent) | Automated |

### C2PA, real integration (§2.4, unnumbered)

| Check | Test(s) | Status |
|---|---|---|
| Real signed file, valid | `test_c2pa_integrity.py::C2paValidationFailureTests.test_valid_manifest_reports_no_failure` (signed at test time); `SunoSampleTests` (3 real Suno downloads) | Automated |
| Asset modified after signing | `test_c2pa_integrity.py::C2paValidationFailureTests.test_audio_edited_after_signing_is_a_visible_ordinary_contradiction` | Automated |
| Untrusted certificate | `test_c2pa_integrity.py::PinnedTrustListTests.test_unknown_signer_scores_below_every_trusted_state`; `AsymmetricC2paTrustTests.test_self_signed_human_authorship_claim_cannot_release_integrity`; `SunoSampleTests.test_samples_are_recognised_as_suno_but_not_trusted` | Automated |
| Same files without the SDK: `C2PA_PARSER_UNAVAILABLE` | — | **Gap**: `c2pa-python` is a core requirement and `C2paRequirementTests` checks that it is installed. No test hides the SDK. |
| Supporting manifests cannot raise `attestation_strength` | `test_target.py::RestoredXmlHelperTests.test_attestation_axis_uses_final_artefact_not_strongest_supporting_node`; `test_c2pa_integrity.py::SunoKnownSignerTests.test_recognition_does_not_raise_attestation_strength` | Automated |

---

## Gaps and partial cases

Ordered by how much each would add, most first. None of them is a known
defect: the behaviour may be correct, but no test proves it.

1. **AU-08 (Gap).** Add a test that copies `audio_calibration_profile.json`,
   changes one value and expects `ValueError("Audio calibration profile digest mismatch")`.
2. **PC-20 (Partial).** Add a golden-file test: evaluate
   `examples/valid-generated` and compare with its `response.json`, ignoring
   timestamps, `processing_ms` and `source_revision`. Do the same for
   `csec-wav`, `invalid-hash` and `unsupported-png` (covers PC-21 too).
3. **C2PA without the SDK (Gap).** Patch the `c2pa` import to fail and assert
   `C2PA_PARSER_UNAVAILABLE` with attestation `unavailable`.
4. **PC-29 (Partial).** Run `run.py` on a request file that is not valid JSON
   and assert a clear `error` with no `Traceback` in stdout or stderr.
5. **PC-30 (Partial).** Assert the `TIMEOUT` finding in
   `test_timeout_terminates_an_actual_sleeping_worker`.
6. **SI-07 and §2.3 (Partial).** One shared helper that checks every response
   the suite produces: schema, no NaN/Infinity, explanation values and
   confidences equal to `axes`.
7. **AI-01 (Partial).** Tighten the tempo tolerance to the plan's ±0.03, or
   change the plan to ±0.05 and record why.
8. **PC-33 (Partial).** Add the 320 kbps MP3 and the upsampled 24/96 file to
   the end-to-end bandwidth test.
9. **SI-10 (Manual).** Project Rock cannot go in the repository. Keep the
   manual record in `reports/verification.md` up to date for each release.

## Tests outside the plans

About 340 of the 501 tests are not listed above. They cover areas the plans
do not number: workflow policies (16 workflows), DDEX RIN/ERN, MIDI, cue and
comp sheets, DAW sessions, AAF ranges, recorder timestamps, forge cost, the
desktop builder and its exports, local file references, benchmarks and the
legacy physical-similarity guards. The 31 `_legacy_*` methods in
`test_target.py` are disabled and not run.

## Keeping this file current

When a test plan gains a case, add a row here. When a test is renamed or
moved, update its row. Re-run the suite before changing a status.
