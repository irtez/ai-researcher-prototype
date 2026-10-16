"""ACL-specific benchmark profile configuration."""

from __future__ import annotations

from writer.base.profiles import ContributionPolicy, NotationPolicy, SectionPolicy, SlotCoveragePolicy


ACL_SECTION_POLICY = SectionPolicy(
    required_sections=("abstract", "introduction", "limitations", "references"),
    ordered_sections=(
        "introduction",
        "related work",
        "method",
        "experiments",
        "results",
        "discussion",
        "conclusion",
        "limitations",
    ),
    aliases={
        "background": "related work",
        "background and related work": "related work",
        "related works": "related work",
        "literature review": "related work",
        "methodology": "method",
        "methods": "method",
        "approach": "method",
        "approaches": "method",
        "proposed method": "method",
        "proposed approach": "method",
        "our method": "method",
        "our approach": "method",
        "model": "method",
        "models": "method",
        "experimental setup": "experiments",
        "experimental evaluation": "experiments",
        "evaluation": "experiments",
        "experiment": "experiments",
        "analysis": "results",
        "results and analysis": "results",
        "results and discussion": "results",
        "discussion": "discussion",
        "conclusion and limitations": "limitations",
        "limitation": "limitations",
        "limitations and future work": "limitations",
        "ethics statement": "ethics",
        "ethical considerations": "ethics",
        "broader impact": "ethics",
        "ethics and broader impact": "ethics",
        "bibliography": "references",
    },
    min_section_words=40,
)

ACL_ABSTRACT_POLICY = SlotCoveragePolicy(
    metric_name="abstract_slot_coverage",
    slots={
        "task": (
            r"\b(?:we\s+(?:study|investigate|address|tackle|explore|consider))\b",
            r"\b(?:this\s+(?:paper|work)\s+(?:studies|investigates|addresses|presents))\b",
            r"\b(?:problem|task|challenge)\b",
        ),
        "method": (
            r"\b(?:we\s+(?:propose|present|introduce|develop|design))\b",
            r"\b(?:our\s+(?:method|approach|framework|model|system))\b",
            r"\b(?:architecture|pipeline|algorithm)\b",
        ),
        "result": (
            r"\b(?:results?\s+(?:show|demonstrate|indicate|suggest))\b",
            r"\b(?:we\s+(?:achieve|obtain|demonstrate|show|outperform))\b",
            r"\b(?:improve|state-of-the-art|sota|baseline)\b",
            r"\b\d+(?:\.\d+)?\s*%\b",
        ),
    },
    empty_message="no abstract found",
)

ACL_CONTRIBUTION_POLICY = ContributionPolicy(
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

ACL_EXPERIMENT_POLICY = SlotCoveragePolicy(
    metric_name="experiment_slot_coverage",
    target_sections=("experiments", "results"),
    slots={
        "datasets": (
            r"\b(?:dataset|benchmark|corpus|corpora)\b",
            r"\b(?:train(?:ing)?\s+(?:set|data|split))\b",
            r"\b(?:test\s+(?:set|data|split))\b",
        ),
        "baselines": (
            r"\b(?:baseline|compared?\s+(?:with|to|against)|previous\s+work)\b",
            r"\b(?:prior\s+(?:method|approach|model|system))\b",
        ),
        "metrics": (
            r"\b(?:metric|accuracy|precision|recall|f[1-9]-?score|bleu|rouge|chrf|meteor)\b",
            r"\b(?:evaluation\s+(?:metric|measure|criterion|criteria))\b",
        ),
        "setup": (
            r"\b(?:implementation|setup|configuration|hardware|gpu|training\s+detail)\b",
            r"\b(?:hyperparameter|learning\s+rate|batch\s+size|epoch|optimizer)\b",
        ),
    },
    empty_message="no experiments/results section found",
)

ACL_REPRO_POLICY = SlotCoveragePolicy(
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

ACL_NOTATION_POLICY = NotationPolicy(
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

ACL_PAGE_LIMIT = 8
ACL_ABSTRACT_WORD_LIMIT = 200
ACL_REVIEW_TEMPLATE_RE = r"\\usepackage(?:\[[^\]]*review[^\]]*\])?\{acl\}"
ACL_TEMPLATE_RE = r"\\usepackage(?:\[[^\]]*\])?\{acl\}"

