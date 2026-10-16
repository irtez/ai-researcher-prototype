from review import (
    review_pdf_paper,
    review_pdf_paper_by_url,
    ReviewMetrics,
    download_pdf_paper,
)

from batch_review import (
    review_with_repeats,
    review_dataset,
    ensemble_review,
    ConsistencyResult,
)

from faithfulness import (
    check_faithfulness,
    check_review_faithfulness,
    FaithfulnessClaim,
    FaithfulnessCheck,
)

from context_control import (
    truncate_text,
    summarize_paper,
    prepare_paper_for_review,
    prepare_paper_for_review_async,
    estimate_tokens,
)

from eval_pipeline import (
    evaluate_reviews,
    evaluate_consistency,
    save_eval_metrics,
    load_reviews_from_jsonl,
    EvalMetrics,
)

from models import (
    ReviewResponse,
    ExtendedReviewResponse,
    AspectItem,
    WEIGHTS,
    compute_overall_score,
    map_to_overall_score,
    # Mode constants
    REVIEW_MODE_GENERAL,
    REVIEW_MODE_WRITER_SPECIFIC_JUDGE,
    SUPPORTED_REVIEW_MODES,
    ReviewMode,
    validate_review_mode,
)

from writer_specific_judge import (
    GroupedRubricReviewResponse,
    RubricGroupResult,
    run_grouped_rubric_review,
)

from prompts import REVIEW_PROMPT

# NEW: Section parsing (Tier 1, Item 1)
from section_parser import (
    parse_sections_from_text,
    ParsedPaper,
    PaperSection,
    extract_keywords_from_sections,
    format_related_work_context,
)

# NEW: arXiv search (Tier 1, Item 3)
from arxiv_search import (
    extract_keywords_from_paper,
    search_arxiv_papers,
    find_highly_similar_papers,
    enrich_with_related_work,
)

# NEW: A3PR pipeline (Tier 2, Item 4)
from a3pr_pipeline import (
    run_a3pr_review,
    run_analysis_stage,
    run_argument_stage,
    run_assessment_stage,
    assemble_final_review,
    AnalysisResult,
    ArgumentResult,
    AssessmentResult,
)

# NEW: Self-critique (Tier 2, Item 5)
from self_critique import (
    critique_review,
    review_with_self_critique,
    CritiqueResult,
)

# NEW: ICLR benchmark
from benchmark.iclr_dataset import (
    ICLRReview,
    download_iclr_dataset,
    load_iclr_reviews,
    parse_iclr_json,
    normalize_score_10_to_6,
    create_benchmark_golden_set,
)

from benchmark.iclr_adapter import (
    ICLRRawReview,
    normalize_iclr_to_extended,
    load_golden_review,
    compare_reviews,
)

__all__ = [
    "review_pdf_paper",
    "review_pdf_paper_by_url",
    "ReviewMetrics",
    "download_pdf_paper",
    
    "review_with_repeats",
    "review_dataset",
    "ensemble_review",
    "ConsistencyResult",
    
    # Faithfulness
    "check_faithfulness",
    "check_review_faithfulness",
    "FaithfulnessClaim",
    "FaithfulnessCheck",
    
    # Context
    "truncate_text",
    "summarize_paper",
    "prepare_paper_for_review",
    "prepare_paper_for_review_async",
    "estimate_tokens",
    
    # Evaluation
    "evaluate_reviews",
    "evaluate_consistency",
    "save_eval_metrics",
    "load_reviews_from_jsonl",
    "EvalMetrics",
    
    # Models (enhanced)
    "ReviewResponse",
    "ExtendedReviewResponse",
    "AspectItem",
    "WEIGHTS",
    "compute_overall_score",
    "map_to_overall_score",

    # Review mode selector
    "REVIEW_MODE_GENERAL",
    "REVIEW_MODE_WRITER_SPECIFIC_JUDGE",
    "SUPPORTED_REVIEW_MODES",
    "ReviewMode",
    "validate_review_mode",

    # Writer-specific judge
    "GroupedRubricReviewResponse",
    "RubricGroupResult",
    "run_grouped_rubric_review",
    
    # Prompts
    "REVIEW_PROMPT",
    
    # NEW: Section parsing
    "parse_sections_from_text",
    "ParsedPaper",
    "PaperSection",
    "extract_keywords_from_sections",
    "format_related_work_context",
    
    # NEW: arXiv search
    "extract_keywords_from_paper",
    "search_arxiv_papers",
    "find_highly_similar_papers",
    "enrich_with_related_work",
    
    # NEW: A3PR pipeline
    "run_a3pr_review",
    "run_analysis_stage",
    "run_argument_stage",
    "run_assessment_stage",
    "assemble_final_review",
    "AnalysisResult",
    "ArgumentResult",
    "AssessmentResult",
    
    "critique_review",
    "review_with_self_critique",
    "CritiqueResult",

    "ICLRReview",
    "download_iclr_dataset",
    "load_iclr_reviews",
    "parse_iclr_json",
    "normalize_score_10_to_6",
    "create_benchmark_golden_set",
    "ICLRRawReview",
    "normalize_iclr_to_extended",
    "load_golden_review",
    "compare_reviews",
]

