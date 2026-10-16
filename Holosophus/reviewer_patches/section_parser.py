import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class PaperSection:
    """A single section of the paper."""
    name: str
    content: str
    start_page: int = 0
    token_count: int = 0


@dataclass
class ParsedPaper:
    """Paper parsed into named sections."""
    sections: List[PaperSection] = field(default_factory=list)
    full_text: str = ""
    references_section: Optional[PaperSection] = None
    abstract: Optional[PaperSection] = None
    introduction: Optional[PaperSection] = None
    methods: Optional[PaperSection] = None
    experiments: Optional[PaperSection] = None
    conclusion: Optional[PaperSection] = None
    
    def get_main_text(self, include_references: bool = False) -> str:
        """Get main text excluding references (saves 15-25% tokens)."""
        parts = []
        for section in self.sections:
            if section.name.lower() in ["references", "bibliography"]:
                if include_references:
                    parts.append(section.content)
                continue
            parts.append(section.content)
        return "\n\n".join(parts)
    
    def to_structured_prompt(self) -> str:
        """Convert to structured format for LLM with section markers."""
        parts = []
        for section in self.sections:
            if section.name.lower() in ["references", "bibliography"]:
                continue 
            parts.append(f"## {section.name}\n\n{section.content}")
        return "\n\n".join(parts)


SECTION_PATTERNS = [
    (r"(?i)^(abstract)", "Abstract"),
    (r"(?i)^(\d+\.?\s*introduction)", "Introduction"),
    (r"(?i)^(\d+\.?\s*(related work|background|literature review))", "Related Work"),
    (r"(?i)^(\d+\.?\s*(method|methods|methodology|approach|model|architecture))", "Methods"),
    (r"(?i)^(\d+\.?\s*(experiment|experiments|evaluation|results|findings))", "Experiments"),
    (r"(?i)^(\d+\.?\s*(discussion|analysis))", "Discussion"),
    (r"(?i)^(\d+\.?\s*(conclusion|conclusions|summary|future work))", "Conclusion"),
    (r"(?i)^(references|bibliography)", "References"),
]


def parse_sections_from_text(text: str) -> ParsedPaper:
    """
    Parse paper text into named sections using regex patterns
    """
    lines = text.split("\n")
    sections: List[PaperSection] = []
    current_section: Optional[str] = None
    current_content: List[str] = []
    current_start_page = 1
    
    word_count = 0
    words_per_page = 800
    
    for i, line in enumerate(lines):
        is_section_header = False
        section_name = None
        
        for pattern, name in SECTION_PATTERNS:
            if re.match(pattern, line.strip()):
                is_section_header = True
                section_name = name
                break
        
        if is_section_header:
            if current_section and current_content:
                content = "\n".join(current_content)
                sections.append(PaperSection(
                    name=current_section,
                    content=content,
                    start_page=current_start_page,
                    token_count=len(content) // 4,
                ))
            
            current_section = section_name
            current_content = []
            current_start_page = (word_count // words_per_page) + 1
        else:
            current_content.append(line)
            word_count += len(line.split())
    
    if current_section and current_content:
        content = "\n".join(current_content)
        sections.append(PaperSection(
            name=current_section,
            content=content,
            start_page=current_start_page,
            token_count=len(content) // 4,
        ))
    
    parsed = ParsedPaper(sections=sections, full_text=text)
    
    for section in sections:
        name_lower = section.name.lower()
        if name_lower == "abstract":
            parsed.abstract = section
        elif name_lower == "introduction":
            parsed.introduction = section
        elif name_lower in ["methods", "methodology", "approach", "model", "architecture"]:
            parsed.methods = section
        elif name_lower in ["experiments", "evaluation", "results", "findings"]:
            parsed.experiments = section
        elif name_lower in ["conclusion", "conclusions", "summary", "future work"]:
            parsed.conclusion = section
        elif name_lower in ["references", "bibliography"]:
            parsed.references_section = section
    
    logger.info(f"Parsed paper into {len(sections)} sections")
    for section in sections:
        logger.debug(f"  {section.name}: {section.token_count} tokens (page {section.start_page})")
    
    return parsed


def extract_keywords_from_sections(parsed_paper: ParsedPaper, max_keywords: int = 10) -> List[str]:
    """
    Extract key terms from Abstract + Methods for arXiv search.
    """
    key_sections = []
    if parsed_paper.abstract:
        key_sections.append(parsed_paper.abstract.content)
    if parsed_paper.methods:
        key_sections.append(parsed_paper.methods.content)
    if parsed_paper.introduction:
        key_sections.append(parsed_paper.introduction.content)
    
    if not key_sections:
        key_text = parsed_paper.full_text[:2000]
    else:
        key_text = "\n\n".join(key_sections)
    

    words = re.findall(r'\b[A-Za-z]{4,}\b', key_text)
    
    stop_words = {
        "this", "that", "with", "from", "have", "been", "were", "are", "was",
        "using", "based", "show", "showing", "propose", "propose", "present",
        "introduce", "develop", "design", "learn", "train", "test", "evaluate"
    }
    
    filtered_words = [w.lower() for w in words if w.lower() not in stop_words]
    
    from collections import Counter
    word_counts = Counter(filtered_words)
    
    keywords = [word for word, count in word_counts.most_common(max_keywords)]
    
    logger.info(f"Extracted {len(keywords)} keywords: {keywords[:5]}...")
    return keywords


def format_related_work_context(related_papers: List[dict]) -> str:
    """
    Format related work from arXiv search into context block.
    """
    if not related_papers:
        return "No closely related recent work found on arXiv."
    
    parts = ["# Related Recent Work (from arXiv)\n"]
    
    for i, paper in enumerate(related_papers, 1):
        title = paper.get("title", "Unknown title")
        abstract = paper.get("abstract", "No abstract available")[:500]  # Truncate
        arxiv_id = paper.get("arxiv_id", "")
        year = paper.get("year", "")
        
        parts.append(f"## {i}. {title}")
        if arxiv_id:
            parts.append(f"arXiv:{arxiv_id} ({year})")
        parts.append(f"Abstract: {abstract}\n")
    
    return "\n".join(parts)
