import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pyzipper
from django.test import Client, RequestFactory, TestCase, override_settings
from django.template.loader import render_to_string

from rdgen.settings import _origin_from_url
from .api_views import validate_generate_params
from .forms import DIY_REPOSITORY, OFFICIAL_REPOSITORY, GenerateForm
from .views import _public_base_url, _resolve_source, generate_custom_client


class PublicBaseUrlTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(GENURL="build.example.com/", PROTOCOL="https")
    def test_uses_configured_external_url(self):
        request = self.factory.get("/", HTTP_HOST="internal:8000")

        self.assertEqual(_public_base_url(request), "https://build.example.com")

    @override_settings(GENURL="http://build.example.com/base/", PROTOCOL="https")
    def test_preserves_configured_scheme(self):
        request = self.factory.get("/", HTTP_HOST="internal:8000")

        self.assertEqual(_public_base_url(request), "http://build.example.com/base")

    def test_csrf_origin_uses_protocol_for_hostname(self):
        self.assertEqual(
            _origin_from_url("rdgen.youyoulai.xyz"),
            "https://rdgen.youyoulai.xyz",
        )

    def test_csrf_origin_preserves_url_scheme_and_port(self):
        self.assertEqual(
            _origin_from_url("http://build.example.com:8000/path/"),
            "http://build.example.com:8000",
        )


