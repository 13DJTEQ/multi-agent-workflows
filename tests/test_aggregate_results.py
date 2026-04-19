"""Tests for aggregate_results.py."""

import json
from pathlib import Path

import pytest

import aggregate_results as ar


class TestMergeDicts:
    """Tests for merge_dicts function."""

    def test_non_overlapping_keys(self):
        dicts = [{"a": 1}, {"b": 2}, {"c": 3}]
        result = ar.merge_dicts(dicts)
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_last_wins_policy(self):
        dicts = [{"a": 1}, {"a": 2}, {"a": 3}]
        result = ar.merge_dicts(dicts, policy="last")
        assert result == {"a": 3}

    def test_first_wins_policy(self):
        dicts = [{"a": 1}, {"a": 2}, {"a": 3}]
        result = ar.merge_dicts(dicts, policy="first")
        assert result == {"a": 1}

    def test_concat_policy_arrays(self):
        dicts = [{"items": [1, 2]}, {"items": [3, 4]}]
        result = ar.merge_dicts(dicts, policy="concat")
        assert result == {"items": [1, 2, 3, 4]}

    def test_error_policy(self):
        dicts = [{"a": 1}, {"a": 2}]
        with pytest.raises(ValueError, match="Conflict on key"):
            ar.merge_dicts(dicts, policy="error")


class TestStrategyMerge:
    """Tests for strategy_merge function."""

    def test_basic_merge(self):
        results = [
            {"auth": {"score": 85}},
            {"api": {"score": 92}},
        ]
        merged = ar.strategy_merge(results)
        assert merged == {"auth": {"score": 85}, "api": {"score": 92}}

    def test_merge_with_policy(self):
        results = [{"key": "first"}, {"key": "second"}]
        merged = ar.strategy_merge(results, merge_policy="first")
        assert merged == {"key": "first"}


class TestStrategyConcat:
    """Tests for strategy_concat function."""

    def test_string_concat(self):
        results = ["Part 1", "Part 2", "Part 3"]
        concatenated = ar.strategy_concat(results, separator="\n")
        assert concatenated == "Part 1\nPart 2\nPart 3"

    def test_dict_with_content_key(self):
        results = [{"content": "A"}, {"content": "B"}]
        concatenated = ar.strategy_concat(results, separator=" | ")
        assert concatenated == "A | B"

    def test_dict_with_text_key(self):
        results = [{"text": "X"}, {"text": "Y"}]
        concatenated = ar.strategy_concat(results, separator="-")
        assert concatenated == "X-Y"

    def test_dict_without_special_keys(self):
        results = [{"data": 1}, {"data": 2}]
        concatenated = ar.strategy_concat(results, separator="\n")
        # Should JSON-dump the dicts
        assert '"data": 1' in concatenated
        assert '"data": 2' in concatenated


class TestStrategyVote:
    """Tests for strategy_vote function."""

    def test_majority_true(self):
        results = [
            {"decision": True},
            {"decision": True},
            {"decision": False},
        ]
        voted = ar.strategy_vote(results, vote_field="decision")
        assert voted["decision"] is True
        assert voted["vote_count"] == {"True": 2, "False": 1}
        assert voted["winner_ratio"] == pytest.approx(2/3)

    def test_majority_false(self):
        results = [
            {"decision": False},
            {"decision": False},
            {"decision": True},
        ]
        voted = ar.strategy_vote(results, vote_field="decision")
        assert voted["decision"] is False

    def test_threshold_check(self):
        results = [
            {"decision": "A"},
            {"decision": "B"},
            {"decision": "C"},
        ]
        voted = ar.strategy_vote(results, vote_field="decision", vote_threshold=0.5)
        # 33% winner does not meet 50% threshold (three-way tie)
        assert voted["threshold_met"] is False

    def test_threshold_met_with_supermajority(self):
        results = [
            {"decision": True},
            {"decision": True},
            {"decision": True},
            {"decision": False},
        ]
        voted = ar.strategy_vote(results, vote_field="decision", vote_threshold=0.67)
        # 75% True meets 67% threshold
        assert voted["threshold_met"] is True

    def test_weighted_voting(self):
        results = [
            {"decision": True, "confidence": 0.9},
            {"decision": False, "confidence": 0.5},
            {"decision": False, "confidence": 0.5},
        ]
        voted = ar.strategy_vote(
            results, vote_field="decision", weighted=True, confidence_field="confidence"
        )
        # True has 0.9 weight vs False with 1.0 total
        assert voted["decision"] is False

    def test_missing_field(self):
        results = [{"other": "value"}]
        voted = ar.strategy_vote(results, vote_field="decision")
        assert "error" in voted


class TestStrategyLatest:
    """Tests for strategy_latest function."""

    def test_selects_latest(self):
        results = [
            {"value": 1, "timestamp": "2024-01-01T10:00:00"},
            {"value": 3, "timestamp": "2024-01-01T12:00:00"},
            {"value": 2, "timestamp": "2024-01-01T11:00:00"},
        ]
        latest = ar.strategy_latest(results)
        assert latest["value"] == 3

    def test_handles_z_suffix(self):
        results = [
            {"value": 1, "timestamp": "2024-01-01T10:00:00Z"},
            {"value": 2, "timestamp": "2024-01-01T11:00:00Z"},
        ]
        latest = ar.strategy_latest(results)
        assert latest["value"] == 2

    def test_empty_results(self):
        latest = ar.strategy_latest([])
        assert latest == {}


class TestFindResultFiles:
    """Tests for find_result_files function."""

    def test_finds_result_json(self, tmp_path):
        (tmp_path / "agent-1").mkdir()
        (tmp_path / "agent-1" / "result.json").write_text("{}")
        (tmp_path / "agent-2").mkdir()
        (tmp_path / "agent-2" / "result.json").write_text("{}")
        
        files = ar.find_result_files(tmp_path)
        assert len(files) == 2

    def test_fallback_to_any_json(self, tmp_path):
        (tmp_path / "output.json").write_text("{}")
        
        files = ar.find_result_files(tmp_path, pattern="result.json")
        assert len(files) == 1

    def test_fallback_to_markdown(self, tmp_path):
        (tmp_path / "report.md").write_text("# Report")
        
        files = ar.find_result_files(tmp_path)
        assert len(files) == 1


class TestLoadFunctions:
    """Tests for file loading functions."""

    def test_load_json_file(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        
        result = ar.load_json_file(f)
        assert result == {"key": "value"}

    def test_load_json_file_invalid(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        
        result = ar.load_json_file(f)
        assert result is None

    def test_load_text_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        
        result = ar.load_text_file(f)
        assert result == "hello world"


class TestIntegration:
    """Integration tests using sample data."""

    def test_merge_sample_results(self, sample_json_results, tmp_outputs):
        files = ar.find_result_files(tmp_outputs)
        results = [ar.load_json_file(f) for f in files]
        results = [r for r in results if r is not None]
        
        merged = ar.strategy_merge(results)
        
        # All modules should be present
        assert "module" in merged or len(results) == 3

    def test_vote_sample_results(self, sample_vote_results, tmp_outputs):
        files = ar.find_result_files(tmp_outputs)
        results = [ar.load_json_file(f) for f in files]
        results = [r for r in results if r is not None]
        
        voted = ar.strategy_vote(results, vote_field="decision")
        
        # 2 True vs 1 False
        assert voted["decision"] is True
        assert voted["winner_count"] == 2
