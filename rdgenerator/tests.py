import base64
import json
import os
import tempfile
from unittest import mock

import pyzipper
from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from .api_views import validate_generate_params
from .forms import GenerateForm
from .views import get_full_url, _norm_hex_color


class ThemeColorValidationTests(SimpleTestCase):
    base = {
        'platform': 'windows',
        'version': '1.5.3',
        'exename': 'client',
        'direction': 'both',
        'installation': 'installationY',
        'settings': 'settingsY',
        'theme': 'system',
        'themeDorO': 'default',
        'passApproveMode': 'password-click',
        'permissionsType': 'custom',
    }

    def _form(self, **overrides):
        data = dict(self.base)
        data.update(overrides)
        return GenerateForm(data)

    def test_theme_colors_accept_hash_or_bare_hex(self):
        form = self._form(themeColor='ff8800', themeSurfaceLight='#FFF6E9')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['themeColor'], '#FF8800')
        self.assertEqual(form.cleaned_data['themeSurfaceLight'], '#FFF6E9')

    def test_blank_theme_color_means_stock(self):
        form = self._form(themeColor='', themeSurfaceDark='   ')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['themeColor'], '')
        self.assertEqual(form.cleaned_data['themeSurfaceDark'], '')

    def test_bad_theme_color_is_rejected(self):
        for value in ('red', '#ff880', '#gg8800', '#ff8800ff'):
            with self.subTest(value=value):
                form = self._form(themeColor=value)
                self.assertFalse(form.is_valid())
                self.assertIn('themeColor', form.errors)

    def test_api_normalizes_theme_colors(self):
        data = dict(self.base)
        data.update({
            'themeColor': 'ff8800',
            'themeSurfaceLight': '#FFF6E9',
            'themeSurfaceDark': '',
            'themeMeColor': '#00AA66',
        })
        cleaned, errors = validate_generate_params(data)
        self.assertEqual(errors, {})
        self.assertEqual(cleaned['themeColor'], '#FF8800')
        self.assertEqual(cleaned['themeSurfaceLight'], '#FFF6E9')
        self.assertEqual(cleaned['themeSurfaceDark'], '')
        self.assertEqual(cleaned['themeMeColor'], '#00AA66')

    def test_api_rejects_bad_theme_color(self):
        data = dict(self.base)
        data['themeMeColor'] = 'green'
        _, errors = validate_generate_params(data)
        self.assertIn('themeMeColor', errors)

    def test_norm_hex_color(self):
        self.assertEqual(_norm_hex_color('ff8800'), '#FF8800')
        self.assertEqual(_norm_hex_color('#ff8800'), '#FF8800')
        self.assertEqual(_norm_hex_color(''), '')
        self.assertEqual(_norm_hex_color('nope'), '')


class GetFullUrlTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get('/', HTTP_HOST='127.0.0.1:8000')

    @override_settings(GENURL='', PROTOCOL='https')
    def test_uses_request_host_without_genurl(self):
        self.assertEqual(get_full_url(self.request), 'https://127.0.0.1:8000')

    @override_settings(GENURL='https://rdgen.example.com:4433/', PROTOCOL='https')
    def test_uses_genurl(self):
        self.assertEqual(get_full_url(self.request), 'https://rdgen.example.com:4433')

    @override_settings(GENURL='rdgen.example.com:8000', PROTOCOL='http')
    def test_adds_protocol_to_genurl_without_one(self):
        self.assertEqual(get_full_url(self.request), 'http://rdgen.example.com:8000')


