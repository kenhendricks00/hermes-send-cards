"""Offline tests for the draft card renderer and the draft rules store. Standard library only."""
import contextlib
import html
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/send-cards/scripts'
EXAMPLES = ROOT / 'skills/send-cards/examples'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


card = load('draft_card')
rules = load('draft_rules')

EVIL = '<script>alert(1)</script>'


def example(name):
    return json.loads((EXAMPLES / f'{name}.json').read_text(encoding='utf-8'))


def prompts(page):
    return [html.unescape(p) for p in re.findall(r'data-hermes-send="([^"]*)"', page)]


class EscapingTests(unittest.TestCase):
    def evil_draft(self, channel='email'):
        return {
            'draft_id': 'd-1', 'channel': channel, 'channel_name': EVIL, 'from': EVIL,
            'to': [{'name': EVIL, 'address': EVIL}], 'cc': [EVIL], 'bcc': [EVIL],
            'subject': EVIL, 'where': EVIL, 'body': EVIL + '\n"quoted" & <b>bold</b>',
            'reply_to': {'author': EVIL, 'date': EVIL, 'text': EVIL},
            'attachments': [{'name': EVIL, 'size': EVIL}], 'bot': EVIL, 'sent_at': EVIL,
        }

    def assert_clean(self, page):
        # The only <script> tags are the card's own.
        self.assertNotIn('<script>alert', page)
        self.assertNotIn('<b>bold</b>', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)

    def test_every_field_escaped_in_every_state_and_layout(self):
        for channel in ('email', 'discord', 'linkedin', 'x'):
            for state in ('pending', 'sent', 'discarded'):
                with self.subTest(channel=channel, state=state):
                    self.assert_clean(card.render(self.evil_draft(channel), state))

    def test_draft_id_escaped_in_attributes_and_script(self):
        page = card.render({'draft_id': '"><img src=x onerror=alert(1)></script>', 'channel': 'email',
                            'to': ['a@example.com'], 'body': 'hi'})
        self.assertNotIn('<img src=x', page)
        self.assertNotIn('</script>"', page)

    def test_body_with_script_close_in_x_copy_text(self):
        page = card.render({'draft_id': 'x', 'channel': 'x', 'from': '@a', 'body': '</script><script>bad()</script>'})
        self.assertNotIn('<script>bad()', page)


class LinkTests(unittest.TestCase):
    def test_only_http_links(self):
        out = card.linkify('see https://example.com/a?b=1&c=2. or javascript:alert(1) or data:text/html,x')
        self.assertIn('href="https://example.com/a?b=1&amp;c=2"', out)
        self.assertNotIn('href="javascript:', out)
        self.assertNotIn('href="data:', out)
        self.assertEqual(out.count('<a '), 1)

    def test_link_cannot_break_attribute(self):
        out = card.linkify('https://example.com/"onmouseover="alert(1)')
        self.assertNotIn('"onmouseover="', out)

    def test_image_sources(self):
        self.assertEqual(card.safe_src('javascript:alert(1)'), '')
        self.assertEqual(card.safe_src('data:text/html;base64,AAAA'), '')
        self.assertEqual(card.safe_src('https://example.com/a.png'), 'https://example.com/a.png')
        page = card.render({'draft_id': 'i', 'channel': 'email', 'icon': 'javascript:alert(1)', 'body': 'x'})
        self.assertNotIn('javascript:', page)

    def test_x_length_counts_links_as_23(self):
        self.assertEqual(card.x_length('a https://example.com/' + 'x' * 100), 2 + 23)


