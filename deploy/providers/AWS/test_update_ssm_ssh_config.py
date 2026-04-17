#!/usr/bin/env python3
#
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for SSH-over-SSM config generation script.

Tests cover:
- SSH config block generation with correct ProxyCommand and StrictHostKeyChecking
- AWS profile embedding in ProxyCommand
- Idempotent host block replacement and appending
- Preservation of unrelated hosts and comments
- Edge cases: empty files, trailing newlines, existing blocks
"""

from update_ssm_ssh_config import _build_host_block, _replace_or_append_host_block


class TestBuildHostBlock:
    """Tests for _build_host_block() function."""

    def test_build_host_block_basic(self) -> None:
        """Test basic host block generation without profile."""
        block = _build_host_block(alias="flip", instance_id="i-1234567890abcdef0", region="eu-west-2")

        assert "Host flip" in block
        assert "HostName i-1234567890abcdef0" in block
        assert "User ubuntu" in block
        assert "IdentityFile ~/.ssh/host-aws" in block
        assert "StrictHostKeyChecking accept-new" in block
        assert "ProxyCommand" in block
        assert "aws ssm start-session" in block
        assert "--region eu-west-2" in block

    def test_build_host_block_with_profile(self) -> None:
        """Test host block generation with AWS profile embedded."""
        block = _build_host_block(
            alias="flip-prod", instance_id="i-prod1234567890abc", region="eu-west-1", profile="production"
        )

        assert "Host flip-prod" in block
        assert "--profile production" in block
        assert "--region eu-west-1" in block

    def test_build_host_block_contains_strict_host_key_checking(self) -> None:
        """Test that StrictHostKeyChecking=accept-new is present for non-interactive SSH."""
        block = _build_host_block(alias="flip-trust", instance_id="i-trust123456789abc", region="eu-west-2")

        assert "StrictHostKeyChecking accept-new" in block

    def test_build_host_block_format(self) -> None:
        """Test overall format of generated SSH config block."""
        block = _build_host_block(alias="test-host", instance_id="i-test123", region="us-east-1")

        # Should be properly formatted SSH config
        lines = block.strip().split("\n")
        assert lines[0].startswith("# Managed by FLIP")
        assert lines[1].startswith("Host test-host")
        assert all(line.startswith("    ") or line.startswith("#") for line in lines[2:])


class TestReplaceOrAppendHostBlock:
    """Tests for _replace_or_append_host_block() function."""

    def test_append_to_empty_file(self) -> None:
        """Test appending a host block to an empty SSH config."""
        content = ""
        new_block = "Host flip\n    HostName i-123\n"

        result = _replace_or_append_host_block(content, "flip", new_block)

        assert new_block in result
        assert result.strip() == new_block.strip()

    def test_append_to_existing_hosts(self) -> None:
        """Test appending a new host to an existing SSH config."""
        content = "Host existing-host\n    HostName 192.168.1.1\n    User ubuntu\n"
        new_block = "Host flip\n    HostName i-123\n"

        result = _replace_or_append_host_block(content, "flip", new_block)

        assert "Host existing-host" in result
        assert "Host flip" in result
        assert "192.168.1.1" in result

    def test_replace_existing_host_block(self) -> None:
        """Test replacing an existing host block with the same alias."""
        old_content = "Host flip\n    HostName old-ip\n    User ubuntu\n"
        new_block = "Host flip\n    HostName i-new123\n"

        result = _replace_or_append_host_block(old_content, "flip", new_block)

        assert "i-new123" in result
        assert "old-ip" not in result
        assert result.count("Host flip\n") == 1

    def test_replace_preserves_other_hosts(self) -> None:
        """Test that replacing one host doesn't affect others."""
        old_content = (
            "Host github.com\n"
            "    HostName github.com\n"
            "    User git\n"
            "\n"
            "Host flip\n"
            "    HostName old-ip\n"
            "    User ubuntu\n"
            "\n"
            "Host production-server\n"
            "    HostName prod.example.com\n"
            "    User admin\n"
        )
        new_block = "Host flip\n    HostName i-new456\n"

        result = _replace_or_append_host_block(old_content, "flip", new_block)

        # Check github.com (may be at start of file without leading newline)
        assert result.startswith("Host github.com\n") or "\nHost github.com\n" in result
        assert "    HostName github.com\n" in result
        assert "Host production-server" in result
        assert "\n    HostName prod.example.com\n" in result
        assert "i-new456" in result
        assert "old-ip" not in result

    def test_replace_handles_multiline_host_blocks(self) -> None:
        """Test replacing host blocks with multiple configuration lines."""
        old_content = (
            "Host flip\n"
            "    HostName old-id\n"
            "    User ubuntu\n"
            "    IdentityFile ~/.ssh/id_rsa\n"
            "    StrictHostKeyChecking accept-new\n"
        )
        new_block = "Host flip\n    HostName i-new789\n"

        result = _replace_or_append_host_block(old_content, "flip", new_block)

        assert "old-id" not in result
        assert "i-new789" in result
        assert result.count("Host flip\n") == 1

    def test_append_handles_missing_trailing_newline(self) -> None:
        """Test appending to content without trailing newline."""
        content = "Host existing\n    HostName 192.168.1.1"
        new_block = "Host flip\n    HostName i-123\n"

        result = _replace_or_append_host_block(content, "flip", new_block)

        # Both hosts should be present
        assert "Host existing" in result
        assert "Host flip" in result

    def test_preserve_comments_and_blank_lines(self) -> None:
        """Test that comments and blank lines outside host blocks are preserved."""
        old_content = "# SSH Config for FLIP\n# Generated by terraform\n\nHost existing\n    HostName 192.168.1.1\n"
        new_block = "Host flip\n    HostName i-123\n"

        result = _replace_or_append_host_block(old_content, "flip", new_block)

        assert "# SSH Config for FLIP" in result
        assert "# Generated by terraform" in result
        assert "Host existing" in result
        assert "Host flip" in result

    def test_exact_duplicate_host_names_only_replace_exact_match(self) -> None:
        """Test that host matching is exact (flip-trust doesn't match flip)."""
        old_content = "Host flip\n    HostName i-old-flip\n\nHost flip-trust\n    HostName i-old-trust\n"
        new_flip_block = "Host flip\n    HostName i-new-flip\n"

        result = _replace_or_append_host_block(old_content, "flip", new_flip_block)

        assert "i-new-flip" in result
        assert "i-old-flip" not in result
        assert "i-old-trust" in result  # Should be unchanged
        assert result.count("Host flip\n") == 1  # Only the flip host
        assert result.count("Host flip-trust\n") == 1  # flip-trust is unchanged


