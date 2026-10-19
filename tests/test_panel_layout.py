"""Structural checks for the native panel; no browser or Adobe process."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Layout(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = {}
        self.duplicates = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        identity = attrs.get('id')
        if identity:
            if identity in self.nodes:
                self.duplicates.append(identity)
            self.nodes[identity] = (tag, attrs, tuple(self.stack))
        if tag not in {'input', 'meta', 'link', 'br', 'img', 'hr'}:
            self.stack.append((tag, identity))

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            raise AssertionError('Unbalanced panel HTML: ' + tag)
        self.stack.pop()


class PanelLayoutTests(unittest.TestCase):
    def setUp(self):
        self.layout = Layout()
        self.layout.feed((ROOT / 'plugin/index.html').read_text(encoding='utf-8-sig'))

    def ancestors(self, identity):
        return {identity for _, identity in self.layout.nodes[identity][2]}

    def test_balanced_markup_unique_controls_and_no_step_navigation(self):
        self.assertEqual(self.layout.stack, [])
        self.assertEqual(self.layout.duplicates, [])
        html = (ROOT / 'plugin/index.html').read_text(encoding='utf-8-sig')
        self.assertNotIn('data-step=', html)
        for identity in ['apply', 'analyze', 'plan', 'sync', 'apply-sync', 'recover-apply',
                         'update', 'cancel', 'recover-update', 'read-selection', 'create-input']:
            self.assertIn(identity, self.layout.nodes)

    def test_basic_controls_are_outside_advanced_and_details_inside(self):
        for identity in ['mode-separate', 'mode-mixed', 'speaker-mapping', 'reserve-camera',
                         'min-shot', 'suppress-short', 'sync', 'read-project']:
            self.assertNotIn('advanced-settings', self.ancestors(identity), identity)
        for identity in ['cameras', 'microphones', 'range-start', 'speaker-count',
                         'vad-threshold', 'calibration', 'speaker-corrections',
                         'short-turn', 'overlap', 'start-camera', 'overrides']:
            self.assertIn('advanced-settings', self.ancestors(identity), identity)

    def test_settings_and_review_are_not_nested_in_advanced_controls(self):
        for identity in ['view-settings', 'view-review', 'action-readiness', 'apply']:
            self.assertNotIn('advanced-settings', self.ancestors(identity), identity)


if __name__ == '__main__':
    unittest.main()