class ButtonTests(unittest.TestCase):
    def test_every_prompt_names_the_draft(self):
        for name in ('email', 'discord', 'linkedin', 'x'):
            o = example(name)
            page = card.render(o, 'pending', str(EXAMPLES))
            found = prompts(page)
            with self.subTest(name=name):
                self.assertTrue(found)
                for p in found:
                    self.assertIn(o['draft_id'], p)
                    self.assertLessEqual(len(p), card.MAX_PROMPT)
                # The scripted Send button builds its prompt around the same id.
                self.assertIn(json.dumps(o['draft_id']), page)

    def test_menu_items(self):
        page = card.render(example('email'))
        for label in ('Allow recipient', 'Needs approval', 'Disable drafts for Hermes', 'Manage rules'):
            self.assertIn(label, page)
        self.assertIn('Disable drafts for Ops bot', card.render(dict(example('email'), bot='Ops bot')))

    def test_long_recipient_list_keeps_prompts_short(self):
        o = {'draft_id': 'long', 'channel': 'email', 'to': [f'user{i}@example.com' for i in range(80)], 'body': 'x'}
        for p in prompts(card.render(o)):
            self.assertLessEqual(len(p), card.MAX_PROMPT)

    def test_disabled_send_logic_present(self):
        page = card.render({'draft_id': 'e', 'channel': 'email', 'to': [], 'subject': '', 'body': ''})
        self.assertIn('need=["to", "body"]', page)
        self.assertIn("classList.toggle('off',!on)", page)
        self.assertIn('sb.disabled=!on', page)
        for ph in ('data-ph="Subject"', 'data-ph="name@example.com"', 'data-ph="Message"'):
            self.assertIn(ph, page)
        chat = card.render(example('discord'))
        self.assertIn('need=["body"]', chat)

    def test_edit_prompt_and_length_guard(self):
        page = card.render(example('email'))
        self.assertIn("'Send draft '+id+' with my edits", page)
        self.assertIn('MAX=500', page)
        self.assertIn('window.hermes.send(p)', page)

    def test_rule_checkmark(self):
        allow = card.render(dict(example('email'), rule='allow'))
        first = allow.index('Allow recipient')
        self.assertIn('m5 12 5 5 9-10', allow[first:allow.index('Needs approval')])

    def test_x_card(self):
        page = card.render(example('x'), 'pending', str(EXAMPLES))
        self.assertIn('>Post</button>', page)
        self.assertIn('@example', page)
        self.assertIn('src="data:image/png;base64,', page)
        self.assertNotIn('unavatar.io', page)
        opt_in = card.render(dict(example('x'), avatar='unavatar'), 'pending', str(EXAMPLES))
        self.assertIn('https://unavatar.io/x/example', opt_in)

    def test_no_host_frame_access(self):
        for name in ('email', 'x'):
            for state in ('pending', 'sent'):
                page = card.render(example(name), state, str(EXAMPLES))
                self.assertNotIn('hermes-inline-preview-size', page)
                self.assertNotIn('parent.postMessage', page)


class DoneStateTests(unittest.TestCase):
    def test_no_action_buttons(self):
        for name in ('email', 'discord', 'linkedin', 'x'):
            for state in ('sent', 'discarded'):
                page = card.render(example(name), state, str(EXAMPLES))
                with self.subTest(name=name, state=state):
                    self.assertNotIn('<button', page)
                    self.assertNotIn('data-hermes-send', page)
                    self.assertNotIn('contenteditable', page)
                    self.assertIn('<details class="gc done"', page)
                    self.assertIn('Sent' if state == 'sent' else 'Discarded', page)

    def test_sent_at(self):
        page = card.render(dict(example('email'), sent_at='2:14 PM'), 'sent')
        self.assertIn('Sent \u00b7 dana.rivera@example.com \u00b7 2:14 PM', page)
        self.assertNotIn('2:14 PM', card.render(dict(example('email'), sent_at='2:14 PM'), 'discarded'))


