#!/usr/bin/env python3
"""
Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Email Template Testing Utility for FLIP Email Templates

This script tests and renders email templates locally with placeholder substitution.
It validates template rendering and generates HTML preview files for manual testing.

Templates are loaded from templates/cognito/ and templates/ses/ directories for single
source of truth between Python testing and Terraform/HCL configuration.

Usage:
    python3 test_email_templates.py                    # Test all templates
    python3 test_email_templates.py --output-dir ./previews  # Save to custom directory
    python3 test_email_templates.py --serve           # Start local HTTP server for preview
"""

import argparse
import os
from dataclasses import dataclass
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


@dataclass
class TestUser:
    """Test user data for email template substitution."""

    username: str = "john.smith@example.com"
    temp_password: str = "TempP@ss123456"
    verification_code: str = "789456"
    subdomain: str = "flip-staging.example.com"
    reset_link: str = "https://flip-staging.example.com/reset?token=abc123xyz789"
    reset_link_text: str = "Reset Password"


@dataclass
class TestSesData:
    """Test data for SES email template substitution."""

    name: str = "Jane Doe"
    email: str = "jane.doe@example.com"
    purpose: str = "Research collaboration on federated learning models"
    trust_name: str = "Guy's and St Thomas' NHS Foundation Trust"
    project_name: str = "Brain Tumour Segmentation"
    project_id: str = "BTS-001"
    username: str = "jdoe"
    password: str = "X7k!mP2$vR9n"


