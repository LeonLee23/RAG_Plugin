"""Versioned package and tool input contracts."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ID = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Short = Annotated[str, Field(min_length=1, max_length=200)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(Model):
    doc_id: ID
    name: Short
    media_type: Literal['md', 'pdf', 'docx']
    doc_revision: ID
    path: str
    source_sha256: Hash
    input_sha256: Hash
    source_dependencies: dict[str, str] = Field(default_factory=dict, max_length=100)
    hashes: dict[str, Hash] = Field(max_length=210)
    index_version: Literal[1] = 1
    coverage: dict[str, int] = Field(default_factory=dict, max_length=5)


class Receipt(Model):
    fingerprint: Hash
    kind: Literal['create', 'import', 'delete']
    task_id: ID | None = None
    result: dict = Field(default_factory=dict)


class TaskSummary(Model):
    doc_id: ID
    operation_id: Short
    state: str


class Manifest(Model):
    format_version: Literal[1] = 1
    kb_id: ID
    name: Short
    description: str = Field(default='', max_length=1000)
    kb_revision: int = Field(default=0, ge=0)
    documents: dict[ID, Document] = Field(default_factory=dict, max_length=10000)
    deleted: dict[ID, ID] = Field(default_factory=dict, max_length=10000)
    operations: dict[Short, Receipt] = Field(default_factory=dict, max_length=10000)
    tasks: dict[ID, TaskSummary] = Field(default_factory=dict, max_length=10000)


class Mount(Model):
    path: str
    access: Literal['read_only', 'read_write']


class Registry(Model):
    revision: int = Field(default=0, ge=0)
    mounts: dict[ID, Mount] = Field(default_factory=dict, max_length=10000)


class Node(Model):
    node_id: str = Field(pattern=r'^n[0-9]+$')
    parent_id: str | None
    title: str = Field(max_length=1000)
    order: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    children: list[str] = Field(default_factory=list, max_length=20000)
    structure_quality: Literal['headings', 'bookmarks', 'pages', 'paragraphs']


class Location(Model):
    kind: Literal['md', 'pdf', 'docx']
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    heading_path: list[str] = Field(default_factory=list, max_length=100)
    page: int | None = Field(default=None, ge=1, le=200)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    block_id: str | None = None
    part: str | None = None
    paragraph: int | None = Field(default=None, ge=1)
    table_path: list[int] | None = None
    row: int | None = Field(default=None, ge=1)
    cell: int | None = Field(default=None, ge=1)
    image: str | None = None


class Mapping(Model):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    location: Location


class Asset(Model):
    asset_id: str = Field(pattern=r'^img-[0-9]+$')
    path: str
    sha256: Hash
    node_id: str
    offset: int = Field(ge=0)
    location: Location


class Tree(Model):
    nodes: list[Node] = Field(max_length=20000)
    assets: dict[str, Asset] = Field(default_factory=dict, max_length=100)


class Locations(Model):
    mappings: list[Mapping] = Field(max_length=100000)


class Enrichment(Model):
    item_id: str = Field(min_length=1, max_length=100)
    kind: Literal['image', 'summary']
    source_sha256: Hash
    text: str = Field(min_length=1, max_length=4000)
    status: Literal['complete', 'unreadable', 'decorative', 'failed'] = 'complete'
    visible_text: list[str] = Field(default_factory=list, max_length=50)
    uncertain: list[str] = Field(default_factory=list, max_length=50)
    quote: str | None = Field(default=None, max_length=4000)
    provider: str = Field(default='unknown', max_length=100)
    model: str = Field(default='unknown', max_length=100)


class Derived(Model):
    items: dict[str, Enrichment] = Field(default_factory=dict, max_length=20100)
    generated_at: dict[str, str] = Field(default_factory=dict, max_length=20100)
    pipeline_version: Literal[1] = 1


class Task(Model):
    task_id: ID
    operation_id: Short
    kb_id: ID
    doc_id: ID
    attempt: int = Field(default=1, ge=1)
    source_sha256: Hash
    expected_doc_revision: ID | None = None
    media_type: Literal['md', 'pdf', 'docx']
    name: Short
    state: Literal['queued', 'parsing', 'awaiting_enrichment', 'ready_to_publish', 'published', 'failed', 'cancelled']
    checkpoint: str = ''
    error: dict | None = None
    candidate: Document | None = None
    input_hashes: dict[str, Hash] = Field(default_factory=dict, max_length=101)