class RenderErrorTests(unittest.TestCase):
    def test_bad_input(self):
        for bad in ({}, {'draft_id': ' '}, [], {'draft_id': 'a', 'kind': 'fax'}, {'draft_id': 'a', 'revision': 'x'}):
            with self.assertRaises(card.DraftError):
                card.render(bad)
        with self.assertRaises(card.DraftError):
            card.render({'draft_id': 'a'}, 'posted')


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def run_card(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / 'draft_card.py'), *args],
                              capture_output=True, text=True, encoding='utf-8')

    def test_render_examples(self):
        for name in ('email', 'discord', 'linkedin', 'x'):
            out = self.dir / f'{name}.html'
            r = self.run_card(str(EXAMPLES / f'{name}.json'), '-o', str(out), '--state', 'sent', '--sent-at', '1:00 PM')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(Path(r.stdout.strip()).resolve(), out.resolve())
            self.assertIn('1:00 PM', out.read_text(encoding='utf-8'))

    def test_missing_file(self):
        r = self.run_card(str(self.dir / 'nope.json'))
        self.assertEqual(r.returncode, 2)
        self.assertIn('cannot read', r.stderr)

    def test_invalid_json(self):
        p = self.dir / 'bad.json'
        p.write_text('{not json', encoding='utf-8')
        r = self.run_card(str(p))
        self.assertEqual(r.returncode, 2)
        self.assertIn('not valid JSON', r.stderr)

    def test_missing_draft_id(self):
        p = self.dir / 'noid.json'
        p.write_text('{"body": "hi"}', encoding='utf-8')
        r = self.run_card(str(p))
        self.assertEqual(r.returncode, 2)
        self.assertIn('draft_id is required', r.stderr)
        self.assertFalse((self.dir / 'noid.html').exists())

    def test_bad_state(self):
        r = self.run_card(str(EXAMPLES / 'email.json'), '--state', 'posted')
        self.assertEqual(r.returncode, 2)

    def test_long_x_post_warns(self):
        p = self.dir / 'long.json'
        p.write_text(json.dumps({'draft_id': 'x', 'channel': 'x', 'from': '@a', 'body': 'x' * 300}), encoding='utf-8')
        r = self.run_card(str(p))
        self.assertEqual(r.returncode, 0)
        self.assertIn("over X's 280", r.stderr)


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.file = Path(self.temp.name) / 'draft_rules.json'

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rules.main(list(args))
        return code, out.getvalue().strip(), err.getvalue()

    def test_round_trip(self):
        self.assertEqual(self.cli('check', 'email', 'dana@example.com'), (0, 'approval', ''))
        self.assertFalse(self.file.exists())
        self.assertEqual(self.cli('allow', 'email', ' Dana@Example.com ')[0], 0)
        self.assertEqual(self.cli('check', 'email', 'dana@example.com')[1], 'allow')
        self.assertEqual(self.cli('check', 'chat', 'dana@example.com')[1], 'approval')
        self.cli('allow', 'email', 'dana@example.com')
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['allow'], ['email:dana@example.com'])
        self.assertEqual(self.cli('approve', 'email', 'dana@example.com')[0], 0)
        self.assertEqual(self.cli('check', 'email', 'dana@example.com')[1], 'approval')

    def test_disable_and_enable_bot(self):
        self.cli('disable-bot', 'Ops Bot')
        self.assertEqual(self.cli('check', 'dm', 'x', '--bot', 'ops bot')[1], 'disabled')
        self.assertEqual(self.cli('check', 'dm', 'x')[1], 'approval')
        self.cli('disable-bot', 'ops bot')
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['disabled_bots'], ['Ops Bot'])
        self.cli('enable-bot', 'OPS BOT')
        self.assertEqual(self.cli('check', 'dm', 'x', '--bot', 'Ops Bot')[1], 'approval')

    def test_list(self):
        self.cli('allow', 'chat', '#general')
        code, out, _ = self.cli('list')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {'allow': ['chat:#general'], 'disabled_bots': []})
        self.assertEqual(json.loads(self.cli()[1])['allow'], ['chat:#general'])

    def test_only_writes_rules_file(self):
        self.cli('allow', 'dm', 'Jordan Park')
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), ['draft_rules.json'])

    def test_corrupt_file_is_treated_as_empty(self):
        self.file.write_text('[1, 2', encoding='utf-8')
        self.assertEqual(self.cli('check', 'email', 'a@example.com')[1], 'approval')

    def test_cli_errors(self):
        for args in (('bogus',), ('check', 'email'), ('allow', 'fax', 'a'), ('allow', 'email', '  '),
                     ('check', 'email', 'a', '--bot'), ('disable-bot',), ('disable-bot', ' ')):
            with self.subTest(args=args):
                code, _, err = self.cli(*args)
                self.assertEqual(code, 2)
                self.assertTrue(err.startswith('draft_rules:'))
        self.assertFalse(self.file.exists())

    def test_subprocess_uses_hermes_home(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / 'draft_rules.py'), 'allow', 'email', 'a@example.com'],
                           capture_output=True, text=True, env=dict(os.environ, HERMES_HOME=self.temp.name))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.file.exists())


if __name__ == '__main__':
    unittest.main()