@override_settings(GENURL='https://rdgen.example.com', PROTOCOL='https')
class GeneratorViewTests(TestCase):
    def setUp(self):
        cwd = os.getcwd()
        tmp = tempfile.TemporaryDirectory()
        os.chdir(tmp.name)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, cwd)

    def test_workflow_downloads_zip_from_genurl(self):
        github = mock.Mock(status_code=200)
        github.json.return_value = {'workflow_run_id': 1, 'html_url': ''}
        with mock.patch('rdgenerator.views.requests.post', return_value=github) as post:
            self.client.post('/generator', {
                'platform': 'windows',
                'version': '1.5.3',
                'exename': 'client',
                'direction': 'both',
                'installation': 'installationY',
                'settings': 'settingsY',
                'theme': 'system',
                'themeDorO': 'default',
                'passApproveMode': 'password-click',
                'permissionsType': 'custom',
            }, HTTP_HOST='127.0.0.1')

        post.assert_called_once()
        zip_url = json.loads(post.call_args.kwargs['json']['inputs']['zip_url'])
        self.assertEqual(zip_url['url'], 'https://rdgen.example.com')

        # The generated custom_.txt must hide RustDesk's "Powered by" link
        # through the built-in option, not by rewriting the translation.
        zip_path = os.path.join('temp_zips', zip_url['file'])
        with pyzipper.AESZipFile(zip_path) as zf:
            zf.setpassword(settings.ZIP_PASSWORD.encode())
            secrets = json.loads(zf.read('secrets.json'))
        custom = json.loads(base64.b64decode(secrets['custom']))
        self.assertEqual(custom['override-settings']['hide-powered-by-me'], 'Y')


class VersionNoticeTests(SimpleTestCase):
    """Hinweis auf neue RustDesk-Versionen (version_check.py)."""

    def setUp(self):
        from django.core.cache import cache
        from . import version_check
        self.vc = version_check
        cache.delete(version_check.CACHE_KEY)
        self.addCleanup(cache.delete, version_check.CACHE_KEY)

    def _responses(self, latest_tag, upstream_forms='', fail=False):
        import requests

        def fake_get(url, headers=None, timeout=None):
            if fail:
                raise requests.ConnectionError('offline')
            resp = mock.Mock()
            resp.status_code = 200
            resp.raise_for_status = mock.Mock()
            if url == self.vc.RUSTDESK_LATEST_URL:
                resp.json = mock.Mock(return_value={'tag_name': latest_tag})
            else:
                resp.text = upstream_forms
            return resp
        return mock.patch.object(self.vc.requests, 'get', side_effect=fake_get)

    def _max_offered(self):
        versions = [self.vc._parse(v) for v in self.vc._offered_versions()]
        return max(versions)

    def test_no_notice_when_latest_is_offered(self):
        latest = '.'.join(str(x) for x in self._max_offered())
        with self._responses(latest):
            self.assertIsNone(self.vc.get_version_notice())

    def test_notice_when_upstream_supports_newer_version(self):
        major, minor, patch = self._max_offered()
        newer = f'{major}.{minor}.{patch + 1}'
        with self._responses(newer, upstream_forms=f"('{newer}','{newer}')"):
            notice = self.vc.get_version_notice()
        self.assertEqual(notice['latest'], newer)
        self.assertTrue(notice['upstream_supported'])

    def test_info_notice_when_upstream_does_not_support_yet(self):
        major, minor, patch = self._max_offered()
        newer = f'{major}.{minor + 1}.0'
        with self._responses(newer, upstream_forms="('1.0.0','1.0.0')"):
            notice = self.vc.get_version_notice()
        self.assertEqual(notice['latest'], newer)
        self.assertFalse(notice['upstream_supported'])

    def test_github_unreachable_shows_nothing_and_is_cached(self):
        with self._responses('', fail=True) as fake:
            self.assertIsNone(self.vc.get_version_notice())
            self.assertIsNone(self.vc.get_version_notice())
        self.assertEqual(fake.call_count, 1)

    def test_result_is_cached(self):
        major, minor, patch = self._max_offered()
        newer = f'{major}.{minor}.{patch + 1}'
        with self._responses(newer, upstream_forms='') as fake:
            self.vc.get_version_notice()
            self.vc.get_version_notice()
        self.assertEqual(fake.call_count, 2)  # Release + Original-forms.py, danach aus dem Cache

    def test_banner_rendered_on_generator_page(self):
        notice = {'latest': '9.9.9', 'release_url': 'https://example.invalid/r',
                  'upstream_supported': True, 'upstream_url': 'https://example.invalid/u'}
        with mock.patch('rdgenerator.views.get_version_notice', return_value=notice):
            response = self.client.get('/')
        self.assertContains(response, 'RustDesk 9.9.9 ist verfügbar')
        self.assertContains(response, 'version-notice--action')
