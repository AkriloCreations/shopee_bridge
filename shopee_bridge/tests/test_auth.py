"""Tests for Shopee OAuth and refresh logic."""

import pytest
from unittest.mock import Mock, patch
from shopee_bridge import auth


def test_refresh_access_token_if_needed_expired():
    """Test refresh when token is expired."""
    with patch('shopee_bridge.auth._settings') as mock_settings, \
         patch('shopee_bridge.auth._perform_refresh') as mock_perform:
        
        # Mock expired token
        mock_settings.return_value.token_expires_at = "1609459200"  # 2021-01-01
        mock_perform.return_value = True
        
        with patch('shopee_bridge.auth.now_epoch', return_value=1609459201):  # 1 second later
            result = auth.refresh_access_token_if_needed()
            assert result is True
            mock_perform.assert_called_once()


def test_refresh_access_token_if_needed_valid():
    """Test no refresh when token is still valid."""
    with patch('shopee_bridge.auth._settings') as mock_settings, \
         patch('shopee_bridge.auth._perform_refresh') as mock_perform:
        
        # Mock valid token
        mock_settings.return_value.token_expires_at = "1609459200"  # 2021-01-01
        mock_perform.return_value = True
        
        with patch('shopee_bridge.auth.now_epoch', return_value=1609459199):  # 1 second before
            result = auth.refresh_access_token_if_needed()
            assert result is False
            mock_perform.assert_not_called()


def test_get_valid_access_token_no_token():
    """Test get_valid_access_token raises when no token available."""
    with patch('shopee_bridge.auth._settings') as mock_settings:
        mock_settings.return_value.access_token = None
        
        with pytest.raises(auth.AuthRequired, match="No access token available"):
            auth.get_valid_access_token()


def test_get_valid_access_token_with_refresh():
    """Test get_valid_access_token refreshes when needed."""
    with patch('shopee_bridge.auth._settings') as mock_settings, \
         patch('shopee_bridge.auth.refresh_access_token_if_needed') as mock_refresh:
        
        mock_settings.return_value.access_token = "old_token"
        mock_refresh.return_value = True
        
        result = auth.get_valid_access_token()
        assert result == "old_token"
        mock_refresh.assert_called_once()


def test_parse_expiry_to_epoch():
    """Test parsing different expiry formats."""
    from datetime import datetime
    
    # Test string epoch
    result = auth._parse_expiry_to_epoch("1609459200")
    assert result == 1609459200
    
    # Test datetime object
    dt = datetime(2021, 1, 1, 0, 0, 0)
    result = auth._parse_expiry_to_epoch(dt)
    assert result == 1609459200


# Original test
from shopee_bridge.api import connect_to_shopee

result = connect_to_shopee(partner_id="2012422", partner_key="shpk4c546664534a59506f494d7a5078476870525970546c6655516658434465")
print(result)