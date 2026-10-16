"""Default metric policies for the generic writer benchmark package."""

from __future__ import annotations

from .profiles import ContributionPolicy, NotationPolicy, SectionPolicy, SlotCoveragePolicy


BASE_SECTION_POLICY = SectionPolicy(
    required_sections=(
        "abstract",
        "introduction",
        "related work",
        "method",
        "experiments",
        "results",
        "limitations",
        "ethics",
        "references",
    ),
    ordered_sections=(
        "introduction",
        "related work",
        "method",
        "experiments",
        "results",
        "limitations",
        "ethics",
    ),
    aliases={
        "background": "related work",
        "background and related work": "related work",
        "related works": "related work",
        "literature review": "related work",
        "methodology": "method",
        "methods": "method",
        "approach": "method",
        "proposed method": "method",
        "proposed approach": "method",
        "our method": "method",
        "our approach": "method",
        "experimental setup": "experiments",
        "experimental evaluation": "experiments",
        "evaluation": "experiments",
        "setup": "experiments",
        "experiment": "experiments",
        "result": "results",
        "results and discussion": "results",
        "results and analysis": "results",
        "discussion": "results",
        "limitation": "limitations",
        "limitations and future work": "limitations",
        "ethics statement": "ethics",
        "broader impact": "ethics",
        "ethical considerations": "ethics",
        "ethics and broader impact": "ethics",
        "broader impact statement": "ethics",
        "societal impact": "ethics",
        "bibliography": "references",
    },
    min_section_words=40,
)

BASE_ABSTRACT_POLICY = SlotCoveragePolicy(
    metric_name="abstract_slot_coverage",
    slots={
        "task": (
            r"\b(?:we\s+(?:study|investigate|address|tackle|explore|consider|propose))\b",
            r"\b(?:this\s+(?:paper|work)\s+(?:studies|investigates|addresses|proposes|presents))\b",
            r"\b(?:problem|challenge|task)\b",
        ),
        "method": (
            r"\b(?:we\s+(?:propose|present|introduce|develop|design))\b",
            r"\b(?:our\s+(?:method|approach|framework|system|technique))\b",
            r"\b(?:architecture|algorithm|pipeline|model)\b",
        ),
        "result": (
            r"\b(?:results?\s+(?:show|demonstrate|indicate|suggest))\b",
            r"\b(?:we\s+(?:achieve|obtain|demonstrate|show))\b",
            r"\b(?:outperform|improve|state-of-the-art|sota|baseline)\b",
            r"\b\d+(?:\.\d+)?\s*%\b",
        ),
    },
    empty_message="no abstract found",
)

BASE_CONTRIBUTION_POLICY = ContributionPolicy(
    metric_name="contribution_statement_presence",
    patterns=(
        r"\b(?:our\s+(?:main\s+)?contributions?\s+(?:are|include|is))\b",
        r"\b(?:we\s+make\s+the\s+following\s+contributions?)\b",
        r"\b(?:the\s+(?:main\s+)?contributions?\s+of\s+this\s+(?:paper|work))\b",
        r"\\(?:paragraph|textbf)\{[^}]*contribution",
        r"\bcontributions?\s*[:.]",
    ),
    target_sections=("introduction",),
)

BASE_EXPERIMENT_POLICY = SlotCoveragePolicy(
    metric_name="experiment_slot_coverage",
    target_sections=("experiments", "results"),
    slots={
        "datasets": (
            r"\b(?:dataset|benchmark|corpus|corpora)\b",
            r"\b(?:train(?:ing)?\s+(?:set|data|split))\b",
            r"\b(?:test\s+(?:set|data|split))\b",
        ),
        "baselines": (
            r"\b(?:baseline|competitor|compared?\s+(?:with|to|against))\b",
            r"\b(?:prior\s+(?:work|method|approach))\b",
        ),
        "metrics": (
            r"\b(?:metric|accuracy|precision|recall|f[1-9]-?score|bleu|rouge|perplexity)\b",
            r"\b(?:evaluation\s+(?:metric|measure|criterion|criteria))\b",
        ),
        "setup": (
            r"\b(?:implementation|setup|configuration|hardware|gpu|training\s+detail)\b",
            r"\b(?:hyperparameter|learning\s+rate|batch\s+size|epoch|optimizer)\b",
        ),
    },
    empty_message="no experiments/results section found",
)

BASE_REPRODUCIBILITY_POLICY = SlotCoveragePolicy(
    metric_name="reproducibility_slot_coverage",
    target_sections=(),
    slots={
        "code_availability": (
            r"\b(?:code\s+(?:is\s+)?(?:available|released|open[\s-]?source))\b",
            r"\b(?:github|repository|codebase)\b",
            r"\b(?:implementation\s+(?:is\s+)?(?:available|public))\b",
        ),
        "data_availability": (
            r"\b(?:data\s+(?:is\s+)?(?:available|released|public))\b",
            r"\b(?:dataset\s+(?:is\s+)?(?:available|released|public))\b",
        ),
        "training_details": (
            r"\b(?:training|trained)\s+(?:for|with|on|using)\b",
            r"\b(?:epoch|iteration|step|convergence)\b",
        ),
        "hyperparameters": (
            r"\b(?:hyperparameter|learning[\s-]rate|batch[\s-]size|dropout|weight[\s-]decay)\b",
            r"\b(?:Adam|SGD|AdamW|optimizer)\b",
        ),
    },
)

BASE_NOTATION_POLICY = NotationPolicy(
    metric_name="notation_consistency",
    variant_groups=(
        ("dataset", "data set", "data-set"),
        ("finetune", "fine-tune", "fine tune"),
        ("pretrain", "pre-train", "pre train"),
        ("pretraining", "pre-training", "pre training"),
        ("finetuning", "fine-tuning", "fine tuning"),
        ("hyperparameter", "hyper-parameter", "hyper parameter"),
        ("multi-agent", "multiagent", "multi agent"),
        ("non-trivial", "nontrivial", "non trivial"),
        ("real-world", "realworld", "real world"),
        ("state-of-the-art", "state of the art"),
    ),
)

