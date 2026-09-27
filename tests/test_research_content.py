import tempfile
from pathlib import Path
from aurora.research.models import ResearchRequest
from aurora.research.service import ResearchService
from aurora.research.store import ResearchStore

class FakeGateway:
    def get(self,url):
        return b"Step 1: verify\n"

def test_research_persists_bounded_source_content():
    with tempfile.TemporaryDirectory() as root:
        service=ResearchService(FakeGateway(),ResearchStore(Path(root)/"research.db"))
        sources=service.retrieve(ResearchRequest("r1","objective",("https://docs.test/a",)))
        assert sources[0].content_text=="Step 1: verify\n"
        assert sources[0].content_sha256

