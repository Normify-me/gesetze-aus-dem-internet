import itertools
import xml.etree.ElementTree as ET

import declxml as xml
from declxml import _PrimitiveValue, _string_parser, _hooks_apply_after_parse
from lxml import etree

from .utils import chunk_string


class _XmlPreservingStringValue(_PrimitiveValue):
    """
    Hack to get at the element so we can serialize its content & children
    (instead of just getting the element text).
    Scaffolding copied from https://github.com/gatkin/declxml/blob/8cc2ff2fa813aa9d4c27d1964fe7d865029a1298/declxml.py
    """
    def parse_at_element(
        self,
        element,  # type: ET.Element
        state  # type: _ProcessorState
    ):
        # type: (...) -> Any
        """Parse the primitive value at the XML element."""
        if self._attribute:
            parsed_value = self._parse_attribute(element, self._attribute, state)
        else:
            serialised_element_content = "".join(
                itertools.chain([element.text or ""], (ET.tostring(child, encoding="unicode") for child in element))
            )
            parsed_value = self._parser_func(serialised_element_content, state)

        return _hooks_apply_after_parse(self._hooks, state, parsed_value)


def node_as_string(
    element_name,  # type: Text
    attribute=None,  # type: Optional[Text]
    required=True,  # type: bool
    alias=None,  # type: Optional[Text]
    default='',  # type: Optional[Text]
    omit_empty=False,  # type: bool
    strip_whitespace=True,  # type: bool
    hooks=None  # type: Optional[Hooks]
):
    # type: (...) -> Processor
    """
    Custom processor for extractingXml nodes as strings. Used because some
    fields contain embedded tags.
    :param strip_whitespace: Indicates whether leading and trailing whitespace should be stripped
        from parsed string values.
    """

    value_parser = _string_parser(strip_whitespace)
    return _XmlPreservingStringValue(
        element_name,
        value_parser,
        attribute,
        required,
        alias,
        default,
        omit_empty,
        hooks
    )


EMPTY_CONTENT_PATTERNS = ["<P/>", "<P />", "<P>-</P>"]
def _content_string_hooks_after_parse(_, text):
    if text == '' or any(text == pat for pat in EMPTY_CONTENT_PATTERNS):
        return None
    return text

content_string_hooks = xml.Hooks(after_parse=_content_string_hooks_after_parse)

header_norm_processor = xml.dictionary("norm", [
    xml.array(xml.string("metadaten/jurabk", alias="jurabk")),
    xml.array(xml.string("metadaten/amtabk", alias="amtabk", required=False)),
    xml.string("metadaten/ausfertigung-datum", alias="first_published"),
    xml.string(".", attribute="doknr"),
    xml.string(".", attribute="builddate", alias="source_timestamp"),
    node_as_string("metadaten/langue", alias="title_long"),
    node_as_string("metadaten/kurzue", alias="title_short", required=False, default=None),
    xml.dictionary("textdaten/text", [
        node_as_string("Content", required=False, hooks=content_string_hooks),
        node_as_string("TOC", required=False, default=None),
        node_as_string("Footnotes", required=False, default=None),
    ], required=False, alias="text"),
    xml.array(xml.dictionary("metadaten/fundstelle", [
        xml.string("periodikum", alias="periodical"),
        xml.string("zitstelle", alias="reference")
    ], required=False), alias="publication_info"),
    xml.array(xml.dictionary("metadaten/standangabe", [
        xml.string("standtyp", alias="category"),
        node_as_string("standkommentar", alias="comment")
    ], required=False), alias="status_info"),
    node_as_string("textdaten/fussnoten/Content", required=False, default=None, alias="notes_documentary_footnotes", hooks=content_string_hooks),
])


def _text(elements, multi=False):
    def _element_text_with_tags(element):
        """Preserve XML tags in the returned text string."""
        return "".join(
            itertools.chain([element.text or ""], (etree.tostring(child, encoding="unicode") for child in element))
        ).strip()

    if elements is None or len(elements) == 0:
        return None

    values = [_element_text_with_tags(el) for el in elements]

    if multi:
        return values

    assert len(values) == 1, f"Multiple values found but not requested: {values}"
    return values[0].strip() or None


def _parse_section_info(norm):
    if not norm.xpath("metadaten/gliederungseinheit"):
        return None

    return {
        "code": _text(norm.xpath("metadaten/gliederungseinheit/gliederungskennzahl")),
        "name": _text(norm.xpath("metadaten/gliederungseinheit/gliederungsbez")),
        "title": _text(norm.xpath("metadaten/gliederungseinheit/gliederungstitel"))
    }