class GenerateApiTests(TestCase):
    def test_web_form_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post("/generator", data={"exename": "support-client"})

        self.assertEqual(response.status_code, 403)

    @override_settings(
        CSRF_TRUSTED_ORIGINS=["https://rdgen.youyoulai.xyz"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    )
    def test_web_form_accepts_csrf_behind_https_proxy(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.get("/generator", HTTP_HOST="rdgen.youyoulai.xyz")
        token = csrf_client.cookies["csrftoken"].value

        response = csrf_client.post(
            "/generator",
            data={"csrfmiddlewaretoken": token},
            HTTP_HOST="rdgen.youyoulai.xyz",
            HTTP_ORIGIN="https://rdgen.youyoulai.xyz",
            HTTP_REFERER="https://rdgen.youyoulai.xyz/generator",
            HTTP_X_FORWARDED_PROTO="https",
        )

        self.assertEqual(response.status_code, 200)

    @override_settings(GENURL="https://build.example.com")
    @patch("rdgenerator.api_views.generate_custom_client")
    def test_json_api_accepts_web_build_parameters(self, generate):
        generate.return_value = {
            "success": True,
            "uuid": "run-id",
            "filename": "support-client",
            "platform": "windows",
            "log_url": "https://github.example/run",
        }

        response = self.client.post(
            "/api/generate",
            data=json.dumps({
                "exename": "support-client",
                "platform": "windows",
                "version": "1.4.9",
                "serverIP": "rd.example.com",
                "delayFix": True,
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        params, public_url = generate.call_args.args
        self.assertEqual(params["serverIP"], "rd.example.com")
        self.assertEqual(public_url, "https://build.example.com")

    def test_custom_repository_requires_owner_and_repository(self):
        _, errors = validate_generate_params({
            "exename": "support-client",
            "sourceRepository": "custom",
            "customSourceRepository": "invalid-repository",
        })

        self.assertIn("customSourceRepository", errors)

    def test_web_form_accepts_custom_repository(self):
        form = GenerateForm(data={
            "exename": "support-client",
            "platform": "windows",
            "version": "1.4.9",
            "sourceRepository": "custom",
            "customSourceRepository": "example/rustdesk",
            "direction": "both",
            "installation": "installationY",
            "settings": "settingsY",
            "theme": "system",
            "themeDorO": "default",
            "passApproveMode": "password-click",
            "permissionsDorO": "default",
            "permissionsType": "custom",
            "androidArch": "aarch64",
            "androidBuildHost": "ubuntu",
        })

        self.assertTrue(form.is_valid(), form.errors)

    def test_android_options_default_to_aarch64_and_visible_connection_ui(self):
        form = GenerateForm()

        self.assertEqual(form.fields["androidArch"].initial, "aarch64")
        self.assertEqual(form.fields["androidBuildHost"].initial, "ubuntu")
        self.assertFalse(form.fields["hideAndroidConnectionNotification"].initial)
        self.assertFalse(form.fields["hideAndroidConnectionCard"].initial)

    def test_json_api_validates_android_architecture(self):
        cleaned, errors = validate_generate_params({
            "exename": "support-client",
            "platform": "android",
            "androidArch": "invalid",
        })

        self.assertIn("androidArch", errors)


class DownloadTests(TestCase):
    def test_android_result_shows_selected_architecture(self):
        html = render_to_string("generated.html", {
            "filename": "ny149",
            "uuid": "00000000-0000-0000-0000-000000000000",
            "platform": "android",
            "android_arch": "armv7",
        })

        self.assertIn("ny149-armv7.apk", html)
        self.assertNotIn("ny149-aarch64.apk", html)
        self.assertNotIn("ny149-x86_64.apk", html)

    def test_missing_download_returns_404(self):
        response = self.client.get("/download", {
            "filename": "missing-aarch64.apk",
            "uuid": "00000000-0000-0000-0000-000000000000",
        })

        self.assertEqual(response.status_code, 404)

    def test_download_rejects_path_traversal(self):
        response = self.client.get("/download", {
            "filename": "../secrets.json",
            "uuid": "00000000-0000-0000-0000-000000000000",
        })

        self.assertEqual(response.status_code, 404)


class SourceResolutionTests(TestCase):
    @patch("rdgenerator.views._github_branch_exists", return_value=True)
    def test_uses_selected_non_official_repository_when_branch_exists(self, branch_exists):
        repository, ref, fallback = _resolve_source({
            "version": "1.4.9",
            "sourceRepository": DIY_REPOSITORY,
        })

        self.assertEqual(repository, DIY_REPOSITORY)
        self.assertEqual(ref, "1.4.9")
        self.assertFalse(fallback)
        branch_exists.assert_called_once_with(DIY_REPOSITORY, "1.4.9")

    @patch("rdgenerator.views._github_branch_exists", return_value=False)
    def test_falls_back_to_official_when_branch_is_missing(self, branch_exists):
        repository, ref, fallback = _resolve_source({
            "version": "1.4.8",
            "sourceRepository": "example/rustdesk",
        })

        self.assertEqual(repository, OFFICIAL_REPOSITORY)
        self.assertEqual(ref, "1.4.8")
        self.assertTrue(fallback)
        branch_exists.assert_called_once_with("example/rustdesk", "1.4.8")

    @patch("rdgenerator.views._github_branch_exists")
    def test_official_repository_does_not_require_branch_lookup(self, branch_exists):
        repository, ref, fallback = _resolve_source({
            "version": "1.4.7",
            "sourceRepository": OFFICIAL_REPOSITORY,
        })

        self.assertEqual(repository, OFFICIAL_REPOSITORY)
        self.assertEqual(ref, "1.4.7")
        self.assertFalse(fallback)
        branch_exists.assert_not_called()


class WorkflowDispatchTests(TestCase):
    @override_settings(
        GHUSER="92376",
        REPONAME="rdgen",
        GHBRANCH="master",
        GHBEARER="test-token",
        ZIP_PASSWORD="test-password",
        GENURL="https://build.example.com",
        RUSTDESK_REPOSITORY="92376/rustdesk-diy",
    )
    @patch("rdgenerator.views._github_branch_exists", return_value=True)
    @patch("rdgenerator.views.requests.post")
    def test_dispatches_diy_repository_and_ref(self, post, branch_exists):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={
                "workflow_run_id": 123,
                "html_url": "https://github.com/92376/rdgen/actions/runs/123",
            }),
        )

        previous_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            os.chdir(temp_dir)
            try:
                result = generate_custom_client(
                    {
                        "exename": "support-client",
                        "platform": "windows",
                        "version": "1.4.9",
                        "sourceRepository": DIY_REPOSITORY,
                        "denyLan": True,
                        "enableDirectIP": True,
                    },
                    "https://build.example.com",
                )
                zip_path = next((temp_dir_path / "temp_zips").glob("secrets_*.zip"))
                with pyzipper.AESZipFile(zip_path) as archive:
                    archive.setpassword(b"test-password")
                    encrypted_inputs = json.loads(archive.read("secrets.json"))
                custom_config = json.loads(base64.b64decode(encrypted_inputs["custom"]))
            finally:
                os.chdir(previous_cwd)

        self.assertTrue(result["success"])
        request_data = post.call_args.kwargs["json"]
        self.assertEqual(request_data["inputs"]["source_repository"], "92376/rustdesk-diy")
        self.assertEqual(request_data["inputs"]["source_ref"], "1.4.9")
        self.assertNotIn("android_arch", request_data["inputs"])
        self.assertEqual(post.call_args.kwargs["timeout"], 20)
        self.assertNotIn("enable-lan-discovery", custom_config)
        self.assertEqual(
            custom_config["default-settings"]["enable-lan-discovery"], "N"
        )
        self.assertEqual(
            custom_config["default-settings"]["direct-server"], "Y"
        )

    @override_settings(
        GHUSER="92376",
        REPONAME="rdgen",
        GHBRANCH="master",
        GHBEARER="test-token",
        ZIP_PASSWORD="test-password",
        GENURL="https://build.example.com",
        RUSTDESK_REPOSITORY="92376/rustdesk-diy",
    )
    @patch("rdgenerator.views._github_branch_exists", return_value=True)
    @patch("rdgenerator.views.requests.post")
    def test_dispatches_android_arch_and_hidden_connection_options(
        self, post, branch_exists
    ):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"workflow_run_id": 124, "html_url": None}),
        )

        previous_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            os.chdir(temp_dir)
            try:
                result = generate_custom_client({
                    "exename": "android-client",
                    "platform": "android",
                    "version": "1.4.9",
                    "sourceRepository": DIY_REPOSITORY,
                    "androidArch": "armv7",
                    "androidBuildHost": "windows",
                    "hideAndroidConnectionNotification": True,
                    "hideAndroidConnectionCard": True,
                }, "https://build.example.com")
                zip_path = next((temp_dir_path / "temp_zips").glob("secrets_*.zip"))
                with pyzipper.AESZipFile(zip_path) as archive:
                    archive.setpassword(b"test-password")
                    encrypted_inputs = json.loads(archive.read("secrets.json"))
                custom_config = json.loads(base64.b64decode(encrypted_inputs["custom"]))
            finally:
                os.chdir(previous_cwd)

        self.assertTrue(result["success"])
        request_data = post.call_args.kwargs["json"]
        self.assertEqual(request_data["inputs"]["android_arch"], "armv7")
        self.assertNotIn("build_host", request_data["inputs"])
        self.assertIn(
            "/actions/workflows/generator-android-windows.yml/dispatches",
            post.call_args.args[0],
        )
        self.assertEqual(
            custom_config["default-settings"]["hide-android-connection-notification"],
            "Y",
        )
        self.assertEqual(
            custom_config["default-settings"]["hide-android-connection-card"],
            "Y",
        )
