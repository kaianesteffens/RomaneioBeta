#!/usr/bin/env python
"""Test script for ConfigManager."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "app" / "fretio" / "src"))

from fretio.config_manager import ConfigManager

def test_singleton():
    """Test singleton pattern."""
    print("Test 1: Singleton pattern...")
    cm1 = ConfigManager.get_instance("test1")
    cm2 = ConfigManager.get_instance("test1")
    assert cm1 is cm2, "Singleton failed"
    print("  ✓ Singleton works")

def test_multiple_companies():
    """Test multiple companies."""
    print("\nTest 2: Multiple companies...")
    cm1 = ConfigManager.get_instance("company1")
    cm2 = ConfigManager.get_instance("company2")
    assert cm1 is not cm2, "Different companies should have different instances"
    assert cm1.empresa_nome == "company1", "Company name mismatch"
    assert cm2.empresa_nome == "company2", "Company name mismatch"
    print("  ✓ Multiple companies work")

def test_config_loading():
    """Test configuration loading."""
    print("\nTest 3: Configuration loading...")
    cm = ConfigManager.get_instance("darlu")
    config = cm.load_config()
    assert isinstance(config, dict), "Config should be dict"
    assert len(config) > 0, "Config should not be empty"
    print(f"  ✓ Config loaded with sections: {list(config.keys())}")

def test_get_methods():
    """Test get methods."""
    print("\nTest 4: Get methods...")
    cm = ConfigManager.get_instance("darlu")
    
    # Test get_section
    romaneio = cm.get_section("romaneio")
    assert isinstance(romaneio, dict), "get_section should return dict"
    print(f"  ✓ get_section('romaneio') = {romaneio}")
    
    # Test get with default
    val = cm.get("romaneio", "cep_origem", default="default")
    assert val != "default", "Should find cep_origem"
    print(f"  ✓ get('romaneio', 'cep_origem') = {val}")
    
    # Test get with non-existent key
    val = cm.get("non_existent", "key", default="my_default")
    assert val == "my_default", "Should return default for non-existent"
    print(f"  ✓ get() returns default for non-existent key")

def test_validation():
    """Test validation."""
    print("\nTest 5: Validation...")
    cm = ConfigManager.get_instance("test_validation")
    is_valid = cm.validate()
    assert isinstance(is_valid, bool), "validate should return bool"
    print(f"  ✓ Validation returned: {is_valid}")

def test_path_precedence():
    """Test config path precedence."""
    print("\nTest 6: Path precedence...")
    cm = ConfigManager.get_instance("darlu")
    paths = cm._get_config_paths()
    assert len(paths) > 0, "Should have config paths"
    
    # First path should be APPDATA
    appdata_path_str = r"AppData\Roaming\Fretio\empresas\darlu"
    is_appdata_first = appdata_path_str.lower() in str(paths[0]).lower()
    print(f"  ✓ First path is APPDATA: {is_appdata_first}")
    print(f"    Paths checked: {len(paths)}")

def test_caching():
    """Test configuration caching."""
    print("\nTest 7: Caching...")
    cm = ConfigManager.get_instance("cache_test")
    
    cfg1 = cm.load_config()
    cfg2 = cm.load_config()
    assert cfg1 is cfg2, "Should return same cached object"
    print("  ✓ Caching works (same object returned)")

def test_fallback():
    """Test fallback configuration."""
    print("\nTest 8: Fallback configuration...")
    cm = ConfigManager.get_instance("nonexistent_company")
    fallback = cm.get_fallback()
    assert isinstance(fallback, dict), "Fallback should be dict"
    assert "fretio" in fallback or len(fallback) > 0, "Fallback should have sections"
    print(f"  ✓ Fallback config has sections: {list(fallback.keys())}")

def test_reload_clears_registered_caches():
    """Test reload invalidates derived caches without deadlocking."""
    print("\nTest 9: Reload cache invalidation...")
    cm = ConfigManager.get_instance("reload_test")
    config_path = Path(__file__).parent / "reload_test_CONFIG.toml"
    config_path.write_text("[romaneio]\ncep_origem = \"12345678\"\n", encoding="utf-8")

    cleared = {"count": 0}

    def clearer():
        cleared["count"] += 1

    original_get_paths = cm._get_config_paths
    ConfigManager.register_cache_clearer(clearer)
    try:
        cm._get_config_paths = lambda: [config_path]
        cm._config_data = {"romaneio": {"cep_origem": "00000000"}}
        before_token = cm.get_cache_token()
        reloaded = cm.reload()
        after_token = cm.get_cache_token()

        assert reloaded.get("romaneio", {}).get("cep_origem") == "12345678", "Reload should read updated file"
        assert cleared["count"] >= 1, "Reload should clear registered caches"
        assert after_token[1] > before_token[1], "Cache token should change after reload"
        print("  ✓ Reload invalidates registered caches")
    finally:
        cm._get_config_paths = original_get_paths
        ConfigManager.unregister_cache_clearer(clearer)
        if config_path.exists():
            config_path.unlink()

if __name__ == "__main__":
    print("="*60)
    print("ConfigManager Test Suite")
    print("="*60)
    
    try:
        test_singleton()
        test_multiple_companies()
        test_config_loading()
        test_get_methods()
        test_validation()
        test_path_precedence()
        test_caching()
        test_fallback()
        test_reload_clears_registered_caches()
        
        print("\n" + "="*60)
        print("✅ All tests passed!")
        print("="*60)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def test_config_manager_fallback_includes_translovato_section():
    config = ConfigManager.get_instance("fallback-translovato-test").get_fallback()

    translovato = config["transportadoras"]["translovato"]
    assert translovato["habilitado"] is False
    assert translovato["cnpj"] == ""
    assert translovato["usuario"] == ""
    assert translovato["senha"] == ""
    assert translovato["produto"] == "CONFECCAO"


def test_config_manager_fallback_includes_default_payer_document_field():
    config = ConfigManager.get_instance("fallback-payer-document-test").get_fallback()

    assert config["romaneio"]["cnpj_pagador_padrao"] == ""
