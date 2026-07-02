import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "app"))

import version_policy as vp


def test_compare_semantic_versions_examples():
    assert vp.compare_semantic_versions("2.25.0", "2.26.0") < 0
    assert vp.compare_semantic_versions("2.26", "2.26.1") < 0
    assert vp.compare_semantic_versions("2.26.1", "2.26.0") >= 0


def test_policy_blocks_when_force_update_and_current_below_minimum():
    policy = vp.evaluate_minimum_version(
        {"min_app_version": "2.26.0", "force_update": True},
        "2.25.9",
    )

    assert policy.is_outdated is True
    assert policy.should_block is True
    assert policy.should_warn is False


def test_policy_warns_when_force_update_false_and_current_below_minimum():
    policy = vp.evaluate_minimum_version(
        {"min_app_version": "2.26.0", "force_update": False},
        "2.25.9",
    )

    assert policy.is_outdated is True
    assert policy.should_block is False
    assert policy.should_warn is True


def test_policy_allows_when_current_version_is_compatible():
    policy = vp.evaluate_minimum_version(
        {"min_app_version": "2.26.0", "force_update": True},
        "2.26.1",
    )

    assert policy.is_outdated is False
    assert policy.should_block is False
    assert policy.should_warn is False


def test_policy_allows_when_minimum_version_is_missing():
    policy = vp.evaluate_minimum_version(
        {"force_update": True},
        "2.25.9",
    )

    assert policy.min_app_version is None
    assert policy.is_outdated is False
    assert policy.should_block is False
    assert policy.should_warn is False
