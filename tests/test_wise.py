"""Tests for Wise API client.

TDD: Write tests first, then implement the client.
"""

import pytest


class TestWiseClient:
    """Tests for Wise API integration."""

    @pytest.mark.asyncio
    async def test_get_profiles_returns_list(self):
        """Test that get_profiles returns a list of business profiles."""
        # TODO: Implement WiseClient and this test
        # client = WiseClient(token="test", private_key_path="test.pem")
        # profiles = await client.get_profiles()
        # assert isinstance(profiles, list)
        pytest.skip("WiseClient not yet implemented - TDD placeholder")

    @pytest.mark.asyncio
    async def test_get_balances_for_profile(self):
        """Test that get_balances returns currency balances for a profile."""
        # TODO: Implement
        pytest.skip("WiseClient not yet implemented - TDD placeholder")

    @pytest.mark.asyncio
    async def test_get_transactions_requires_sca(self):
        """Test that get_transactions handles SCA signing."""
        # TODO: Implement
        pytest.skip("WiseClient not yet implemented - TDD placeholder")

    @pytest.mark.asyncio
    async def test_sign_ott_produces_valid_signature(self):
        """Test that OTT signing produces a valid base64 signature."""
        # TODO: Implement
        pytest.skip("WiseClient not yet implemented - TDD placeholder")
