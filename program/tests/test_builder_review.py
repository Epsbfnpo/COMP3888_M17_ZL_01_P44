"""Presentation regression matrix: the display never changes the assessment contract."""
import copy
import json
from pathlib import Path
import sys
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from builder_review import build_review_presentation, review_text
from builder_report import render_review_html

SOURCE, TARGET = 'a' * 64, 'b' * 64
NON_SCORING = {'scoring_eligible': False, 'integrity_contribution': None, 'content_match_is_provenance': False}


def response(outcome='content_found_elsewhere'):
    row = dict(source_hash=SOURCE, target_hash=TARGET, relationship_type='excerpted_from', selection_id=None,
               declared_source_range_seconds=[7, 11], declared_target_range_seconds=[0, 4],
               best_source_range_seconds=[2, 6], search_supported_offsets_seconds=[2, 10],
               spectromap_agrees=True, diagnostic_ids=['task:audfprint', 'task:spectromap'],
               outcome=outcome, **NON_SCORING)
    return {'case_id': 'CASE', 'run_id': 'RUN', 'execution_status': 'succeeded',
            'system': {'version': '0.8.3'}, 'axes': {
                'integrity': {'value': 1.0, 'confidence': .427, 'status': 'consistent', 'availability': 'available'},
                'completeness': {'value': .5, 'confidence': .9, 'availability': 'partial'}},
            'findings': [{'code': 'AUDIO_DECLARED_RANGE_NOT_SUPPORTED', 'detail_key': 'audio_declared_range',
                          'severity': 'low', **copy.deepcopy(row)}],
            'assessment_checks': {
                'audio_declared_range': [row], 'integrity_decision': [{'contradiction_count': 0}],
                'audio_diagnostic_summary': [{'enabled': True, 'completed_provider_tasks': 2,
                                               'unavailable_provider_tasks': 0}],
                'audio_fingerprint': [dict(diagnostic_id='task:audfprint', provider='audfprint',
                    source_hash=SOURCE, target_hash=TARGET, relation_id='r', status='inconclusive', matches=[]),
                    dict(diagnostic_id='task:spectromap', provider='spectromap', source_hash=SOURCE,
                         target_hash=TARGET, relation_id='r', status='inconclusive', matches=[])],
                'support_breadth': [{'applicable': True, 'required_recording_count': 13,
                    'supported': 1, 'contradicted': 0, 'coverage_scope': 'confirmed',
                    'assets': [{'bound': True} for _ in range(13)]}]}}


