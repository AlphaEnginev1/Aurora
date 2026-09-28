"""Evidence-first research intelligence for Aurora."""

from .models import ClaimRecord, EvidenceRecord, EvidenceState, ResearchFinding, ResearchRequest, ResearchReport, SourceRecord
from .service import ResearchService
from .store import ResearchStore
from .discovery import DiscoveredSource, HttpSearchProvider, ResearchDiscoveryProvider
from .parser import ParsedSource, SourceParsingError, extract_claims, parse_source
from .verification import EvidenceRelation, ResearchVerifier, VerificationAssessment, VerificationError
from .knowledge import CitationEdge, CitationRelation, KnowledgeArtifact, KnowledgeError, TrustedKnowledgeBuilder

__all__ = [
    "ClaimRecord",
    "EvidenceRecord",
    "EvidenceState",
    "ResearchFinding",
    "ResearchRequest",
    "ResearchReport",
    "ResearchService",
    "ResearchStore",
    "SourceRecord",
    "DiscoveredSource",
    "HttpSearchProvider",
    "ResearchDiscoveryProvider",
    "ParsedSource",
    "SourceParsingError",
    "extract_claims",
    "parse_source",
    "EvidenceRelation",
    "ResearchVerifier",
    "VerificationAssessment",
    "VerificationError",
    "CitationEdge",
    "CitationRelation",
    "KnowledgeArtifact",
    "KnowledgeError",
    "TrustedKnowledgeBuilder",
]