def _parse_text(norm):
    elements = norm.xpath("textdaten/text")

    if not elements:
        return {}

    assert len(elements) == 1, 'Found multiple elements matching "textdaten/text"'
    text = elements[0]

    text_format = text.get("format")
    if text_format == "decorated":
        assert _text(text) is None, "Found text[@format=decorated] with unexpected text content."
        return {}

    assert text_format == "XML", f'Unknown text format {text["format"]}'

    content = _parse_text_content(text.xpath("Content"))
    toc = _text(text.xpath("TOC"))
    assert not (content and toc), "Found norm with both TOC and Content."

    data = {"body": content or toc, "footnotes": _text(text.xpath("Footnotes"))}

    return data


def _parse_text_content(content):
    text_content = _text(content)
    if not text_content or any(text_content.strip() == p for p in EMPTY_CONTENT_PATTERNS):
        return None
    return text_content


def _parse_documentary_footnotes(norm):
    return _parse_text_content(norm.xpath("textdaten/fussnoten/Content"))


def load_norms_from_file(file_or_filepath):
    if hasattr(file_or_filepath, "read"):
        doc = etree.parse(file_or_filepath)
    else:
        with open(file_or_filepath) as f:
            doc = etree.parse(f)

    return doc.xpath("/dokumente/norm")


def extract_law_attrs(header_norm):
    law_dict = xml.parse_from_string(header_norm_processor, etree.tostring(header_norm, encoding="unicode"))

    # post-process text
    law_dict["notes_body"] = law_dict["text"].get("Content") or law_dict["text"].get("TOC")
    law_dict["notes_footnotes"] = law_dict["text"].get("Footnotes")
    del law_dict["text"]

    # post-process abbreviations
    primary, *rest = list(dict.fromkeys(law_dict["amtabk"] + law_dict["jurabk"]))
    law_dict["abbreviation"] = primary
    law_dict["extra_abbreviations"] = rest
    del law_dict["amtabk"]
    del law_dict["jurabk"]

    return law_dict


def extract_contents(body_norms):
    def _extract_common_attrs(norm):
        text = _parse_text(norm)
        return {
            "doknr": norm.get("doknr"),
            "body": text.get("body"),
            "footnotes": text.get("footnotes"),
            "documentary_footnotes": _parse_documentary_footnotes(norm)
        }

    def _set_item_type(item, norm):
        if "NE" in item["doknr"]:
            item["item_type"] = "article"
        elif "NG" in item["doknr"]:
            if item["body"]:
                item["item_type"] = "heading_article"
            else:
                item["item_type"] = "heading"
        else:
            raise Exception(f"Unknown norm structure encountered: {etree.tostring(norm)}")

    def _set_name_and_title(item, norm):
        section_info = _parse_section_info(norm)

        if "NE" in item["doknr"]:
            item.update({
                "name": _text(norm.xpath("metadaten/enbez")),
                "title": _text(norm.xpath("metadaten/titel"))
            })
        elif "NG" in item["doknr"]:
            item.update({
                "name": section_info["name"],
                "title": section_info["title"]
            })
        else:
            raise Exception(f"Unknown norm structure encountered: {etree.tostring(norm)}")

    def _find_parent(sections_by_code, code):
        """
        Search by iteratively removing 3 digits from the end of the code to find a
        match among already-added sections.
        """
        chunks = chunk_string(code, 3)
        for i in reversed(range(len(chunks) + 1)):
            substring = "".join(chunks[:i])
            if sections_by_code.get(substring):
                return sections_by_code[substring]
        return None

    def _set_parent(item, norm, parser_state):
        section_info = _parse_section_info(norm)
        code = section_info and section_info["code"]

        if "NE" in item["doknr"]:
            if code:
                item["parent"] = _find_parent(parser_state["sections_by_code"], code)
            else:
                item["parent"] = parser_state["current_parent"]

        elif "NG" in item["doknr"]:
            item["parent"] = _find_parent(parser_state["sections_by_code"], code)
            parser_state["sections_by_code"][code] = parser_state["current_parent"] = item

        if item["parent"]:
            parser_state["items_with_children"].add(item["parent"]["doknr"])

    content_items = []

    parser_state = {
        "current_parent": None,
        "sections_by_code": {"": None},
        "items_with_children": set(),
    }

    for norm in body_norms:
        item = _extract_common_attrs(norm)
        _set_item_type(item, norm)
        _set_name_and_title(item, norm)
        _set_parent(item, norm, parser_state)
        content_items.append(item)

    # Convert empty heading articles to articles
    for item in content_items:
        if item["item_type"] == "heading_article" and item["doknr"] not in parser_state["items_with_children"]:
            item["item_type"] = "article"

    return content_items


def parse_law(file_or_filepath):
    header_norm, *body_norms = load_norms_from_file(file_or_filepath)

    law_attrs = extract_law_attrs(header_norm)
    law_attrs["contents"] = extract_contents(body_norms)

    return law_attrs