class ReviewPresentationTests(unittest.TestCase):
    def model(self, r=None, **kw):
        return build_review_presentation(r if r is not None else response(),
                                         {SOURCE: 'source.wav', TARGET: 'mix.wav'}, **kw)

    def test_summary_first_and_score_unchanged_with_elsewhere_finding(self):
        r = response(); before = copy.deepcopy(r)
        model = self.model(r); text = review_text(model); render_review_html(model)
        self.assertEqual(r, before)
        self.assertEqual(model['raw_response'], before)
        self.assertEqual(model['counts']['audio_review'], 1)
        self.assertEqual(model['axes'][0]['label'], 'Core Evidence Consistency')
        self.assertEqual(model['axes'][0]['key'], 'integrity')
        self.assertEqual(model['axes'][0]['value'], '1.000')
        self.assertIn('1 audio content finding(s) require review', text)
        self.assertLess(text.index('Declared interval needs review'), text.index('Four separate dimensions'))
        self.assertNotIn('No issues requiring attention', text)

    def test_model_is_detached_even_if_a_caller_edits_it(self):
        r = response(); before = copy.deepcopy(r); model = self.model(r)
        model['raw_response']['axes']['integrity']['value'] = 0
        model['items'][0]['raw'][0]['outcome'] = 'changed'
        self.assertEqual(r, before)

    def test_all_axes_value_confidence_status_and_non_audio_findings_preserved(self):
        r = response(); core = copy.deepcopy(r)
        core['assessment_checks'].pop('audio_declared_range'); core['findings'] = []
        for current in (core, r):
            current['findings'].append({'code': 'CORE_RECORD', 'message': 'unchanged'})
        self.model(core); self.model(r)
        self.assertEqual(r['axes'], core['axes'])
        self.assertEqual([f for f in r['findings'] if f['code'] == 'CORE_RECORD'], core['findings'])
        self.assertFalse(r['assessment_checks']['audio_declared_range'][0]['scoring_eligible'])
        self.assertIsNone(r['assessment_checks']['audio_declared_range'][0]['integrity_contribution'])
        self.assertFalse(self.model(r)['items'][0]['affects_integrity'])

    def test_detailed_row_and_linked_finding_count_once_with_both_references(self):
        item = self.model()['items'][0]
        self.assertEqual(len(self.model()['items']), 1)
        self.assertEqual(item['references'], ['audio_declared_range[0]', 'findings[0]'])
        self.assertEqual(item['diagnostic_ids'], ['task:audfprint', 'task:spectromap'])
        self.assertEqual(item['severity'], 'low')

    def test_duplicate_presentations_of_same_row_count_once(self):
        r = response(); r['assessment_checks']['audio_declared_range'] *= 2
        self.assertEqual(self.model(r)['counts']['audio_review'], 1)

    def test_distinct_windows_on_same_files_do_not_merge(self):
        r = response(); second = copy.deepcopy(r['assessment_checks']['audio_declared_range'][0])
        second.update(declared_source_range_seconds=[17, 21], diagnostic_ids=['task2:audfprint'])
        r['assessment_checks']['audio_declared_range'].append(second)
        self.assertEqual(self.model(r)['counts']['audio_review'], 2)

    def test_conflicting_windows_are_not_merged_even_with_reused_diagnostic_ids(self):
        r = response(); second = copy.deepcopy(r['assessment_checks']['audio_declared_range'][0])
        second['declared_source_range_seconds'] = [17, 21]
        r['assessment_checks']['audio_declared_range'].append(second)
        self.assertEqual(self.model(r)['counts']['audio_review'], 2)

    def test_distinct_selections_do_not_merge(self):
        r = response(); second = copy.deepcopy(r['assessment_checks']['audio_declared_range'][0])
        second.update(selection_id='clip-2', diagnostic_ids=['clip-2:audfprint'])
        r['assessment_checks']['audio_declared_range'].append(second)
        self.assertEqual(self.model(r)['counts']['audio_review'], 2)

    def test_missing_identity_does_not_merge_independent_unknown_rows(self):
        r = response(); r['findings'] = []
        r['assessment_checks']['audio_declared_range'] = [{'outcome': 'new_state'}, {'outcome': 'new_state'}]
        model = self.model(r)
        self.assertEqual(len(model['items']), 2)
        self.assertNotEqual(model['items'][0]['id'], model['items'][1]['id'])
        # Identical intervals without task IDs can belong to repeated relationships.
        # Never assume two independent detail rows are one diagnostic task.
        row = response()['assessment_checks']['audio_declared_range'][0]
        row.pop('diagnostic_ids')
        r['assessment_checks']['audio_declared_range'] = [copy.deepcopy(row), copy.deepcopy(row)]
        self.assertEqual(self.model(r)['counts']['audio_review'], 2)

    def test_structured_outcome_takes_precedence_over_english_message(self):
        r = response('content_not_located'); r['findings'][0]['message'] = 'Everything is verified.'
        model = self.model(r)
        self.assertEqual(model['counts']['audio_review'], 0)
        self.assertEqual(model['counts']['audio_unresolved'], 1)
        self.assertIn('inconclusive', model['items'][0]['title'])

    def test_search_unavailable_retains_local_observation_and_gap(self):
        model = self.model(response('search_unavailable'))
        self.assertEqual(model['counts']['audio_gap'], 1)
        self.assertEqual(model['counts']['audio_review'], 0)
        self.assertIn('wider search did not complete', model['items'][0]['explanation'])
        self.assertIn('interval assessment(s) could not conclude', model['summary'][2])

    def test_insufficient_signal_and_no_positive_control_are_not_contradictions(self):
        for state in ('insufficient_support', 'no_positive_control'):
            model = self.model(response(state))
            self.assertEqual(model['counts']['audio_unresolved'], 1)
            self.assertEqual(model['counts']['core'], 0)
            self.assertEqual(model['counts']['audio_review'], 0)

    def test_unavailable_states_are_gaps_not_passes(self):
        for state in ('not_assessed', 'not_assessed_durations_differ', 'not_run', 'unavailable'):
            model = self.model(response(state))
            self.assertEqual(model['counts']['audio_gap'], 1)
            self.assertEqual(model['counts']['audio_review'], 0)

    def test_supported_range_is_a_supporting_detail_not_review_count(self):
        model = self.model(response('declared_range_supported'))
        self.assertEqual(model['counts']['support'], 1)
        self.assertEqual(model['counts']['audio_review'], 0)
        self.assertIn('Declared interval supported', review_text(model, include_support=True))

    def test_unknown_outcome_retained_and_not_assumed_pass_or_failure(self):
        model = self.model(response('new_outcome'))
        self.assertEqual(model['counts']['unknown'], 1)
        self.assertIn('new_outcome', model['items'][0]['title'])
        self.assertIn('not been interpreted as a pass or failure', model['items'][0]['explanation'])

    def test_legacy_finding_without_detail_still_shown(self):
        r = response(); r['assessment_checks'].pop('audio_declared_range')
        self.assertEqual(self.model(r)['counts']['audio_review'], 1)
        self.assertIn('7.00–11.00 s', self.model(r)['items'][0]['explanation'])

    def test_missing_summary_distinguishes_disabled_requested_and_unknown(self):
        r = response(); r['assessment_checks'].pop('audio_diagnostic_summary')
        for requested, phrase in ((False, 'not enabled'), (True, 'no diagnostic result returned'), (None, 'run state unknown')):
            self.assertIn(phrase, self.model(r, audio_requested=requested)['summary'][0])

    def test_provider_failure_keeps_core_execution_separate(self):
        r = response(); r['assessment_checks']['audio_diagnostic_summary'][0].update(
            reason_code='deadline', message='Time budget exhausted.')
        model = self.model(r)
        self.assertIn('Core evaluation completed', model['summary'][0]); self.assertIn('not completed', model['summary'][0])
        self.assertIn('Time budget exhausted', model['summary'][0])

    def test_high_numeric_score_does_not_hide_core_contradiction(self):
        r = response(); r['axes']['integrity'].update(value=.994, status='contradicted')
        r['assessment_checks']['integrity_decision'][0]['contradiction_count'] = 1
        model = self.model(r)
        self.assertIn('1 core contradiction', model['summary'][1])
        self.assertEqual(model['axes'][0]['value'], '0.994 (contradicted: 1)')

    def test_null_with_contradiction_remains_null(self):
        r = response(); r['axes']['integrity'].update(value=None, status='contradicted')
        r['assessment_checks']['integrity_decision'][0].update(contradiction_count=1, numeric_release='withheld')
        model = self.model(r)
        self.assertIn('withheld', model['summary'][1]); self.assertIn('1 core contradiction', model['summary'][1])
        self.assertIn('Not assessed', model['axes'][0]['value'])
        self.assertNotIn('0.000', model['axes'][0]['value'])

    def test_incomplete_keeps_contradiction_count_and_missing_scan(self):
        r = response(); r['axes']['integrity'].update(value=None, status='incomplete')
        r['assessment_checks']['integrity_decision'][0].update(contradiction_count=1, not_completed_count=2)
        text = self.model(r)['summary'][1]
        self.assertIn('1 core contradiction', text); self.assertIn('2 file check(s) incomplete', text)

    def test_coverage_one_of_thirteen_stays_visible_near_summary(self):
        model = self.model(); self.assertIn('1/13', model['summary'][3])
        self.assertIn('12 lack qualifying checks', model['summary'][3])
        self.assertIn('13/13', '\n'.join(model['coverage']))

    def test_checked_includes_contradictions_not_just_support(self):
        r = response(); r['assessment_checks']['support_breadth'][0].update(supported=1, contradicted=2)
        text = '\n'.join(self.model(r)['coverage'])
        self.assertIn('3/13 relevant source files have qualifying cross-evidence checks (1 consistent; 2 contradicted)', text)
        self.assertIn('not a pass rate', text)

    def test_provisional_coverage_does_not_assert_missing_material(self):
        r = response(); r['assessment_checks']['support_breadth'][0].update(coverage_scope='provisional', unmatched_references=12)
        text = '\n'.join(self.model(r)['coverage'])
        self.assertIn('Provisional coverage: 12', text); self.assertIn('do not assume all were omitted', text)

    def test_coverage_unknown_is_not_zero_and_not_applicable_is_distinct(self):
        r = response(); r['assessment_checks'].pop('support_breadth')
        self.assertIn('unknown', self.model(r)['coverage'][0])
        r['assessment_checks']['support_breadth'] = [{'applicable': False}]
        self.assertIn('not applicable', self.model(r)['coverage'][0])

    def test_multiple_candidates_and_spectromap_agreement_retained(self):
        text = self.model()['items'][0]['explanation']
        for phrase in ('7.00–11.00 s', '0.00–4.00 s', '2.00–6.00 s', '10.00–14.00 s',
                       'no unique historical position', 'SpectroMap:', 'audfprint'):
            self.assertIn(phrase, text)

    def test_spectromap_false_and_unknown_are_not_claimed_as_agreement(self):
        for value, phrase in ((False, 'did not corroborate'), (None, 'not assessed')):
            r = response(); r['assessment_checks']['audio_declared_range'][0]['spectromap_agrees'] = value
            self.assertIn(phrase, self.model(r)['items'][0]['explanation'])

    def test_without_reasoning_essential_states_still_visible(self):
        text = review_text(self.model())
        for phrase in ('1.000', '7.00–11.00 s', '1/13', 'no change to any score', 'Reasoning not supplied'):
            self.assertIn(phrase, text)

    def test_html_escapes_external_names_messages_and_has_no_remote_resources(self):
        r = response(); r['findings'][0]['message'] = '<script>alert(1)</script>'
        model = build_review_presentation(r, {SOURCE: '<img src=x onerror=bad>', TARGET: 'mix.wav'})
        html = render_review_html(model)
        self.assertIn('&lt;img src=x onerror=bad&gt;', html)
        self.assertNotIn('<script>', html); self.assertNotIn('<img ', html)
        self.assertNotIn('<link ', html); self.assertNotIn('src="http', html)
        self.assertIn('@media print', html)
        self.assertLess(html.index('Declared interval needs review'), html.index('Four separate dimensions'))

    def test_html_text_and_gui_model_share_counts_scope_and_references(self):
        model = self.model(); text = review_text(model); html = render_review_html(model)
        for phrase in ('1 audio content finding(s) require review', '1/13 relevant source files have qualifying cross-evidence checks',
                       'Core Evidence Consistency', 'task:audfprint', '0.8.3'):
            self.assertIn(phrase, text); self.assertIn(phrase, html)

    def test_raw_json_export_has_no_presentation_fields(self):
        r = response(); self.model(r)
        self.assertNotIn('summary', r); self.assertNotIn('review_items', r)
        self.assertNotIn('Core Evidence Consistency', r['axes'])
        self.assertIn('integrity', json.loads(json.dumps(r))['axes'])

    def test_missing_names_use_hash_and_do_not_invent_filenames(self):
        model = build_review_presentation(response())
        self.assertIn('aaaaaaaaaaaa', model['items'][0]['affected'])
        self.assertNotIn('source.wav', model['items'][0]['affected'])

    def test_core_audio_file_failure_is_not_classified_as_optional_audio(self):
        r = response(); r['assessment_checks']['bound_file_integrity'] = [
            {'rule': 'audio_samples_valid', 'passed': False, 'evidence_hash': SOURCE, 'reason': 'NaN sample.'}]
        model = self.model(r)
        self.assertEqual(model['items'][0]['group'], 'core')
        self.assertNotEqual(model['items'][0]['affects_integrity'], False)

    def test_execution_error_remains_visible_in_important_items_and_export(self):
        r = response(); r.update(execution_status='error', error='evaluation failed')
        model = self.model(r)
        self.assertEqual(model['items'][0]['id'], 'execution-detail')
        self.assertIn('evaluation failed', model['items'][0]['explanation'])
        self.assertIn('evaluation failed', render_review_html(model))


