"""Tests for scripts/dependency_graph.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import dependency_graph as dg


def make_manifest(tmp_path: Path, tasks: list[dict], fmt: str = "json") -> Path:
    data = {"tasks": tasks}
    if fmt == "json":
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps(data))
    else:
        pytest.importorskip("yaml")
        import yaml

        p = tmp_path / "manifest.yaml"
        p.write_text(yaml.safe_dump(data))
    return p


class TestLoadManifest:
    def test_load_json(self, tmp_path):
        p = make_manifest(tmp_path, [{"id": "a", "prompt": "do a"}])
        tasks = dg.load_manifest(p)
        assert len(tasks) == 1
        assert tasks[0].id == "a"
        assert tasks[0].prompt == "do a"
        assert tasks[0].depends_on == []

    def test_load_yaml(self, tmp_path):
        pytest.importorskip("yaml")
        p = make_manifest(
            tmp_path,
            [{"id": "a", "prompt": "do a"}, {"id": "b", "prompt": "do b", "depends_on": ["a"]}],
            fmt="yaml",
        )
        tasks = dg.load_manifest(p)
        assert len(tasks) == 2
        assert tasks[1].depends_on == ["a"]

    def test_missing_required(self, tmp_path):
        p = make_manifest(tmp_path, [{"id": "a"}])
        with pytest.raises(ValueError, match="required fields"):
            dg.load_manifest(p)

    def test_not_a_dict(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps([{"id": "a", "prompt": "p"}]))
        with pytest.raises(ValueError, match="'tasks' key"):
            dg.load_manifest(p)


class TestTopoSort:
    def test_linear_chain(self):
        tasks = [
            dg.Task("c", "c", depends_on=["b"]),
            dg.Task("a", "a"),
            dg.Task("b", "b", depends_on=["a"]),
        ]
        phases = dg.topo_sort(tasks)
        assert len(phases) == 3
        assert [t.id for t in phases[0]] == ["a"]
        assert [t.id for t in phases[1]] == ["b"]
        assert [t.id for t in phases[2]] == ["c"]

    def test_diamond(self):
        tasks = [
            dg.Task("scan", "scan"),
            dg.Task("sec", "sec", depends_on=["scan"]),
            dg.Task("perf", "perf", depends_on=["scan"]),
            dg.Task("synth", "synth", depends_on=["sec", "perf"]),
        ]
        phases = dg.topo_sort(tasks)
        assert len(phases) == 3
        assert [t.id for t in phases[0]] == ["scan"]
        assert [t.id for t in phases[1]] == ["perf", "sec"]  # sorted by id
        assert [t.id for t in phases[2]] == ["synth"]

    def test_parallel_roots(self):
        tasks = [
            dg.Task("a", "a"),
            dg.Task("b", "b"),
            dg.Task("c", "c"),
        ]
        phases = dg.topo_sort(tasks)
        assert len(phases) == 1
        assert [t.id for t in phases[0]] == ["a", "b", "c"]

    def test_cycle_detected(self):
        tasks = [
            dg.Task("a", "a", depends_on=["b"]),
            dg.Task("b", "b", depends_on=["a"]),
        ]
        with pytest.raises(ValueError, match="Cycle detected"):
            dg.topo_sort(tasks)


class TestValidate:
    def test_unknown_dependency(self):
        tasks = [dg.Task("a", "a", depends_on=["ghost"])]
        errors = dg.validate(tasks)
        assert any("unknown task" in e for e in errors)

    def test_self_dependency(self):
        tasks = [dg.Task("a", "a", depends_on=["a"])]
        errors = dg.validate(tasks)
        assert any("itself" in e for e in errors)

    def test_duplicate_ids(self):
        tasks = [dg.Task("a", "a"), dg.Task("a", "b")]
        errors = dg.validate(tasks)
        assert any("Duplicate task id" in e for e in errors)

    def test_clean_manifest(self):
        tasks = [
            dg.Task("a", "a"),
            dg.Task("b", "b", depends_on=["a"]),
        ]
        assert dg.validate(tasks) == []


class TestPlanToDict:
    def test_shape(self):
        tasks = [
            dg.Task("a", "A"),
            dg.Task("b", "B", depends_on=["a"]),
        ]
        phases = dg.topo_sort(tasks)
        d = dg.plan_to_dict(phases)
        assert d["num_phases"] == 2
        assert d["total_tasks"] == 2
        assert d["phases"][0]["phase"] == 1
        assert d["phases"][0]["parallelism"] == 1
        assert d["phases"][1]["tasks"][0]["depends_on"] == ["a"]


class TestFormatText:
    def test_includes_phases(self):
        tasks = [dg.Task("a", "do a")]
        out = dg.format_text(dg.topo_sort(tasks))
        assert "Phase 1" in out
        assert "a: do a" in out


class TestFormatDot:
    def test_emits_edges(self):
        tasks = [dg.Task("a", "a"), dg.Task("b", "b", depends_on=["a"])]
        out = dg.format_dot(tasks, dg.topo_sort(tasks))
        assert "digraph" in out
        assert '"a" -> "b"' in out
