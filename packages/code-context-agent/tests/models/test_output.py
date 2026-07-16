"""Tests for structured output models."""

import pytest
from pydantic import ValidationError

from code_context_agent.models.output import (
    AnalysisResult,
    ArchitecturalRisk,
    Bundle,
    BusinessLogicItem,
    CodeHealthMetrics,
    GeneratedFile,
    RefactoringCandidate,
)


class TestBusinessLogicItem:
    """Tests for BusinessLogicItem model."""

    def test_valid(self) -> None:
        item = BusinessLogicItem(
            rank=1,
            name="process_payment",
            role="Handles payment processing",
            location="src/payment.py:42",
            score=0.85,
            category="workflows",
        )
        assert item.rank == 1
        assert item.score == 0.85

    def test_score_bounds_low(self) -> None:
        with pytest.raises(ValidationError):
            BusinessLogicItem(
                rank=1,
                name="x",
                role="y",
                location="z:1",
                score=-0.1,
            )

    def test_score_bounds_high(self) -> None:
        with pytest.raises(ValidationError):
            BusinessLogicItem(
                rank=1,
                name="x",
                role="y",
                location="z:1",
                score=1.1,
            )

    def test_category_optional(self) -> None:
        item = BusinessLogicItem(
            rank=1,
            name="x",
            role="y",
            location="z:1",
            score=0.5,
        )
        assert item.category is None


class TestArchitecturalRisk:
    """Tests for ArchitecturalRisk model."""

    def test_valid(self) -> None:
        risk = ArchitecturalRisk(
            description="High coupling between auth and db modules",
            severity="high",
            location="src/auth.py",
            mitigation="Extract shared interface",
        )
        assert risk.severity == "high"

    def test_optional_fields(self) -> None:
        risk = ArchitecturalRisk(description="Some risk", severity="low")
        assert risk.location is None
        assert risk.mitigation is None


class TestGeneratedFile:
    """Tests for GeneratedFile model."""

    def test_valid(self) -> None:
        gf = GeneratedFile(path="CONTEXT.md", line_count=250, description="Main context")
        assert gf.path == "CONTEXT.md"

    def test_negative_lines_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GeneratedFile(path="x.md", line_count=-1, description="test")


class TestAnalysisResult:
    """Tests for AnalysisResult model."""

    def test_minimal(self) -> None:
        result = AnalysisResult(
            status="completed",
            summary="A simple Python app with 50 files.",
            total_files_analyzed=50,
        )
        assert result.status == "completed"
        assert result.business_logic_items == []

    def test_full(self) -> None:
        result = AnalysisResult(
            status="completed",
            summary="Complex app with auth and payments.",
            total_files_analyzed=500,
            business_logic_items=[
                BusinessLogicItem(
                    rank=1,
                    name="login",
                    role="Auth",
                    location="auth.py:10",
                    score=0.9,
                ),
            ],
            risks=[
                ArchitecturalRisk(description="Untested hotspot", severity="high"),
            ],
            generated_files=[
                GeneratedFile(path="CONTEXT.md", line_count=280, description="Main"),
            ],
        )
        assert len(result.business_logic_items) == 1
        assert len(result.risks) == 1

    def test_roundtrip(self) -> None:
        """Test model_dump / model_validate roundtrip."""
        result = AnalysisResult(
            status="completed",
            summary="Test summary.",
            total_files_analyzed=10,
            business_logic_items=[
                BusinessLogicItem(
                    rank=1,
                    name="fn",
                    role="role",
                    location="f:1",
                    score=0.5,
                ),
            ],
        )
        data = result.model_dump()
        restored = AnalysisResult.model_validate(data)
        assert restored == result

    def test_backward_compat_new_fields_optional(self) -> None:
        """New code health fields default gracefully when omitted."""
        result = AnalysisResult(
            status="completed",
            summary="Legacy result without code health data.",
            total_files_analyzed=50,
        )
        assert result.refactoring_candidates == []
        assert result.code_health is None

    def test_with_code_health_fields(self) -> None:
        """AnalysisResult accepts code health data."""
        result = AnalysisResult(
            status="completed",
            summary="Result with code health.",
            total_files_analyzed=100,
            refactoring_candidates=[
                RefactoringCandidate(
                    type="extract_helper",
                    pattern="_collect_text",
                    files=["a.py:34", "b.py:81"],
                    occurrence_count=2,
                    duplicated_lines=7,
                    score=14.0,
                ),
            ],
            code_health=CodeHealthMetrics(
                duplication_percentage=3.2,
                total_clone_groups=5,
                unused_symbol_count=3,
                code_smell_count=8,
            ),
        )
        assert len(result.refactoring_candidates) == 1
        assert result.code_health.duplication_percentage == 3.2
        assert result.code_health.code_smell_count == 8

    def test_code_health_roundtrip(self) -> None:
        """Code health data survives model_dump / model_validate."""
        result = AnalysisResult(
            status="completed",
            summary="Roundtrip test.",
            total_files_analyzed=10,
            refactoring_candidates=[
                RefactoringCandidate(
                    type="dead_code",
                    pattern="orphan_func",
                    files=["x.py"],
                    occurrence_count=1,
                    score=1.0,
                ),
            ],
            code_health=CodeHealthMetrics(unused_symbol_count=1),
        )
        data = result.model_dump()
        restored = AnalysisResult.model_validate(data)
        assert restored == result


class TestBundle:
    """Tests for Bundle model."""

    def test_valid(self) -> None:
        b = Bundle(
            area="auth",
            path="bundles/BUNDLE.auth.md",
            line_count=150,
            summary="Authentication and authorization patterns.",
        )
        assert b.area == "auth"
        assert b.line_count == 150
        assert b.focus_match is False

    def test_focus_match(self) -> None:
        b = Bundle(
            area="security",
            path="bundles/BUNDLE.security.md",
            line_count=200,
            summary="Security analysis.",
            focus_match=True,
        )
        assert b.focus_match is True

    def test_negative_line_count_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Bundle(
                area="x",
                path="x.md",
                line_count=-1,
                summary="test",
            )