class FiveSectionSummaryTests(unittest.TestCase):
    def model(self, r=None, **options):
        return build_review_presentation(response() if r is None else r, **options)

    def section(self, model, title):
        return next(s for s in model['summary_sections'] if s['title'] == title)

    def test_five_sections_are_ordered_identically_in_text_and_html(self):
        model = self.model(); titles = ['Evaluation Status', 'Core Evidence Consistency', 'Review Required',
                                       'Coverage & Limitations', 'Other Key Results']
        self.assertEqual([s['title'] for s in model['summary_sections']], titles)
        self.assertEqual(len(model['summary']), 5)
        text, html = review_text(model), render_review_html(model)
        positions = [text.index(title + ':') for title in titles]
        self.assertEqual(positions, sorted(positions))
        positions = [html.index('<h3>' + title.replace('&', '&amp;') + '</h3>') for title in titles]
        self.assertEqual(positions, sorted(positions))

    def test_core_scope_names_checks_and_excludes_optional_audio_analysis(self):
        model = self.model(); scope = self.section(model, 'Core Evidence Consistency')['scope']
        for phrase in ('File identity and readability', 'technical properties and relationships',
                       'cross-evidence consistency', 'Optional audio analysis', 'does not affect this score'):
            self.assertIn(phrase, scope)
            self.assertIn(phrase, review_text(model))
            self.assertIn(phrase, render_review_html(model))

    def test_provider_limit_does_not_become_zero_interval_analysis_gaps(self):
        r = response(); r['assessment_checks']['audio_diagnostic_summary'][0].update(
            completed_provider_tasks=9, unavailable_provider_tasks=1)
        r['assessment_checks']['audio_semantics'] = [{'provider': 'music_semantics', 'status': 'unavailable',
            'reason_code': 'insufficient_signal', 'message': 'Too little audio for beat-level harmony and form.'}]
        model = self.model(r); status = self.section(model, 'Evaluation Status')['text']
        coverage = self.section(model, 'Coverage & Limitations')['text']
        self.assertIn('9 algorithm task(s) completed; 1 unavailable or unable to conclude', status)
        self.assertIn('Musical-semantics analysis: too little usable audio', coverage)
        self.assertNotIn('0 analysis gap', '\n'.join(model['summary']))
        self.assertNotIn('0 inconclusive comparison', '\n'.join(model['summary']))
        self.assertEqual(model['counts']['audio_review'], 1)

    def test_duplicate_provider_details_do_not_repeat_summary_limitations(self):
        r = response(); unavailable = {'provider': 'librosa', 'status': 'unavailable',
            'reason_code': 'dependency_unavailable'}
        r['assessment_checks']['audio_structure'] = [unavailable, copy.deepcopy(unavailable)]
        text = self.section(self.model(r), 'Coverage & Limitations')['text']
        self.assertEqual(text.count('Acoustic-structure analysis:'), 1)

    def test_provider_errors_stay_separate_from_execution_success(self):
        r = response(); r['assessment_checks']['audio_diagnostic_summary'][0].update(
            completed_provider_tasks=0, unavailable_provider_tasks=2)
        r['assessment_checks']['audio_structure'] = [{'provider': 'librosa', 'status': 'unavailable',
            'reason_code': 'provider_failed', 'message': 'InternalException /local/cache/path'}]
        model = self.model(r)
        self.assertIn('Core evaluation completed', self.section(model, 'Evaluation Status')['text'])
        self.assertIn('analysis could not run successfully', self.section(model, 'Coverage & Limitations')['text'])
        self.assertNotIn('/local/cache/path', '\n'.join(model['summary']))

    def test_disabled_and_unknown_audio_are_not_claimed_completed(self):
        r = response(); r['assessment_checks'].pop('audio_diagnostic_summary')
        for requested, phrase in ((False, 'not enabled'), (None, 'run state unknown'), (True, 'no diagnostic result')):
            text = self.section(self.model(r, audio_requested=requested), 'Evaluation Status')['text']
            self.assertIn(phrase, text)
            self.assertNotIn('Audio analysis completed', text)

    def test_zero_tasks_does_not_claim_content_was_compared(self):
        r = response(); r['assessment_checks']['audio_diagnostic_summary'][0].update(
            completed_provider_tasks=0, unavailable_provider_tasks=0)
        self.assertIn('no content comparison is claimed', self.section(self.model(r), 'Evaluation Status')['text'])

    def test_qualifying_evidence_missing_is_withheld_not_execution_failure(self):
        r = response(); r['axes']['integrity'].update(value=None, status='insufficient_evidence')
        r['assessment_checks']['integrity_decision'][0].update(numeric_release='withheld',
            assessed_fact_count=15, release_reason='insufficient_qualifying_cross_evidence')
        model = self.model(r); text = self.section(model, 'Core Evidence Consistency')['text']
        self.assertIn('No contradictions were found', text)
        self.assertIn('Score withheld because qualifying cross-evidence is missing', text)
        self.assertIn('Core evaluation completed', model['summary'][0])
        self.assertNotIn('withheld / not assessed', text)

    def test_execution_error_does_not_claim_checks_passed(self):
        r = response(); r.update(execution_status='error', error='Final object hash mismatch')
        r['axes']['integrity'].update(value=None, status=None)
        model = self.model(r)
        self.assertIn('Core evaluation failed', model['summary'][0])
        self.assertIn('Final object hash mismatch', model['summary'][0])
        self.assertIn('was not assessed because', model['summary'][1])
        self.assertNotIn('No contradictions were found', model['summary'][1])

    def test_all_release_reasons_have_specific_human_explanations(self):
        for reason, phrase in (
            ('no_assessed_layers', 'no contributing evidence layers'),
            ('insufficient_assessed_layers', 'too few contributing evidence layers'),
            ('insufficient_support_breadth', 'too few relevant sources'),
            ('file_checks_not_completed', 'required file checks'),
            ('final_attestation_unreadable', "final file's attestation")):
            r = response(); r['axes']['integrity']['value'] = None
            r['assessment_checks']['integrity_decision'][0]['release_reason'] = reason
            self.assertIn(phrase, self.section(self.model(r), 'Core Evidence Consistency')['text'])

    def test_optional_workflow_absence_does_not_become_missing_requirement(self):
        r = response(); r['workflow_assessment'] = {'rule_results': [
            {'level': 'required', 'rule_id': 'r', 'status': 'satisfied'},
            {'level': 'optional', 'rule_id': 'o', 'status': 'missing'}]}
        self.assertIn('requirements were met', self.section(self.model(r), 'Other Key Results')['text'])
        r['workflow_assessment']['rule_results'][0]['status'] = 'missing'
        self.assertIn('1 missing', self.section(self.model(r), 'Other Key Results')['text'])

    def test_workflow_completion_is_not_inferred_from_axis_value(self):
        r = response(); r['axes']['completeness']['value'] = 1.0
        text = self.section(self.model(r), 'Other Key Results')['text']
        self.assertIn('Workflow requirements were not assessed or not supplied', text)
        self.assertNotIn('requirements were met', text)

    def test_c2pa_states_distinguish_absent_invalid_untrusted_and_not_checked(self):
        for state, phrase in (('absent', 'no manifest found'), ('invalid', 'manifest invalid'),
                              ('untrusted', 'signer not trusted'), ('not_checked', 'not checked'),
                              ('unreadable', 'could not be read'), ('trusted_c2pa', 'signer trusted'),
                              ('trusted_legacy_itl', 'legacy policy'), ('new_state', 'uninterpreted state new_state')):
            r = response(); r['findings'].append({'code': 'C2PA_FINAL_ATTESTATION', 'final_c2pa_state': state})
            self.assertIn(phrase, self.section(self.model(r), 'Other Key Results')['text'])

    def test_zero_attestation_score_does_not_imply_manifest_absent(self):
        r = response(); r['axes']['attestation_strength'] = {'value': 0, 'confidence': .9, 'availability': 'available'}
        text = self.section(self.model(r), 'Other Key Results')['text']
        self.assertIn('Final-file C2PA: state not supplied', text)
        self.assertNotIn('no manifest found', text)

    def test_disclosure_counts_and_exclusions_do_not_imply_ai_detection(self):
        r = response(); r['assessment_checks']['ai_disclosure_coverage'] = [{
            'declared_audio_count': 0, 'assessed_audio_count': 2, 'excluded_input_error_count': 1}]
        text = self.section(self.model(r), 'Other Key Results')['text']
        self.assertIn('0/2 assessed audio files', text)
        self.assertIn('1 audio file(s) excluded', text)
        self.assertIn('not AI detection', text)

    def test_disclosure_conflict_is_reported_in_other_key_results(self):
        r = response(); r['assessment_checks']['cross_evidence'] = [
            {'rule': 'c2pa_ai_source_type_matches_creation_method', 'passed': False}]
        self.assertIn('declaration conflicts with signed evidence', self.section(self.model(r), 'Other Key Results')['text'])

    def test_summary_model_changes_neither_json_nor_version(self):
        from music_target import VERSION
        r = response(); before = copy.deepcopy(r); model = self.model(r)
        model['summary_sections'][0]['text'] = 'Edited only in display'
        self.assertEqual(r, before)
        self.assertNotIn('summary_sections', r)
        self.assertEqual(VERSION, '0.8.3')
        self.assertEqual(model['template_version'], '0.8.3')
