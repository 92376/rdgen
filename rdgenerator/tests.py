import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pyzipper
from django.test import Client, RequestFactory, TestCase, override_settings

from rdgen.settings import _origin_from_url
from .views import _public_base_url, generate_custom_client


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


class WorkflowDispatchTests(TestCase):
    @override_settings(
        GHUSER="92376",
        REPONAME="rdgen",
        GHBRANCH="master",
        GHBEARER="test-token",
        ZIP_PASSWORD="test-password",
        GENURL="https://build.example.com",
        RUSTDESK_REPOSITORY="92376/rustdesk-diy",
        RUSTDESK_REF="1.4.9",
    )
    @patch("rdgenerator.views.requests.post")
    def test_dispatches_diy_repository_and_ref(self, post):
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
                        "denyLan": True,
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
        self.assertEqual(post.call_args.kwargs["timeout"], 20)
        self.assertNotIn("enable-lan-discovery", custom_config)
        self.assertEqual(
            custom_config["default-settings"]["enable-lan-discovery"], "N"
        )