class TestIntegration:
    """Integration tests for the full SSH config generation workflow."""

    def test_multiple_host_blocks_idempotent(self) -> None:
        """Test that processing the same config multiple times is idempotent."""
        content = ""
        flip_block = _build_host_block(alias="flip", instance_id="i-flip123", region="eu-west-2")
        trust_block = _build_host_block(alias="flip-trust", instance_id="i-trust123", region="eu-west-2")

        # First pass
        content = _replace_or_append_host_block(content, "flip", flip_block)
        content = _replace_or_append_host_block(content, "flip-trust", trust_block)

        content_after_first = content

        # Second pass (simulating re-run with same values)
        content = _replace_or_append_host_block(content, "flip", flip_block)
        content = _replace_or_append_host_block(content, "flip-trust", trust_block)

        # Should be identical (idempotent)
        assert content == content_after_first
        assert content.count("Host flip\n") == 1
        assert content.count("Host flip-trust\n") == 1

    def test_update_both_flip_hosts_preserves_other_config(self) -> None:
        """Test updating both flip and flip-trust while preserving other hosts."""
        original = (
            "Host github.com\n"
            "    HostName github.com\n"
            "\n"
            "Host flip\n"
            "    HostName old-flip-ip\n"
            "\n"
            "Host flip-trust\n"
            "    HostName old-trust-ip\n"
        )

        new_flip = _build_host_block(alias="flip", instance_id="i-new-flip", region="eu-west-2")
        new_trust = _build_host_block(alias="flip-trust", instance_id="i-new-trust", region="eu-west-2")

        result = original
        result = _replace_or_append_host_block(result, "flip", new_flip)
        result = _replace_or_append_host_block(result, "flip-trust", new_trust)

        # Check all expected content
        # github.com may be at start of file without leading newline
        assert result.startswith("Host github.com\n") or "\nHost github.com\n" in result
        assert "    HostName github.com\n" in result
        assert "i-new-flip" in result
        assert "i-new-trust" in result
        assert "old-flip-ip" not in result
        assert "old-trust-ip" not in result
        assert "Host flip" in result
        assert "Host flip-trust" in result
        assert "Host flip-trust" in result
        assert "Host flip-trust" in result
