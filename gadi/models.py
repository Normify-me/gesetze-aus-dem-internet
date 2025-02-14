import re

from sqlalchemy import Column, ForeignKey, Index, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


def slugify(string):
    string = string.lower()
    # Transcribe umlauts etc.
    for orig, repl in [("ß", "ss"), ("ä", "ae"), ("ö", "oe"), ("ü", "ue")]:
        string = string.replace(orig, repl)
    # Replace other characters with underscore
    string = re.sub("[^a-z0-9]", "_", string)
    # Collapse consecutive underscores
    string = re.sub("_+", "_", string)
    return string


class Law(Base):
    __tablename__ = "laws"

    id = Column(Integer, primary_key=True)
    doknr = Column(String, nullable=False, unique=True)
    slug = Column(String, nullable=False, index=True)
    gii_slug = Column(String, nullable=False, index=True)
    abbreviation = Column(String, nullable=False)
    extra_abbreviations = Column(postgresql.ARRAY(String), nullable=False)
    first_published = Column(String, nullable=False)
    source_timestamp = Column(String, nullable=False)
    title_long = Column(String, nullable=False)
    title_short = Column(String)
    publication_info = Column(postgresql.JSONB, nullable=False)
    status_info = Column(postgresql.JSONB, nullable=False)
    notes_body = Column(String)
    notes_footnotes = Column(String)
    notes_documentary_footnotes = Column(String)

    attachments = relationship(
        "Attachment",
        back_populates="law",
        order_by="Attachment.name",
        cascade="all, delete, delete-orphan",
        passive_deletes=True
    )

    contents = relationship(
        "ContentItem",
        back_populates="law",
        order_by="ContentItem.order",
        cascade="all, delete, delete-orphan",
        passive_deletes=True
    )

    @staticmethod
    def from_dict(law_dict, gii_slug):
        law = Law(
            slug=slugify(law_dict["abbreviation"]),
            gii_slug=gii_slug,
            **{k: v for k, v in law_dict.items() if k not in ["contents", "attachments"]}
        )

        content_item_dicts = law_dict["contents"]
        content_items_by_doknr = {}
        for idx, content_item_dict in enumerate(content_item_dicts):
            content_item = ContentItem.from_dict(content_item_dict, idx, content_items_by_doknr)
            content_items_by_doknr[content_item.doknr] = content_item
            law.contents.append(content_item)

        attachments_dict = law_dict["attachments"]
        for name, data_uri in attachments_dict.items():
            law.attachments.append(Attachment(name=name, data_uri=data_uri))

        return law


class ContentItem(Base):
    __tablename__ = "content_items"

    id = Column(Integer, primary_key=True)
    doknr = Column(String, nullable=False, unique=True)
    item_type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    title = Column(String)
    body = Column(String)
    footnotes = Column(String)
    documentary_footnotes = Column(String)
    law_id = Column(Integer, ForeignKey("laws.id", ondelete="CASCADE"), index=True)
    parent_id = Column(Integer, ForeignKey("content_items.id"))
    order = Column(Integer, nullable=False)

    law = relationship("Law", back_populates="contents")
    parent = relationship("ContentItem", remote_side=[id], uselist=False)

    @staticmethod
    def from_dict(content_item_dict, order, content_items_by_doknr):
        parent_dict = content_item_dict["parent"]
        parent = parent_dict and content_items_by_doknr[parent_dict["doknr"]]

        content_item_attrs = {k: v for k, v in content_item_dict.items() if k != "parent"}
        content_item = ContentItem(parent=parent, order=order, **content_item_attrs)
        return content_item


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    data_uri = Column(String, nullable=False)
    law_id = Column(Integer, ForeignKey("laws.id", ondelete="CASCADE"), index=True)

    law = relationship("Law", back_populates="attachments")