class EmailTemplateTester:
    """Tests and renders FLIP email templates loaded from files."""

    def __init__(self, test_user: TestUser | None = None, test_ses: TestSesData | None = None):
        """Initialize the tester with test data and load templates from files.

        Templates are loaded from templates/cognito/ and templates/ses/ directories.
        This ensures Python and Terraform reference the same source files, preventing drift.
        """
        self.test_user = test_user or TestUser()
        self.test_ses = test_ses or TestSesData()

        # Load Cognito templates
        cognito_dir = Path(__file__).parent / "templates" / "cognito"
        self.INVITE_TEMPLATE_HTML = (cognito_dir / "invite.html").read_text()
        self.PASSWORD_RESET_CODE_TEMPLATE_HTML = (cognito_dir / "password_reset_code.html").read_text()
        self.PASSWORD_RESET_LINK_TEMPLATE_HTML = (cognito_dir / "password_reset_link.html").read_text()

        # Load SES templates
        ses_dir = Path(__file__).parent / "templates" / "ses"
        self.ACCESS_REQUEST_TEMPLATE_HTML = (ses_dir / "flip-access-request.html").read_text()
        self.XNAT_CREDENTIALS_TEMPLATE_HTML = (ses_dir / "flip-xnat-credentials.html").read_text()

    def substitute_placeholders(self, template: str) -> tuple[str, dict[str, str]]:
        """
        Substitute Cognito and SES placeholders in email template.

        Returns:
            Tuple of (rendered_html, substitutions_dict)
        """
        substitutions = {
            # Cognito placeholders
            "{username}": self.test_user.username,
            "{####}": self.test_user.verification_code,
            "{flip_alb_subdomain}": self.test_user.subdomain,
            "{reset_link}": self.test_user.reset_link,
            # Cognito link-based placeholder: {## Link Text ##} becomes <a href="reset-url">Link Text</a>
            "{## Reset Password ##}": f'<a href="{self.test_user.reset_link}" style="display: inline-block; padding: 12px 32px; color: #ffffff; text-decoration: none; font-weight: 600; font-size: 16px;">{self.test_user.reset_link_text}</a>',
            # SES placeholders
            "{{name}}": self.test_ses.name,
            "{{email}}": self.test_ses.email,
            "{{purpose}}": self.test_ses.purpose,
            "{{trust_name}}": self.test_ses.trust_name,
            "{{project_name}}": self.test_ses.project_name,
            "{{project_id}}": self.test_ses.project_id,
            "{{username}}": self.test_ses.username,
            "{{password}}": self.test_ses.password,
        }

        rendered = template
        for placeholder, value in substitutions.items():
            rendered = rendered.replace(placeholder, value)

        return rendered, substitutions

    def validate_template(self, template: str, template_name: str) -> dict[str, any]:
        """
        Validate email template for common issues.

        Args:
            template: HTML template string
            template_name: Name of template for reporting

        Returns:
            Validation report with issues and warnings
        """
        issues = []
        warnings = []

        # Check for missing DOCTYPE
        if "<!DOCTYPE html>" not in template:
            warnings.append("Missing DOCTYPE declaration")

        # Check for meta charset
        if 'charset="utf-8"' not in template:
            warnings.append("Missing UTF-8 charset declaration")

        # Check for viewport meta tag
        if "viewport" not in template:
            warnings.append("Missing viewport meta tag (may not render well on mobile)")

        # Check for FLIP branding
        if "#61366e" not in template or "#9452A8" not in template:
            issues.append("Missing FLIP primary color (#61366e or #9452A8)")

        # Check for inline CSS
        if "<style>" in template or "<link rel=" in template:
            warnings.append("Using external stylesheets - inline CSS preferred for email compatibility")

        # Check for table-based layout
        if "<table" not in template:
            warnings.append("Not using table-based layout - may have rendering issues in some email clients")

        # Check for required text elements
        required_text = ["FLIP", "The FLIP Team", "Hello"]
        for text in required_text:
            if text not in template:
                issues.append(f"Missing required text: '{text}'")

        # Validate accessibility
        if "alt=" not in template and "<img" in template:
            warnings.append("Images present without alt text")

        return {
            "template": template_name,
            "valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "total_chars": len(template),
            "estimated_size_kb": len(template) / 1024,
        }

    def render_and_save(self, output_dir: Path) -> None:
        """
        Render all templates with placeholders and save to HTML files.

        Args:
            output_dir: Directory to save HTML preview files
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        templates = [
            ("invite", self.INVITE_TEMPLATE_HTML),
            ("password_reset_code", self.PASSWORD_RESET_CODE_TEMPLATE_HTML),
            ("password_reset_link", self.PASSWORD_RESET_LINK_TEMPLATE_HTML),
            ("access_request", self.ACCESS_REQUEST_TEMPLATE_HTML),
            ("xnat_credentials", self.XNAT_CREDENTIALS_TEMPLATE_HTML),
        ]

        for name, template in templates:
            rendered, subs = self.substitute_placeholders(template)

            # Create preview HTML with metadata
            preview = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>FLIP Email - {name.replace("_", " ").title()}</title>
    <style>
        body {{ font-family: system-ui, sans-serif; background: #f0f0f0; margin: 0; padding: 20px; }}
        .metadata {{ background: #fff; padding: 20px; margin-bottom: 20px; border-radius: 4px; border-left: 4px solid #61366e; }}
        .metadata h2 {{ margin: 0 0 10px 0; color: #301A37; }}
        .metadata p {{ margin: 5px 0; color: #666; font-size: 14px; }}
        .metadata code {{ background: #f5f5f5; padding: 2px 6px; border-radius: 3px; font-family: monospace; }}
        .email-preview {{ background: #fff; border-radius: 4px; overflow: hidden; }}
        .test-info {{ background: #FEF3F2; padding: 10px 20px; border-top: 1px solid #E51170; color: #BF360C; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="metadata">
        <h2>FLIP Email Template Preview</h2>
        <p><strong>Type:</strong> {name.replace("_", " ").title()}</p>
        <p><strong>Size:</strong> {len(rendered) / 1024:.1f} KB</p>
        <p><strong>Test User:</strong> {self.test_user.username}</p>
        <p><strong>Environment:</strong> {self.test_user.subdomain}</p>
        <hr>
        <p><strong>Substitutions Applied:</strong></p>
        <ul>
"""
            for placeholder, value in subs.items():
                preview += f"            <li><code>{placeholder}</code> → <code>{value}</code></li>\n"

            preview += f"""        </ul>
    </div>
    <div class="email-preview">
        {rendered}
    </div>
    <div class="test-info">
        Open this file in your email client or upload to Litmus/Email-on-Acid for cross-client testing.
    </div>
</body>
</html>
"""

            output_file = output_dir / f"flip_email_{name}.html"
            output_file.write_text(preview)
            print(f"✓ Saved: {output_file}")

    def test_all(self) -> list[dict]:
        """Run all validation tests.

        Returns:
            List of test results for each template
        """
        templates = [
            ("Temporary Password Invitation", self.INVITE_TEMPLATE_HTML),
            ("Password Reset (Code)", self.PASSWORD_RESET_CODE_TEMPLATE_HTML),
            ("Password Reset (Link)", self.PASSWORD_RESET_LINK_TEMPLATE_HTML),
            ("Access Request", self.ACCESS_REQUEST_TEMPLATE_HTML),
            ("XNAT Credentials", self.XNAT_CREDENTIALS_TEMPLATE_HTML),
        ]

        results = []
        for name, template in templates:
            validation = self.validate_template(template, name)
            rendered, subs = self.substitute_placeholders(template)

            results.append({
                "name": name,
                "validation": validation,
                "substitutions": subs,
                "rendered_size_kb": len(rendered) / 1024,
            })

        return results


