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
        self.assertIn('1 content finding(s) require review', text)
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
        self.assertIn('partly completed', model['summary'][2])

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
            self.assertIn(phrase, self.model(r, audio_requested=requested)['summary'][2])

    def test_provider_failure_keeps_core_execution_separate(self):
        r = response(); r['assessment_checks']['audio_diagnostic_summary'][0].update(
            reason_code='deadline', message='Time budget exhausted.')
        model = self.model(r)
        self.assertIn('succeeded', model['summary'][0]); self.assertIn('not completed', model['summary'][2])
        self.assertIn('Time budget exhausted', model['summary'][2])

    def test_high_numeric_score_does_not_hide_core_contradiction(self):
        r = response(); r['axes']['integrity'].update(value=.994, status='contradicted')
        r['assessment_checks']['integrity_decision'][0]['contradiction_count'] = 1
        model = self.model(r)
        self.assertIn('1 core contradiction', model['summary'][1])
        self.assertEqual(model['axes'][0]['value'], '0.994 (contradicted: 1)')

    def test_null_with_contradiction_remains_null(self):
        r = response(); r['axes']['integrity'].update(value=None, status='contradicted')
        r['assessment_checks']['integrity_decision'][0]['contradiction_count'] = 1
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
        model = self.model(); self.assertIn('1/13', model['summary'][-1])
        self.assertIn('without qualifying checks 12', model['summary'][-1])
        self.assertIn('13/13', '\n'.join(model['coverage']))

    def test_checked_includes_contradictions_not_just_support(self):
        r = response(); r['assessment_checks']['support_breadth'][0].update(supported=1, contradicted=2)
        text = '\n'.join(self.model(r)['coverage'])
        self.assertIn('3/13; consistent 1; contradicted 2', text)
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
        for phrase in ('1 content finding(s) require review', 'Qualified recording cross-checks: 1/13',
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
