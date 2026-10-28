"""Offline human report renderer; consumes only the detached GUI presentation model."""
from html import escape
import json

from builder_review import GROUP_LABELS, confidence_lines


def render_review_html(model):
    def e(value):
        return escape(str(value), quote=True)
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<title>Music evidence assessment report</title>',
             '<style>body{font:16px/1.5 system-ui,sans-serif;max-width:1050px;margin:32px auto;padding:0 20px;color:#172333}'
             'h1,h2,h3{line-height:1.2}table{width:100%;border-collapse:collapse}td,th{padding:10px;text-align:left;'
             'border-bottom:1px solid #ccd2da;overflow-wrap:anywhere}.summary,.scope,article{padding:16px;margin:12px 0;'
             'background:#f3f5f7;border:1px solid #cbd3dc;border-radius:6px}.audio_review{border-left:6px solid #ad6500}'
             '.core{border-left:6px solid #a32b26}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}'
             '@media print{body{margin:0;max-width:none}article,tr{break-inside:avoid}details>*{display:block!important}'
             'summary{display:none}h2,h3{break-after:avoid}.summary,.scope{break-inside:avoid}}</style>',
             '<h1>Music evidence assessment report</h1><section class="summary"><h2>Assessment summary</h2>']
    parts += ['<p>' + e(line) + '</p>' for line in model['summary']]
    parts += ['<p class="scope">' + e(model['scope_note']) + '</p></section>', '<h2>Items requiring attention</h2>']
    for group, label in GROUP_LABELS.items():
        rows = [item for item in model['items'] if item['group'] == group]
        if not rows or group == 'support':
            continue
        parts.append('<h3>' + e(label) + '</h3>')
        for item in rows:
            parts += ['<article class="' + e(group) + '" id="' + e(item['id']) + '"><h3>' + e(item['title']) + '</h3>',
                      '<p>' + e(item['affected']) + '</p><pre>' + e(item['explanation']) + '</pre>',
                      '<p>' + e(item['scoring_note']) + '</p><p>Suggested action: ' + e(item['suggestion']) + '</p>',
                      '<p>Original severity: ' + e(item['severity'] or 'not supplied') + '</p>',
                      '<p>Evidence references: ' + e(', '.join(item['references'] + item['diagnostic_ids'])) + '</p></article>']
    parts += ['<h2>Four separate dimensions</h2><table><thead><tr><th>Dimension</th><th>Value</th><th>State / scope</th>'
              '<th>Assessment confidence</th></tr></thead><tbody>']
    for axis in model['axes']:
        parts.append('<tr>' + ''.join('<td>' + e(axis[key]) + '</td>' for key in ('label', 'value', 'status', 'confidence')) + '</tr>')
    parts += ['</tbody></table><h2>Coverage and detailed evidence</h2>',
               '<pre>' + e("\n".join(model['coverage'] + confidence_lines(model) +
                  ["Assessment confidence is not a probability of authenticity."] +
                  [axis['label'] + ": " + axis['reasoning'] for axis in model['axes']] +
                  model['details'] + model['notes'])) + '</pre>',
              '<details><summary>Other observations and supporting details</summary>' +
              ''.join('<article><h3>' + e(item['title']) + '</h3><p>' + e(item['affected']) + '</p><pre>' +
                      e(item['explanation']) + '</pre><p>' + e(item['scoring_note']) + '</p></article>'
                      for item in model['items'] if item['group'] == 'support') + '</details>',
              '<h2>Snapshot and display version</h2><pre>' +
              e(json.dumps(model['metadata'], indent=2, ensure_ascii=False)) + '</pre><p>Display template: ' +
              e(model['template_version']) + '</p>',

              '<details><summary>Complete original machine response</summary><pre>' +
              e(json.dumps(model['raw_response'], indent=2, ensure_ascii=False, allow_nan=False)) + '</pre></details>',
              '</html>']
    return '\n'.join(parts)