def print_test_results(results: list) -> None:
    """Pretty-print test results."""
    print("\n" + "=" * 80)
    print("FLIP EMAIL TEMPLATE VALIDATION REPORT".center(80))
    print("=" * 80 + "\n")

    for result in results:
        name = result["name"]
        validation = result["validation"]

        status = "✓ PASS" if validation["valid"] else "✗ FAIL"
        print(f"{status} | {name}")
        print(f"  Size: {result['rendered_size_kb']:.1f} KB")

        if validation["issues"]:
            print("  Issues:")
            for issue in validation["issues"]:
                print(f"    ✗ {issue}")

        if validation["warnings"]:
            print("  Warnings:")
            for warning in validation["warnings"]:
                print(f"    ⚠ {warning}")

        print()

    print("=" * 80)
    print(f"Total templates tested: {len(results)}")
    passed = sum(1 for r in results if r["validation"]["valid"])
    print(f"Passed: {passed}/{len(results)}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Test FLIP Cognito email templates locally")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./email_previews"),
        help="Directory to save HTML preview files (default: ./email_previews)",
    )
    parser.add_argument(
        "--username",
        default="john.smith@example.com",
        help="Test username to substitute (default: john.smith@example.com)",
    )
    parser.add_argument(
        "--subdomain",
        default="flip-staging.example.com",
        help="FLIP subdomain to substitute (default: flip-staging.example.com)",
    )
    parser.add_argument(
        "--serve", action="store_true", help="Start local HTTP server to view previews (default port: 8000)"
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for local HTTP server (default: 8000)")

    args = parser.parse_args()

    # Create test user with provided arguments
    test_user = TestUser(username=args.username, subdomain=args.subdomain)

    # Run tests
    tester = EmailTemplateTester(test_user)
    results = tester.test_all()
    print_test_results(results)

    # Save previews
    print(f"\nSaving email previews to: {args.output_dir.absolute()}")
    tester.render_and_save(args.output_dir)

    # Optionally serve previews
    if args.serve:
        print(f"\n📧 Starting local HTTP server at http://localhost:{args.port}")
        print("Open the following URLs in your browser:")
        for name in ["invite", "password_reset_code", "password_reset_link", "access_request", "xnat_credentials"]:
            print(f"  • http://localhost:{args.port}/flip_email_{name}.html")
        print("\nPress Ctrl+C to stop the server.\n")

        os.chdir(args.output_dir)
        handler = SimpleHTTPRequestHandler
        server = HTTPServer(("localhost", args.port), handler)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n\nServer stopped.")


if __name__ == "__main__":
    main()
